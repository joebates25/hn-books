# hn-books

V0 Hacker News book-mention indexer: a **command-line tool** that finds book URLs in comments, resolves them to canonical books, and ranks them.

This is the URL-only slice: no LLM extraction and no title matching yet. It also builds a static website (see below).

```text
HN comments
    │
    ▼
URL detection (Amazon, Goodreads, O'Reilly, Manning, …)
    │
    ▼
Open Library resolution (ISBN / work id)
    │
    ▼
SQLite: Comment + Book + Mention
    │
    ▼
hn-books top / show / search
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Parquet backfill also needs DuckDB:

```bash
pip install -e ".[dev,parquet]"
```

## Commands

All data lives in `data/hn_books.sqlite` unless you pass `--db`.

```bash
# Pull recent comments from the official HN API and index book URLs
hn-books crawl --max-items 500

# Historical month from the Hugging Face Parquet archive
hn-books backfill --year 2026 --month 1 --limit 2000

# Ranked books (independent commenters + threads beat raw mention count)
hn-books top
hn-books top --days 7 --limit 20

# Inspect one book
hn-books show 9781449373320
hn-books show "data-intensive"

# Search the local catalog
hn-books search kleppmann

hn-books stats
```

`crawl` remembers `last_processed_item_id` in the database and resumes from there.

## What V0 detects

Book-ish links in comment HTML, including HN's escaped `href`s:

- Amazon `/dp/`, `/gp/product/`, short `amzn.to` / `a.co` links
- Goodreads `/book/show/…` (author pages ignored)
- Google Books (ngrams ignored)
- Open Library ISBN / work / edition URLs
- O'Reilly library/product URLs with ISBNs
- Manning, PragProg, No Starch title slugs
- Any other URL that embeds an ISBN-13

Comments without a matching URL are skipped. Textual mentions like “read DDIA” are out of scope for V0.

## Ranking

```text
score = uniqueUsers + uniqueThreads * 2 + log(mentions + 1)
```

Independent mentions across threads rank higher than a pile-on in one story.

## Next (not in V0)

ISBN-in-text, exact/fuzzy title matching, LLM extraction, alias learning (`DDIA` → Kleppmann), and a website.

## Website

```bash
# Write site/ (index, recent, all books, one page per book, books.json)
hn-books build-site --out site
python3 -m http.server --directory site 8000
```

Only resolved books (with an author or Open Library match) are published.
Unresolved URL slugs and ASINs stay in the database.

`.github/workflows/publish.yml` runs every 6 hours on GitHub Actions:
restore the database from the `data` branch, `harvest --days 3`, push the
database back (the `data` branch is force-pushed, so it always has one commit), build the site, and deploy
it to GitHub Pages. Run it by hand from the Actions tab with "Run workflow".

