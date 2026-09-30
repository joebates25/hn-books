"""SQLite persistence for comments, books, and mentions."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hn_books.models import Comment, ResolvedBook

SCHEMA = """
CREATE TABLE IF NOT EXISTS comment (
    id INTEGER PRIMARY KEY,
    story_id INTEGER,
    parent_id INTEGER,
    author TEXT,
    created_at INTEGER NOT NULL,
    text TEXT NOT NULL,
    story_title TEXT
);

CREATE TABLE IF NOT EXISTS book (
    id INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT,
    isbn13 TEXT,
    isbn10 TEXT,
    openlibrary_work_id TEXT,
    openlibrary_book_id TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS mention (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES book(id),
    comment_id INTEGER NOT NULL REFERENCES comment(id),
    matched_text TEXT NOT NULL,
    detection_method TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    UNIQUE(book_id, comment_id, matched_text)
);

CREATE TABLE IF NOT EXISTS resolve_cache (
    cache_key TEXT PRIMARY KEY,
    identity_key TEXT,
    payload_json TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mention_book ON mention(book_id);
CREATE INDEX IF NOT EXISTS idx_mention_comment ON mention(comment_id);
CREATE INDEX IF NOT EXISTS idx_comment_created ON comment(created_at);
CREATE INDEX IF NOT EXISTS idx_book_isbn13 ON book(isbn13);
CREATE INDEX IF NOT EXISTS idx_book_title ON book(title);
"""


@dataclass(frozen=True)
class RankedBook:
    id: int
    title: str
    author: str | None
    isbn13: str | None
    source: str | None
    mentions: int
    unique_users: int
    unique_threads: int
    score: float


@dataclass(frozen=True)
class MentionRow:
    comment_id: int
    author: str | None
    created_at: int
    story_id: int | None
    story_title: str | None
    matched_text: str
    detection_method: str
    snippet: str


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def cache_get(self, cache_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload_json FROM resolve_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None or row["payload_json"] is None:
            return None
        return json.loads(row["payload_json"])

    def cache_set(self, cache_key: str, identity_key: str | None, payload: dict | None) -> None:
        now = int(datetime.now(tz=timezone.utc).timestamp())
        self.conn.execute(
            """
            INSERT INTO resolve_cache(cache_key, identity_key, payload_json, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                identity_key = excluded.identity_key,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (cache_key, identity_key, None if payload is None else json.dumps(payload), now),
        )
        self.conn.commit()

    def upsert_comment(self, comment: Comment) -> None:
        created = int(comment.created_at.timestamp())
        self.conn.execute(
            """
            INSERT INTO comment(id, story_id, parent_id, author, created_at, text, story_title)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                story_id = COALESCE(excluded.story_id, comment.story_id),
                parent_id = COALESCE(excluded.parent_id, comment.parent_id),
                author = COALESCE(excluded.author, comment.author),
                created_at = excluded.created_at,
                text = excluded.text,
                story_title = COALESCE(excluded.story_title, comment.story_title)
            """,
            (
                comment.id,
                comment.story_id,
                comment.parent_id,
                comment.author,
                created,
                comment.text,
                comment.story_title,
            ),
        )

    def upsert_book(self, book: ResolvedBook, identity_key: str) -> int:
        row = self.conn.execute(
            "SELECT id, title, author, isbn13 FROM book WHERE identity_key = ?",
            (identity_key,),
        ).fetchone()
        if row is None and book.isbn13:
            row = self.conn.execute(
                "SELECT id, identity_key FROM book WHERE isbn13 = ?",
                (book.isbn13,),
            ).fetchone()
            if row is not None:
                return int(row["id"])
        if row is None:
            cur = self.conn.execute(
                """
                INSERT INTO book(
                    identity_key, title, author, isbn13, isbn10,
                    openlibrary_work_id, openlibrary_book_id, source
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity_key,
                    book.title,
                    book.author,
                    book.isbn13,
                    book.isbn10,
                    book.openlibrary_work_id,
                    book.openlibrary_book_id,
                    book.source,
                ),
            )
            return int(cur.lastrowid)
        # Fill in missing metadata if a later resolution is richer.
        self.conn.execute(
            """
            UPDATE book SET
                title = CASE WHEN title = identity_key OR title = '' THEN ? ELSE title END,
                author = COALESCE(author, ?),
                isbn13 = COALESCE(isbn13, ?),
                isbn10 = COALESCE(isbn10, ?),
                openlibrary_work_id = COALESCE(openlibrary_work_id, ?),
                openlibrary_book_id = COALESCE(openlibrary_book_id, ?)
            WHERE id = ?
            """,
            (
                book.title,
                book.author,
                book.isbn13,
                book.isbn10,
                book.openlibrary_work_id,
                book.openlibrary_book_id,
                row["id"],
            ),
        )
        return int(row["id"])

    def add_mention(
        self,
        book_id: int,
        comment_id: int,
        matched_text: str,
        detection_method: str,
        confidence: float,
    ) -> bool:
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO mention(
                book_id, comment_id, matched_text, detection_method, confidence
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (book_id, comment_id, matched_text, detection_method, confidence),
        )
        return cur.rowcount > 0

    def commit(self) -> None:
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "comments": count("comment"),
            "books": count("book"),
            "mentions": count("mention"),
        }

    def ranked_books(
        self, since_unix: int | None = None, limit: int | None = 25, resolved_only: bool = False
    ) -> list[RankedBook]:
        clauses = []
        params: list = []
        if since_unix is not None:
            clauses.append("c.created_at >= ?")
            params.append(since_unix)
        if resolved_only:
            # Unresolved books only have a URL slug or ASIN as a title, so keep them off public pages.
            clauses.append("(b.author IS NOT NULL OR b.openlibrary_work_id IS NOT NULL)")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT
                b.id,
                b.title,
                b.author,
                b.isbn13,
                b.source,
                COUNT(m.id) AS mentions,
                COUNT(DISTINCT c.author) AS unique_users,
                COUNT(DISTINCT COALESCE(c.story_id, c.id)) AS unique_threads
            FROM book b
            JOIN mention m ON m.book_id = b.id
            JOIN comment c ON c.id = m.comment_id
            {where}
            GROUP BY b.id
            """,
            params,
        ).fetchall()
        ranked = []
        for row in rows:
            mentions = int(row["mentions"])
            users = int(row["unique_users"])
            threads = int(row["unique_threads"])
            score = users + threads * 2 + math.log(mentions + 1)
            ranked.append(
                RankedBook(
                    id=int(row["id"]),
                    title=row["title"],
                    author=row["author"],
                    isbn13=row["isbn13"],
                    source=row["source"],
                    mentions=mentions,
                    unique_users=users,
                    unique_threads=threads,
                    score=score,
                )
            )
        ranked.sort(key=lambda b: (b.score, b.mentions, b.unique_threads), reverse=True)
        return ranked[:limit]

    def search_books(self, query: str, limit: int = 25) -> list[sqlite3.Row]:
        like = f"%{query}%"
        return self.conn.execute(
            """
            SELECT id, title, author, isbn13, source
            FROM book
            WHERE title LIKE ? OR IFNULL(author, '') LIKE ? OR IFNULL(isbn13, '') LIKE ?
            ORDER BY title
            LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()

    def get_book(self, token: str) -> sqlite3.Row | None:
        if token.isdigit():
            row = self.conn.execute("SELECT * FROM book WHERE id = ?", (int(token),)).fetchone()
            if row:
                return row
        row = self.conn.execute("SELECT * FROM book WHERE isbn13 = ?", (token,)).fetchone()
        if row:
            return row
        return self.conn.execute(
            "SELECT * FROM book WHERE title LIKE ? ORDER BY id LIMIT 1",
            (f"%{token}%",),
        ).fetchone()

    def mentions_for_book(
        self, book_id: int, limit: int | None = 20, snippet_width: int = 160
    ) -> list[MentionRow]:
        rows = self.conn.execute(
            """
            SELECT
                c.id AS comment_id,
                c.author,
                c.created_at,
                c.story_id,
                c.story_title,
                c.text,
                m.matched_text,
                m.detection_method
            FROM mention m
            JOIN comment c ON c.id = m.comment_id
            WHERE m.book_id = ?
            ORDER BY c.created_at DESC
            LIMIT ?
            """,
            (book_id, -1 if limit is None else limit),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                MentionRow(
                    comment_id=int(row["comment_id"]),
                    author=row["author"],
                    created_at=int(row["created_at"]),
                    story_id=row["story_id"],
                    story_title=row["story_title"],
                    matched_text=row["matched_text"],
                    detection_method=row["detection_method"],
                    snippet=_snippet(row["text"], snippet_width),
                )
            )
        return out


def _snippet(text: str, width: int = 160) -> str:
    from html import unescape
    import re

    cleaned = unescape(text or "")
    cleaned = re.sub(r"<p>", "\n", cleaned, flags=re.I)
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.I)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= width:
        return cleaned
    return cleaned[: width - 1] + "…"
