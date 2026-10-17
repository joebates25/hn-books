import json
from datetime import datetime, timezone
from pathlib import Path

from hn_books.db import Store
from hn_books.extract import extract_candidates
from hn_books.ingest import Ingestor
from hn_books.models import Comment, ResolvedBook
from hn_books.resolve import Resolution, Resolver


class FakeResolver(Resolver):
    def __init__(self, store: Store):
        self.store = store

    def resolve(self, candidate):  # type: ignore[override]
        isbn = candidate.isbn13 or candidate.asin or candidate.extra_id or candidate.normalized_url
        title = candidate.title_hint or isbn
        author = "Martin Kleppmann" if candidate.isbn13 else None
        book = ResolvedBook(title=title, author=author, isbn13=candidate.isbn13, source=candidate.source)
        identity = f"isbn:{candidate.isbn13}" if candidate.isbn13 else f"{candidate.source}:{isbn}"
        return Resolution(book=book, identity_key=identity, confidence=1.0)

    def close(self) -> None:
        return None


def test_ingest_and_rank(tmp_path: Path):
    store = Store(tmp_path / "test.sqlite")
    ingestor = Ingestor(store, FakeResolver(store))
    comments = [
        Comment(
            id=1,
            author="alice",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            text='<a href="https://www.amazon.com/Designing-Data-Intensive-Applications/dp/1449373321">ddia</a>',
            story_id=10,
        ),
        Comment(
            id=2,
            author="bob",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            text='<a href="https://www.amazon.com/dp/1449373321">same book</a>',
            story_id=11,
        ),
        Comment(
            id=3,
            author="carol",
            created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            text='<a href="https://www.manning.com/books/just-use-postgres">pg</a>',
            story_id=12,
        ),
        Comment(
            id=4,
            author="dave",
            created_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
            text="no book here, just a thought",
            story_id=13,
        ),
    ]
    mentions = sum(ingestor.ingest_comment(c) for c in comments)
    store.commit()
    assert mentions == 3
    stats = store.stats()
    assert stats["comments"] == 3  # comment 4 has no candidate, so it is not stored
    assert stats["books"] == 2
    assert stats["mentions"] == 3

    ranked = store.ranked_books()
    assert ranked[0].isbn13 == "9781449373320"
    assert ranked[0].mentions == 2
    assert ranked[0].unique_users == 2
    assert ranked[0].unique_threads == 2
    assert ranked[0].score > ranked[1].score

    book = store.get_book("9781449373320")
    assert book is not None
    rows = store.mentions_for_book(int(book["id"]))
    assert {r.author for r in rows} == {"alice", "bob"}


def test_extract_used_by_pipeline():
    # sanity: the comment HTML we ingest is the same shape HN serves
    text = 'I recommend <a href="https://www.amazon.com/dp/1449373321">this</a>'
    assert extract_candidates(text)[0].isbn13 == "9781449373320"


def test_build_site_writes_book_pages(tmp_path: Path):
    from hn_books.site import build_site

    store = Store(tmp_path / "test.sqlite")
    ingestor = Ingestor(store, FakeResolver(store))
    ingestor.ingest_comment(
        Comment(
            id=1,
            author="alice",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            text='Read <a href="https://www.amazon.com/dp/1449373321">this &lt;book&gt;</a>',
            story_id=10,
            story_title="Ask HN: Best <b>books</b>?",
        )
    )
    ingestor.ingest_comment(
        Comment(
            id=2,
            author="bob",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            text='<a href="https://www.manning.com/books/just-use-postgres">pg</a>',
            story_id=11,
        )
    )
    store.commit()
    result = build_site(store, tmp_path / "site", now=datetime(2026, 1, 5, tzinfo=timezone.utc))
    assert result.books == 1  # the unresolved Manning slug is not published
    book_id = store.ranked_books(resolved_only=True)[0].id
    page = (tmp_path / "site" / "book" / f"{book_id}.html").read_text()
    assert "news.ycombinator.com/item?id=1" in page
    assert "Ask HN: Best &lt;b&gt;books&lt;/b&gt;?" in page  # story titles are escaped
    assert "<b>books</b>" not in page
    data = json.loads((tmp_path / "site" / "books.json").read_text())
    assert data["books"][0]["url"] == f"book/{book_id}.html"
    assert "index.html?range=month" in (tmp_path / "site" / "recent.html").read_text()  # old links redirect
    # Store links: the posted Amazon link, cleaned up, plus a Goodreads link built from the ISBN.
    assert '<a href="https://www.goodreads.com/book/isbn/9781449373320">Goodreads</a>' in page
    assert '<a href="https://www.amazon.com/dp/1449373321">Amazon</a>' in page
    assert "First link posted on HN" not in page  # it was the Amazon link, already shown


def test_store_links_prefer_oldest_posted_link_and_clean_it():
    from hn_books.db import MentionRow, RankedBook
    from hn_books.site import _store_links

    def mention(url: str, method: str, created_at: int) -> MentionRow:
        return MentionRow(1, "alice", created_at, 10, None, url, method, "")

    book = RankedBook(1, "Fair Play", "Eve Rodsky", None, None, 3, 3, 3, 1.0)
    mentions = [  # newest first, as mentions_for_book returns them
        mention("https://www.goodreads.com/book/show/999-other-edition", "goodreads_url", 3),
        mention("https://goodreads.com/en/book/show/44071899-fair-pl...", "goodreads_url", 2),
        mention("https://www.amazon.co.uk/Fair-Play/dp/B07FKMDWKY/ref=sr_1?tag=someone-21", "amazon_url", 1),
    ]
    assert _store_links(book, None, mentions) == [
        ("Goodreads", "https://www.goodreads.com/book/show/44071899"),
        ("Amazon", "https://www.amazon.co.uk/dp/B07FKMDWKY"),
    ]
    # A short link HN cut off can't be repaired, and with no ISBN there is nothing to fall back on.
    assert _store_links(book, None, [mention("https://a.co/d/iR7s...", "amazon_short_url", 1)]) == []


def test_build_site_embeds_mention_timeline(tmp_path: Path):
    import re

    from hn_books.site import build_site

    store = Store(tmp_path / "test.sqlite")
    ingestor = Ingestor(store, FakeResolver(store))
    for i, (author, story_id) in enumerate([("alice", 10), ("bob", None)], start=1):
        ingestor.ingest_comment(
            Comment(
                id=i,
                author=author,
                created_at=datetime(2026, 1, i, tzinfo=timezone.utc),
                text='<a href="https://www.amazon.com/dp/1449373321">ddia</a>',
                story_id=story_id,
            )
        )
    store.commit()
    build_site(store, tmp_path / "site", now=datetime(2026, 1, 5, tzinfo=timezone.utc))

    index = (tmp_path / "site" / "index.html").read_text()
    book_id = store.ranked_books(resolved_only=True)[0].id
    timeline = json.loads(re.search(r"<script type=application/json id=timeline>(.*?)</script>", index).group(1))
    jan1, jan2 = (int(datetime(2026, 1, d, tzinfo=timezone.utc).timestamp()) for d in (1, 2))
    # Newest first; a comment with no story counts as its own thread, like the SQL ranking.
    assert timeline == {str(book_id): [[jan2, "bob", 2], [jan1, "alice", 10]]}
    assert "<button type=button value=week>Last week</button>" in index
    assert "recent.html" not in index  # the Recent tab is gone


def test_build_site_tags_books_by_link_source(tmp_path: Path):
    from hn_books.site import build_site

    store = Store(tmp_path / "test.sqlite")
    ingestor = Ingestor(store, FakeResolver(store))
    links = [
        "https://www.amazon.com/dp/1449373321",
        "https://www.goodreads.com/book/isbn/9781449373320",  # same book, second site
        "https://www.amazon.com/dp/0134685997",
    ]
    for i, url in enumerate(links, start=1):
        ingestor.ingest_comment(
            Comment(
                id=i,
                author=f"user{i}",
                created_at=datetime(2026, 1, i, tzinfo=timezone.utc),
                text=f'<a href="{url}">book</a>',
                story_id=10 + i,
            )
        )
    store.commit()
    build_site(store, tmp_path / "site", top_limit=1, now=datetime(2026, 1, 5, tzinfo=timezone.utc))

    ddia, other = store.ranked_books(resolved_only=True)
    assert ddia.detection_methods == ("amazon_url", "goodreads_isbn_url")
    index = (tmp_path / "site" / "index.html").read_text()
    assert f'<li data-id={ddia.id} data-src="amazon goodreads"><a class=title href="book/{ddia.id}.html">' in index
    assert "via Amazon and Goodreads" in index
    # Past top_limit the book is still in the page, hidden, so the Amazon filter can show it.
    assert f'<li data-id={other.id} data-src="amazon" hidden><a class=title href="book/{other.id}.html">' in index
    assert "<button type=button value=goodreads>Goodreads <span class=count>1</span></button>" in index
    assert "<button type=button value=amazon>Amazon <span class=count>2</span></button>" in index
    assert "<script src=filter.js defer></script>" in index
    assert (tmp_path / "site" / "filter.js").exists()
    data = json.loads((tmp_path / "site" / "books.json").read_text())
    assert data["books"][0]["linked_from"] == ["amazon", "goodreads"]
