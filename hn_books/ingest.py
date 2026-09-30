"""Comment ingest: live HN API plus optional historical Parquet backfill."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import httpx

from hn_books.db import Store
from hn_books.extract import extract_candidates
from hn_books.models import Comment
from hn_books.resolve import Resolver

HN_API = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_API = "https://hn.algolia.com/api/v1/search_by_date"
HF_PARQUET = "https://huggingface.co/datasets/chrismarchetta/hacker-news/resolve/main/data/{year}/{year}-{month:02d}.parquet"
TYPE_COMMENT = 2
USER_AGENT = "hn-books/0.1 (hobby CLI)"

BOOK_URL_QUERIES = (
    "amazon.com/dp",
    "amazon.com/gp/product",
    "goodreads.com/book",
    "oreilly.com/library",
    "manning.com/books",
    "pragprog.com/titles",
    "nostarch.com",
    "openlibrary.org",
    "books.google.com/books",
    "amzn.to",
    "a.co/",
)


class Ingestor:
    def __init__(self, store: Store, resolver: Resolver, client: httpx.Client | None = None):
        self.store = store
        self.resolver = resolver
        self.client = client or httpx.Client(
            timeout=20.0,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def close(self) -> None:
        self.client.close()

    def ingest_comment(self, comment: Comment) -> int:
        candidates = extract_candidates(comment.text)
        if not candidates:
            return 0
        self.store.upsert_comment(comment)
        added = 0
        for candidate in candidates:
            resolution = self.resolver.resolve(candidate)
            book_id = self.store.upsert_book(resolution.book, resolution.identity_key)
            if self.store.add_mention(
                book_id=book_id,
                comment_id=comment.id,
                matched_text=candidate.url,
                detection_method=candidate.detection_method,
                confidence=resolution.confidence,
            ):
                added += 1
        return added

    def crawl(self, start_id: int | None = None, max_items: int | None = None, sleep: float = 0.0) -> dict[str, int]:
        maxitem = int(self._get_json(f"{HN_API}/maxitem.json"))
        if start_id is not None:
            item_id = start_id
        else:
            stored = self.store.get_meta("last_processed_item_id")
            if stored:
                item_id = int(stored) + 1
            else:
                item_id = max(1, maxitem - (max_items or 200) + 1)
        processed = 0
        comments = 0
        mentions = 0
        last_id = item_id - 1
        while item_id <= maxitem:
            if max_items is not None and processed >= max_items:
                break
            item = self._get_json(f"{HN_API}/item/{item_id}.json")
            processed += 1
            last_id = item_id
            if item and item.get("type") == "comment" and item.get("text"):
                comment = comment_from_hn_item(item)
                mentions += self.ingest_comment(comment)
                comments += 1
            if processed % 25 == 0:
                self.store.set_meta("last_processed_item_id", str(last_id))
                self.store.commit()
            if sleep:
                time.sleep(sleep)
            item_id += 1
        self.store.set_meta("last_processed_item_id", str(last_id))
        self.store.commit()
        return {
            "processed": processed,
            "comments": comments,
            "mentions": mentions,
            "last_id": last_id,
            "maxitem": maxitem,
        }

    def harvest(self, since_unix: int, until_unix: int | None = None) -> dict[str, int]:
        """Find recent comments that likely contain book URLs, then ingest them."""
        comments = list(self.iter_algolia_comments(since_unix, until_unix))
        mentions = 0
        for i, comment in enumerate(comments, start=1):
            mentions += self.ingest_comment(comment)
            if i % 25 == 0:
                self.store.commit()
        self.store.commit()
        return {"comments": len(comments), "mentions": mentions}

    def iter_algolia_comments(
        self, since_unix: int, until_unix: int | None = None
    ) -> Iterator[Comment]:
        seen: set[int] = set()
        for query in BOOK_URL_QUERIES:
            page = 0
            while True:
                filters = f"created_at_i>{since_unix}"
                if until_unix is not None:
                    filters += f",created_at_i<{until_unix}"
                data = self._get_json(
                    ALGOLIA_API,
                    params={
                        "query": query,
                        "tags": "comment",
                        "hitsPerPage": 100,
                        "page": page,
                        "numericFilters": filters,
                    },
                )
                hits = data.get("hits") or []
                if not hits:
                    break
                for hit in hits:
                    comment_id = int(hit.get("objectID") or 0)
                    if not comment_id or comment_id in seen:
                        continue
                    text = hit.get("comment_text") or ""
                    if not text:
                        continue
                    seen.add(comment_id)
                    created = datetime.fromtimestamp(
                        int(hit.get("created_at_i") or 0), tz=timezone.utc
                    )
                    story = hit.get("story_id")
                    yield Comment(
                        id=comment_id,
                        author=hit.get("author"),
                        created_at=created,
                        text=text,
                        story_id=int(story) if story is not None else None,
                        story_title=hit.get("story_title"),
                    )
                if page + 1 >= int(data.get("nbPages") or 1):
                    break
                page += 1

    def backfill_month(self, year: int, month: int, limit: int | None = None) -> dict[str, int]:
        url = HF_PARQUET.format(year=year, month=month)
        comments_seen = 0
        mentions = 0
        for comment in iter_parquet_comments(url, limit=limit):
            comments_seen += 1
            mentions += self.ingest_comment(comment)
            if comments_seen % 100 == 0:
                self.store.commit()
        self.store.commit()
        return {"comments": comments_seen, "mentions": mentions, "year": year, "month": month}

    def _get_json(self, url: str, params: dict | None = None) -> Any:
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def comment_from_hn_item(item: dict) -> Comment:
    created = datetime.fromtimestamp(int(item.get("time") or 0), tz=timezone.utc)
    return Comment(
        id=int(item["id"]),
        author=item.get("by"),
        created_at=created,
        text=item.get("text") or "",
        parent_id=item.get("parent"),
    )


def iter_parquet_comments(url: str, limit: int | None = None) -> Iterator[Comment]:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit(
            "Parquet backfill requires duckdb. Install with: pip install 'hn-books[parquet]'"
        ) from exc

    con = duckdb.connect()
    query = f"""
        SELECT id, "by", epoch(time)::BIGINT AS ts, text, parent
        FROM read_parquet(?)
        WHERE type = {TYPE_COMMENT}
          AND text IS NOT NULL
          AND text <> ''
    """
    if limit is not None:
        query += " LIMIT ?"
        result = con.execute(query, [url, limit])
    else:
        result = con.execute(query, [url])
    while True:
        rows = result.fetchmany(500)
        if not rows:
            break
        for row in rows:
            item_id, author, ts, text, parent = row
            created = datetime.fromtimestamp(int(ts or 0), tz=timezone.utc)
            yield Comment(
                id=int(item_id),
                author=author,
                created_at=created,
                text=text or "",
                parent_id=int(parent) if parent is not None else None,
            )
