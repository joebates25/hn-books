"""Static site builder: SQLite → plain HTML + JSON for GitHub Pages."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from hn_books.db import MentionRow, RankedBook, Store

SITE_NAME = "HN Books"
HN_ITEM = "https://news.ycombinator.com/item?id={}"


@dataclass(frozen=True)
class BuildResult:
    out_dir: Path
    books: int
    pages: int


def build_site(
    store: Store,
    out_dir: str | Path,
    top_limit: int = 100,
    recent_days: int = 30,
    now: datetime | None = None,
) -> BuildResult:
    """Write the whole site into out_dir. Links are relative, so it works under a /repo/ subpath."""
    out = Path(out_dir)
    shutil.rmtree(out / "book", ignore_errors=True)  # drop pages for books that are no longer published
    (out / "book").mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(tz=timezone.utc)
    updated = now.strftime("%Y-%m-%d %H:%M UTC")

    all_books = store.ranked_books(limit=None, resolved_only=True)
    since = int((now - timedelta(days=recent_days)).timestamp())
    recent = store.ranked_books(since_unix=since, limit=top_limit, resolved_only=True)

    pages = 0
    _write(out / "index.html", _page(
        title=f"{SITE_NAME} — most-mentioned books on Hacker News",
        heading="Most-mentioned books on Hacker News",
        intro="Ranked by how many different people and threads linked to each book.",
        body=_ranked_list(all_books[:top_limit]),
        active="index",
        updated=updated,
    ))
    _write(out / "recent.html", _page(
        title=f"{SITE_NAME} — last {recent_days} days",
        heading=f"Popular in the last {recent_days} days",
        intro="Only mentions from comments posted in this window count.",
        body=_ranked_list(recent) if recent else "<p class=empty>No mentions in this window yet.</p>",
        active="recent",
        updated=updated,
    ))
    _write(out / "all.html", _page(
        title=f"{SITE_NAME} — all books",
        heading="All books",
        intro=f"{len(all_books)} books, A–Z. Type to filter.",
        body=_all_list(all_books),
        active="all",
        updated=updated,
    ))
    pages += 3

    json_rows = []
    for rank, book in enumerate(all_books, start=1):
        row = store.get_book(str(book.id))
        mentions = store.mentions_for_book(book.id, limit=None, snippet_width=400)
        _write(out / "book" / f"{book.id}.html", _book_page(book, row, mentions, rank, updated))
        pages += 1
        json_rows.append({
            "id": book.id,
            "rank": rank,
            "title": book.title,
            "author": book.author,
            "isbn13": book.isbn13,
            "mentions": book.mentions,
            "unique_users": book.unique_users,
            "unique_threads": book.unique_threads,
            "score": round(book.score, 3),
            "url": f"book/{book.id}.html",
        })

    _write(out / "books.json", json.dumps(
        {"updated": now.isoformat(), "books": json_rows}, indent=1, ensure_ascii=False
    ))
    _write(out / "style.css", STYLE)
    _write(out / ".nojekyll", "")
    return BuildResult(out_dir=out, books=len(all_books), pages=pages)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _byline(book: RankedBook) -> str:
    return f' <span class=author>by {escape(book.author)}</span>' if book.author else ""


def _counts(book: RankedBook) -> str:
    people = "person" if book.unique_users == 1 else "people"
    threads = "thread" if book.unique_threads == 1 else "threads"
    return f"{book.unique_users} {people} · {book.unique_threads} {threads}"


def _ranked_list(books: list[RankedBook]) -> str:
    items = [
        f'<li><a class=title href="book/{b.id}.html">{escape(b.title)}</a>{_byline(b)}'
        f'<div class=meta>{_counts(b)}</div></li>'
        for b in books
    ]
    return "<ol class=books>\n" + "\n".join(items) + "\n</ol>"


def _all_list(books: list[RankedBook]) -> str:
    by_title = sorted(books, key=lambda b: b.title.casefold())
    items = [
        f'<li data-q="{escape((b.title + " " + (b.author or "") + " " + (b.isbn13 or "")).casefold())}">'
        f'<a class=title href="book/{b.id}.html">{escape(b.title)}</a>{_byline(b)}'
        f'<div class=meta>{_counts(b)}</div></li>'
        for b in by_title
    ]
    return (
        '<input id=filter type=search placeholder="Filter by title, author or ISBN" autocomplete=off>\n'
        "<ul class=books id=all>\n" + "\n".join(items) + "\n</ul>\n"
        "<p class=empty id=none hidden>No matches.</p>\n"
        "<script>\n"
        "const f=document.getElementById('filter'),rows=[...document.querySelectorAll('#all li')],"
        "none=document.getElementById('none');\n"
        "f.addEventListener('input',()=>{const q=f.value.trim().toLowerCase();let n=0;"
        "for(const r of rows){const hit=!q||r.dataset.q.includes(q);r.hidden=!hit;n+=hit}none.hidden=n>0});\n"
        "</script>"
    )


def _book_page(book: RankedBook, row, mentions: list[MentionRow], rank: int, updated: str) -> str:
    links = []
    if row is not None and row["openlibrary_work_id"]:
        links.append(f'<a href="https://openlibrary.org/works/{escape(row["openlibrary_work_id"])}">Open Library</a>')
    elif book.isbn13:
        links.append(f'<a href="https://openlibrary.org/isbn/{escape(book.isbn13)}">Open Library</a>')
    if mentions and mentions[-1].matched_text.startswith(("http://", "https://")):
        links.append(f'<a href="{escape(mentions[-1].matched_text)}">First link posted on HN</a>')

    cover = ""
    if book.isbn13:
        cover = (
            f'<img class=cover alt="" loading=lazy onerror="this.remove()" '
            f'src="https://covers.openlibrary.org/b/isbn/{escape(book.isbn13)}-M.jpg?default=false">'
        )

    facts = [f"#{rank} overall", _counts(book)]
    if book.isbn13:
        facts.append(f"ISBN {escape(book.isbn13)}")

    items = []
    for m in mentions:
        when = datetime.fromtimestamp(m.created_at, tz=timezone.utc).strftime("%Y-%m-%d")
        who = escape(m.author or "anon")
        thread = ""
        if m.story_id:
            label = escape(m.story_title) if m.story_title else "thread"
            thread = f' in <a href="{HN_ITEM.format(m.story_id)}">{label}</a>'
        items.append(
            f'<li><div class=meta><b>{who}</b> · <a href="{HN_ITEM.format(m.comment_id)}">{when}</a>{thread}</div>'
            f"<blockquote>{escape(m.snippet)}</blockquote></li>"
        )

    body = (
        f'<div class=book-head>{cover}<div>'
        f'<p class=facts>{" · ".join(facts)}</p>'
        f'<p class=links>{" · ".join(links)}</p>'
        f"</div></div>\n"
        f"<h2>What people said ({len(mentions)})</h2>\n"
        "<ul class=mentions>\n" + "\n".join(items) + "\n</ul>"
    )
    heading = escape(book.title) + (f' <span class=author>by {escape(book.author)}</span>' if book.author else "")
    return _page(
        title=f"{book.title} — {SITE_NAME}",
        heading=heading,
        heading_is_html=True,
        intro="",
        body=body,
        active="",
        updated=updated,
        root="../",
    )


def _page(
    title: str,
    heading: str,
    intro: str,
    body: str,
    active: str,
    updated: str,
    root: str = "",
    heading_is_html: bool = False,
) -> str:
    def nav(key: str, href: str, label: str) -> str:
        current = ' aria-current=page' if key == active else ""
        return f'<a href="{root}{href}"{current}>{label}</a>'

    h1 = heading if heading_is_html else escape(heading)
    intro_html = f"<p class=intro>{escape(intro)}</p>" if intro else ""
    return f"""<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<link rel=stylesheet href="{root}style.css">
</head>
<body>
<header>
<a class=brand href="{root}index.html">{SITE_NAME}</a>
<nav>{nav("index", "index.html", "Top")}{nav("recent", "recent.html", "Recent")}{nav("all", "all.html", "All books")}</nav>
</header>
<main>
<h1>{h1}</h1>
{intro_html}
{body}
</main>
<footer>Books linked in <a href="https://news.ycombinator.com/">Hacker News</a> comments. Updated {escape(updated)}.</footer>
</body>
</html>
"""


STYLE = """:root {
  --bg: #fbfaf7; --fg: #1d1d1b; --muted: #6b6a66; --line: #e4e1d8;
  --accent: #d35400; --quote: #f3f0e8;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #17171a; --fg: #ebe9e4; --muted: #9d9b95; --line: #2d2d31;
    --accent: #ff8a3d; --quote: #222226; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 18px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Verdana, sans-serif;
  letter-spacing: 0.01em; word-spacing: 0.04em; }
a { color: var(--accent); text-underline-offset: 3px; }
header { display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: baseline;
  max-width: 760px; margin: 0 auto; padding: 20px 16px; border-bottom: 1px solid var(--line); }
.brand { font-weight: 700; font-size: 20px; text-decoration: none; color: var(--fg); }
nav { display: flex; gap: 18px; }
nav a { color: var(--muted); text-decoration: none; }
nav a[aria-current] { color: var(--fg); font-weight: 600; border-bottom: 2px solid var(--accent); }
main { max-width: 760px; margin: 0 auto; padding: 8px 16px 48px; }
h1 { font-size: 28px; line-height: 1.3; margin: 24px 0 8px; }
h2 { font-size: 20px; margin: 32px 0 8px; }
.intro, .meta, .facts, footer, .empty { color: var(--muted); }
.meta { font-size: 15px; }
.author { font-weight: 400; color: var(--muted); }
.books { padding-left: 0; list-style: none; counter-reset: rank; }
ol.books li { counter-increment: rank; }
ol.books li::before { content: counter(rank) "."; display: inline-block; min-width: 2.4em; color: var(--muted); }
ol.books .meta { padding-left: 2.4em; }
.books li { padding: 10px 0; border-bottom: 1px solid var(--line); }
.books .title { font-weight: 600; }
#filter { width: 100%; font: inherit; padding: 10px 14px; margin: 8px 0 12px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--quote); color: var(--fg); }
.book-head { display: flex; gap: 20px; align-items: flex-start; }
.cover { width: 110px; border-radius: 4px; box-shadow: 0 2px 8px rgb(0 0 0 / 0.15); }
.mentions { list-style: none; padding: 0; }
.mentions li { margin: 0 0 20px; }
blockquote { margin: 6px 0 0; padding: 10px 14px; background: var(--quote);
  border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0; }
footer { max-width: 760px; margin: 0 auto; padding: 20px 16px 40px; font-size: 15px;
  border-top: 1px solid var(--line); }
"""
