"""Static site builder: SQLite → plain HTML + JSON for GitHub Pages."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from hn_books.db import MentionRow, RankedBook, Store

SITE_NAME = "HN Books"
HN_ITEM = "https://news.ycombinator.com/item?id={}"

# The site a book was linked from, keyed by the prefix of the mention's detection method.
SITE_LABELS = {
    "amazon": "Amazon",
    "goodreads": "Goodreads",
    "google": "Google Books",
    "manning": "Manning",
    "nostarch": "No Starch",
    "openlibrary": "Open Library",
    "oreilly": "O'Reilly",
    "pragprog": "PragProg",
}
# Filter buttons on the list pages. Every site not named here falls under "other".
SOURCE_FILTERS = [("goodreads", "Goodreads"), ("amazon", "Amazon"), ("other", "Other sites")]
# Date buttons on the Top page. filter.js turns each into a time window; the first is the default.
RANGES = [("week", "Last week"), ("month", "Last month"), ("all", "All time"), ("custom", "Pick dates")]

RECENT_REDIRECT = """<!doctype html>
<meta charset=utf-8>
<title>HN Books</title>
<meta http-equiv=refresh content="0; url=index.html?range=month">
<a href="index.html?range=month">The Recent page moved to Top → Last month.</a>
"""


@dataclass(frozen=True)
class BuildResult:
    out_dir: Path
    books: int
    pages: int


def build_site(
    store: Store,
    out_dir: str | Path,
    top_limit: int = 100,
    now: datetime | None = None,
) -> BuildResult:
    """Write the whole site into out_dir. Links are relative, so it works under a /repo/ subpath."""
    out = Path(out_dir)
    shutil.rmtree(out / "book", ignore_errors=True)  # drop pages for books that are no longer published
    (out / "book").mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(tz=timezone.utc)
    updated = now.strftime("%Y-%m-%d %H:%M UTC")

    all_books = store.ranked_books(limit=None, resolved_only=True)

    pages = 0
    json_rows = []
    # Each book's mentions as [unix time, commenter, thread id], so the Top page can rank any date range.
    timeline: dict[int, list] = {}
    for rank, book in enumerate(all_books, start=1):
        row = store.get_book(str(book.id))
        mentions = store.mentions_for_book(book.id, limit=None, snippet_width=400)
        _write(out / "book" / f"{book.id}.html", _book_page(book, row, mentions, rank, updated))
        pages += 1
        timeline[book.id] = [[m.created_at, m.author, m.story_id or m.comment_id] for m in mentions]
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
            "linked_from": _sites(book),
            "url": f"book/{book.id}.html",
        })

    _write(out / "index.html", _page(
        title=f"{SITE_NAME} — most-mentioned books on Hacker News",
        heading="Most-mentioned books on Hacker News",
        intro="Ranked by how many different people and threads linked to each book.",
        body=_ranked_list(all_books, top_limit, _range_filter(timeline)),
        active="index",
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
    # The Recent page is now the Top page's "Last month" view. Keep old links working.
    _write(out / "recent.html", RECENT_REDIRECT)
    pages += 3

    _write(out / "books.json", json.dumps(
        {"updated": now.isoformat(), "books": json_rows}, indent=1, ensure_ascii=False
    ))
    _write(out / "style.css", STYLE)
    _write(out / "filter.js", FILTER_JS)
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


def _sites(book: RankedBook) -> list[str]:
    return sorted({method.split("_")[0] for method in book.detection_methods})


def _source_groups(book: RankedBook) -> list[str]:
    named = {key for key, _ in SOURCE_FILTERS}
    return sorted({site if site in named else "other" for site in _sites(book)})


def _list_item(book: RankedBook, attrs: str = "") -> str:
    via = " and ".join(SITE_LABELS.get(site, "another site") for site in _sites(book))
    return (
        f'<li data-id={book.id} data-src="{" ".join(_source_groups(book))}"{attrs}>'
        f'<a class=title href="book/{book.id}.html">{escape(book.title)}</a>{_byline(book)}'
        f'<div class=meta><span class=counts>{_counts(book)}</span> · via {escape(via)}</div></li>'
    )


def _source_filter(books: list[RankedBook]) -> str:
    """Buttons that narrow a list to books linked from one site. Hidden until filter.js runs."""
    counts = Counter(group for b in books for group in _source_groups(b))
    buttons = ["<button type=button value=all>Any site</button>"] + [
        f"<button type=button value={key}>{label} <span class=count>{counts[key]}</span></button>"
        for key, label in SOURCE_FILTERS
    ]
    return (
        '<div class=chips data-filter=source role=group aria-label="Linked from" hidden>'
        "<span class=label>Linked from</span>" + "".join(buttons) + "</div>"
    )


def _range_filter(timeline: dict[int, list]) -> str:
    """Date buttons for the Top page, plus the mention data filter.js ranks with. Hidden until filter.js runs."""
    buttons = "".join(f"<button type=button value={key}>{label}</button>" for key, label in RANGES)
    data = json.dumps(timeline, separators=(",", ":")).replace("</", "<\\/")
    return (
        '<div class=chips data-filter=range role=group aria-label="When" hidden>'
        f"<span class=label>When</span>{buttons}</div>\n"
        "<div class=chips id=dates hidden><span class=label></span>"
        "<label>From <input type=date id=start></label><label>To <input type=date id=end></label></div>\n"
        f"<script type=application/json id=timeline>{data}</script>\n"
    )


def _filtered(books: list[RankedBook], list_html: str, range_filter: str = "") -> str:
    summary = "<p class=summary id=summary aria-live=polite></p>\n" if range_filter else ""
    return (
        range_filter + _source_filter(books) + "\n" + summary + list_html + "\n"
        "<p class=empty id=none hidden>No books match.</p>\n"
        "<script src=filter.js defer></script>"
    )


def _ranked_list(books: list[RankedBook], limit: int, range_filter: str = "") -> str:
    """Every book is in the page so a filter can still show `limit` of them. Past the limit they start hidden."""
    items = [_list_item(b, " hidden" if rank > limit else "") for rank, b in enumerate(books, start=1)]
    return _filtered(books, f"<ol class=books data-limit={limit}>\n" + "\n".join(items) + "\n</ol>", range_filter)


def _all_list(books: list[RankedBook]) -> str:
    by_title = sorted(books, key=lambda b: b.title.casefold())
    items = [
        _list_item(b, f' data-q="{escape((b.title + " " + (b.author or "") + " " + (b.isbn13 or "")).casefold())}"')
        for b in by_title
    ]
    return (
        '<input id=filter type=search placeholder="Filter by title, author or ISBN" autocomplete=off>\n'
        + _filtered(books, "<ul class=books>\n" + "\n".join(items) + "\n</ul>")
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
<nav>{nav("index", "index.html", "Top")}{nav("all", "all.html", "All books")}</nav>
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
  --accent: #d35400; --quote: #f3f0e8; color-scheme: light dark;
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
[hidden] { display: none !important; }
.chips { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 10px 0; font-size: 15px; }
.chips .label { color: var(--muted); min-width: 6.5em; }
.chips button, .chips input { font: inherit; padding: 5px 14px; color: var(--fg);
  border: 1px solid var(--line); border-radius: 999px; background: var(--quote); }
.chips button { cursor: pointer; }
.chips button[aria-pressed=true] { background: var(--fg); border-color: var(--fg); color: var(--bg); font-weight: 600; }
.chips .count { color: var(--muted); }
.chips [aria-pressed=true] .count { color: inherit; }
.chips label { display: flex; align-items: center; gap: 8px; margin-right: 8px; }
.summary { font-weight: 600; margin: 18px 0 0; }
.book-head { display: flex; gap: 20px; align-items: flex-start; }
.cover { width: 110px; border-radius: 4px; box-shadow: 0 2px 8px rgb(0 0 0 / 0.15); }
.mentions { list-style: none; padding: 0; }
.mentions li { margin: 0 0 20px; }
blockquote { margin: 6px 0 0; padding: 10px 14px; background: var(--quote);
  border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0; }
footer { max-width: 760px; margin: 0 auto; padding: 20px 16px 40px; font-size: 15px;
  border-top: 1px solid var(--line); }
"""

# Shared by the list pages: the source buttons, plus the text box on the All books page and the
# date buttons on the Top page. Choices ride along in the URL (?site=, ?range=, ?start=, ?end=),
# so they survive a reload and moving between pages.
FILTER_JS = """(() => {
  const $ = (sel) => document.querySelector(sel);
  const sourceButtons = [...document.querySelectorAll('[data-filter=source] button')];
  const rangeButtons = [...document.querySelectorAll('[data-filter=range] button')];
  const list = $('.books'), none = $('#none'), summary = $('#summary'), text = $('#filter');
  const start = $('#start'), end = $('#end');
  const timeline = $('#timeline') ? JSON.parse($('#timeline').textContent) : null;
  const rows = [...list.children]; // all-time rank order, which also breaks ties
  const limit = Number(list.dataset.limit) || Infinity;
  const DAY = 86400, now = Date.now() / 1000;
  const times = timeline ? Object.values(timeline).flat().map((m) => m[0]) : [];
  const first = Math.min(...times), newest = Math.max(...times);

  const params = new URLSearchParams(location.search);
  const pick = (buttons, value, fallback) => (buttons.some((b) => b.value === value) ? value : fallback);
  let source = pick(sourceButtons, params.get('site'), 'all');
  let range = pick(rangeButtons, params.get('range'), 'week');
  if (start) { start.value = params.get('start') || ''; end.value = params.get('end') || ''; }

  const ymd = (unix) => {
    const d = new Date(unix * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  const fromYmd = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d) / 1000; };
  const nice = (unix) => new Date(unix * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  // [from, to) in unix seconds. Custom dates are local days and the end day counts in full.
  function window_() {
    if (range === 'week') return [now - 7 * DAY, Infinity];
    if (range === 'month') return [now - 30 * DAY, Infinity];
    if (range === 'custom') {
      return [start.value ? fromYmd(start.value) : -Infinity, end.value ? fromYmd(end.value) + DAY : Infinity];
    }
    return [-Infinity, Infinity];
  }

  function describe(from, to) {
    if (range === 'week') return 'in the last 7 days';
    if (range === 'month') return 'in the last 30 days';
    if (range === 'all') return times.length ? `since ${nice(first)}` : 'so far';
    if (from > -Infinity && to < Infinity) return `between ${nice(from)} and ${nice(to - DAY)}`;
    if (from > -Infinity) return `since ${nice(from)}`;
    if (to < Infinity) return `up to ${nice(to - DAY)}`;
    return 'so far';
  }

  // Same score as the Python ranking: people + 2 × threads + log(mentions + 1).
  function ranked(from, to) {
    const out = [];
    rows.forEach((li, i) => {
      const hits = timeline[li.dataset.id].filter(([t]) => t >= from && t < to);
      if (!hits.length) return;
      const people = new Set(hits.map((m) => m[1]).filter(Boolean)).size;
      const threads = new Set(hits.map((m) => m[2])).size;
      out.push({ li, i, people, threads, mentions: hits.length, score: people + 2 * threads + Math.log(hits.length + 1) });
    });
    return out.sort((a, b) => b.score - a.score || b.mentions - a.mentions || b.threads - a.threads || a.i - b.i);
  }

  function withState(href) {
    const url = new URL(href, location.href), p = url.searchParams, here = new URLSearchParams(location.search);
    for (const k of ['site', 'range', 'start', 'end']) p.delete(k);
    if (source !== 'all') p.set('site', source);
    if (!timeline) { // no date buttons on this page: pass the Top page's choice through untouched
      for (const k of ['range', 'start', 'end']) if (here.has(k)) p.set(k, here.get(k));
    } else if (range !== 'week') {
      p.set('range', range);
      if (range === 'custom' && start.value) p.set('start', start.value);
      if (range === 'custom' && end.value) p.set('end', end.value);
    }
    return url.href;
  }

  function apply() {
    const [from, to] = window_();
    const q = text ? text.value.trim().toLowerCase() : '';
    const matches = timeline ? ranked(from, to) : rows.map((li) => ({ li }));
    const counts = {};
    let shown = 0;
    for (const li of rows) li.hidden = true;
    for (const m of matches) {
      const groups = m.li.dataset.src.split(' ');
      for (const g of groups) counts[g] = (counts[g] || 0) + 1;
      if (timeline) {
        m.li.querySelector('.counts').textContent = `${plural(m.people, 'person', 'people')} · ${plural(m.threads, 'thread', 'threads')}`;
        list.append(m.li); // moves it into this window's rank order
      }
      const hit = (source === 'all' || groups.includes(source)) && (!q || m.li.dataset.q.includes(q)) && shown < limit;
      m.li.hidden = !hit;
      shown += hit;
    }

    for (const b of sourceButtons) {
      b.setAttribute('aria-pressed', b.value === source);
      const count = b.querySelector('.count');
      if (count) count.textContent = counts[b.value] || 0;
    }
    for (const b of rangeButtons) b.setAttribute('aria-pressed', b.value === range);
    if (start) $('#dates').hidden = range !== 'custom';

    if (summary) {
      const total = source === 'all' ? matches.length : counts[source] || 0;
      const label = sourceButtons.find((b) => b.value === source).firstChild.textContent.trim();
      const via = source === 'all' ? '' : ` from ${source === 'other' ? 'other sites' : label}`;
      const when = describe(from, to);
      summary.textContent = total
        ? `${plural(total, 'book', 'books')} linked${via} ${when}.` + (total > limit ? ` Showing the top ${limit}.` : '')
        : `No books linked${via} ${when}.` + (times.length && newest < from ? ` The newest link we have is from ${nice(newest)}.` : '');
    }
    none.hidden = shown > 0 || Boolean(summary);

    for (const a of document.querySelectorAll('header a')) a.href = withState(a.href);
    try { history.replaceState(null, '', withState(location.href)); } catch {}
  }

  for (const b of sourceButtons) b.addEventListener('click', () => { source = b.value; apply(); });
  for (const b of rangeButtons) {
    b.addEventListener('click', () => {
      if (b.value === 'custom' && range !== 'custom' && !start.value && !end.value) {
        const [from, to] = window_(); // start from what is on screen now
        start.value = ymd(Math.max(from, first));
        end.value = ymd(Math.min(to, now));
      }
      range = b.value;
      apply();
      if (range === 'custom') start.focus();
    });
  }
  if (start) {
    for (const input of [start, end]) {
      if (times.length) { input.min = ymd(first); input.max = ymd(now); }
      input.addEventListener('change', apply);
    }
  }
  if (text) text.addEventListener('input', apply);
  for (const group of document.querySelectorAll('.chips[data-filter]')) group.hidden = false;
  apply();
})();
"""
