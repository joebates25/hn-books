"""Command-line interface for the V0 HN book indexer."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hn_books.db import Store
from hn_books.ingest import Ingestor
from hn_books.resolve import Resolver
from hn_books.site import build_site

DEFAULT_DB = Path("data/hn_books.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hn-books",
        description="Index book mentions in Hacker News comments (V0: retailer/publisher URLs only).",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="SQLite database path (default: data/hn_books.sqlite)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="Ingest new comments from the official HN API")
    crawl.add_argument("--start-id", type=int, help="First item ID to process (default: resume, or last 200)")
    crawl.add_argument("--max-items", type=int, default=200, help="Max HN items to fetch (default: 200)")
    crawl.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between item fetches")

    harvest = sub.add_parser(
        "harvest",
        help="Find comments with book URLs via Algolia HN Search, then index them",
    )
    harvest.add_argument("--days", type=int, default=14, help="Look back N days (default: 14)")

    backfill = sub.add_parser("backfill", help="Ingest one month of comments from the Hugging Face Parquet archive")
    backfill.add_argument("--year", type=int, required=True)
    backfill.add_argument("--month", type=int, required=True)
    backfill.add_argument("--limit", type=int, help="Stop after N comments (useful for a smoke test)")

    top = sub.add_parser("top", help="Print ranked books")
    top.add_argument("--days", type=int, help="Only count mentions from the last N days")
    top.add_argument("--limit", type=int, default=25)

    show = sub.add_parser("show", help="Show a book and recent mention comments")
    show.add_argument("book", help="Book id, ISBN-13, or title fragment")
    show.add_argument("--limit", type=int, default=10)

    search = sub.add_parser("search", help="Search stored books by title/author/ISBN")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=25)

    sub.add_parser("stats", help="Print database counts")

    site = sub.add_parser("build-site", help="Write the static website (HTML + books.json)")
    site.add_argument("--out", default="site", help="Output directory (default: site)")
    site.add_argument("--top", type=int, default=100, help="Books on the Top page (default: 100)")
    site.add_argument("--recent-days", type=int, default=30, help="Window for the Recent page (default: 30)")

    args = parser.parse_args(argv)
    store = Store(args.db)
    try:
        if args.command == "crawl":
            return _cmd_crawl(store, args)
        if args.command == "harvest":
            return _cmd_harvest(store, args)
        if args.command == "backfill":
            return _cmd_backfill(store, args)
        if args.command == "top":
            return _cmd_top(store, args)
        if args.command == "show":
            return _cmd_show(store, args)
        if args.command == "search":
            return _cmd_search(store, args)
        if args.command == "stats":
            return _cmd_stats(store)
        if args.command == "build-site":
            return _cmd_build_site(store, args)
        parser.error(f"unknown command {args.command}")
        return 2
    finally:
        store.close()


def _cmd_crawl(store: Store, args: argparse.Namespace) -> int:
    resolver = Resolver(store)
    ingestor = Ingestor(store, resolver)
    try:
        result = ingestor.crawl(
            start_id=args.start_id,
            max_items=args.max_items,
            sleep=args.sleep,
        )
    finally:
        resolver.close()
        ingestor.close()
    print(
        f"processed {result['processed']} items "
        f"({result['comments']} comments, {result['mentions']} new mentions); "
        f"last_id={result['last_id']} maxitem={result['maxitem']}"
    )
    return 0


def _cmd_harvest(store: Store, args: argparse.Namespace) -> int:
    since = int((datetime.now(tz=timezone.utc) - timedelta(days=args.days)).timestamp())
    resolver = Resolver(store)
    ingestor = Ingestor(store, resolver)
    try:
        result = ingestor.harvest(since_unix=since)
    finally:
        resolver.close()
        ingestor.close()
    print(
        f"harvested last {args.days} days: "
        f"{result['comments']} candidate comments, {result['mentions']} new mentions"
    )
    return 0


def _cmd_backfill(store: Store, args: argparse.Namespace) -> int:
    resolver = Resolver(store)
    ingestor = Ingestor(store, resolver)
    try:
        result = ingestor.backfill_month(args.year, args.month, limit=args.limit)
    finally:
        resolver.close()
        ingestor.close()
    print(
        f"backfilled {args.year}-{args.month:02d}: "
        f"{result['comments']} comments, {result['mentions']} new mentions"
    )
    return 0


def _cmd_top(store: Store, args: argparse.Namespace) -> int:
    since = None
    if args.days is not None:
        since = int((datetime.now(tz=timezone.utc) - timedelta(days=args.days)).timestamp())
    books = store.ranked_books(since_unix=since, limit=args.limit)
    if not books:
        print("No books indexed yet. Try: hn-books crawl --max-items 500")
        return 0
    print(f"{'#':>3}  {'score':>6}  {'ment':>4}  {'usr':>3}  {'thd':>3}  book")
    for i, book in enumerate(books, start=1):
        author = f" — {book.author}" if book.author else ""
        isbn = f" [{book.isbn13}]" if book.isbn13 else ""
        print(
            f"{i:3d}  {book.score:6.1f}  {book.mentions:4d}  {book.unique_users:3d}  "
            f"{book.unique_threads:3d}  {book.title}{author}{isbn}"
        )
    return 0


def _cmd_show(store: Store, args: argparse.Namespace) -> int:
    book = store.get_book(args.book)
    if book is None:
        print(f"No book matching {args.book!r}", file=sys.stderr)
        return 1
    print(f"#{book['id']}  {book['title']}")
    if book["author"]:
        print(f"author  {book['author']}")
    if book["isbn13"]:
        print(f"isbn13  {book['isbn13']}")
    if book["openlibrary_work_id"]:
        print(f"openlibrary  https://openlibrary.org/works/{book['openlibrary_work_id']}")
    print(f"source  {book['source']}")
    print()
    mentions = store.mentions_for_book(int(book["id"]), limit=args.limit)
    if not mentions:
        print("No mentions stored.")
        return 0
    for mention in mentions:
        when = datetime.fromtimestamp(mention.created_at, tz=timezone.utc).strftime("%Y-%m-%d")
        author = mention.author or "anon"
        print(f"- {when}  {author}  https://news.ycombinator.com/item?id={mention.comment_id}")
        print(f"  {mention.matched_text}  ({mention.detection_method})")
        if mention.snippet:
            print(f"  {mention.snippet}")
    return 0


def _cmd_search(store: Store, args: argparse.Namespace) -> int:
    rows = store.search_books(args.query, limit=args.limit)
    if not rows:
        print("No matches.")
        return 0
    for row in rows:
        author = f" — {row['author']}" if row["author"] else ""
        isbn = f" [{row['isbn13']}]" if row["isbn13"] else ""
        print(f"#{row['id']}  {row['title']}{author}{isbn}")
    return 0


def _cmd_stats(store: Store) -> int:
    stats = store.stats()
    last = store.get_meta("last_processed_item_id") or "-"
    print(f"comments  {stats['comments']}")
    print(f"books     {stats['books']}")
    print(f"mentions  {stats['mentions']}")
    print(f"last_id   {last}")
    return 0


def _cmd_build_site(store: Store, args: argparse.Namespace) -> int:
    result = build_site(store, args.out, top_limit=args.top, recent_days=args.recent_days)
    print(f"wrote {result.pages} pages for {result.books} books to {result.out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
