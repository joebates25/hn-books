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
    import json

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
    assert "book/" in (tmp_path / "site" / "recent.html").read_text()  # Jan 1 is within 30 days of Jan 5
