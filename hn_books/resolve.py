"""Resolve a book URL/candidate to a canonical Book via Open Library."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

import httpx

from hn_books.db import Store
from hn_books.extract import classify_url
from hn_books.isbn import isbn13_to_isbn10
from hn_books.models import BookCandidate, ResolvedBook

USER_AGENT = "hn-books/0.1 (hobby CLI; +https://github.com/HackerNews/API)"
OPENLIBRARY_BOOKS = "https://openlibrary.org/api/books"
OPENLIBRARY_SEARCH = "https://openlibrary.org/search.json"


@dataclass(frozen=True)
class Resolution:
    book: ResolvedBook
    identity_key: str
    confidence: float
    cache_hit: bool = False


class Resolver:
    def __init__(self, store: Store, client: httpx.Client | None = None, delay: float = 0.15):
        self.store = store
        self.delay = delay
        self._last_call = 0.0
        self.client = client or httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def close(self) -> None:
        self.client.close()

    def resolve(self, candidate: BookCandidate) -> Resolution:
        candidate = self._expand_short_url(candidate)
        cache_key = candidate.isbn13 or candidate.normalized_url
        cached = self.store.cache_get(cache_key)
        if cached:
            book = ResolvedBook(**cached["book"])
            return Resolution(
                book=book,
                identity_key=cached["identity_key"],
                confidence=cached["confidence"],
                cache_hit=True,
            )

        book, identity_key, confidence = self._resolve_uncached(candidate)
        self.store.cache_set(
            cache_key,
            identity_key,
            {"book": asdict(book), "identity_key": identity_key, "confidence": confidence},
        )
        return Resolution(book=book, identity_key=identity_key, confidence=confidence)

    def _expand_short_url(self, candidate: BookCandidate) -> BookCandidate:
        if candidate.detection_method != "amazon_short_url":
            return candidate
        try:
            self._throttle()
            response = self.client.get(candidate.url, headers={"Accept": "*/*"})
            expanded = classify_url(str(response.url))
            if expanded is not None:
                return expanded
        except httpx.HTTPError:
            return candidate
        return candidate

    def _resolve_uncached(self, candidate: BookCandidate) -> tuple[ResolvedBook, str, float]:
        if candidate.isbn13:
            ol = self._lookup_isbn(candidate.isbn13)
            if ol:
                return ol, f"isbn:{ol.isbn13 or candidate.isbn13}", 1.0
            return (
                ResolvedBook(
                    title=candidate.title_hint or candidate.isbn13,
                    isbn13=candidate.isbn13,
                    isbn10=candidate.isbn10,
                    source=candidate.source,
                ),
                f"isbn:{candidate.isbn13}",
                0.9,
            )

        if candidate.source == "openlibrary" and candidate.extra_id:
            ol = self._lookup_ol_id(candidate.extra_id)
            if ol:
                key = f"isbn:{ol.isbn13}" if ol.isbn13 else f"ol:{candidate.extra_id}"
                return ol, key, 0.95

        if candidate.title_hint and len(candidate.title_hint.split()) >= 3:
            ol = self._search_title(candidate.title_hint)
            if ol:
                key = f"isbn:{ol.isbn13}" if ol.isbn13 else f"ol:{ol.openlibrary_work_id}"
                return ol, key, 0.6

        identity = _fallback_identity(candidate)
        title = candidate.title_hint or identity
        return (
            ResolvedBook(
                title=title,
                isbn13=candidate.isbn13,
                isbn10=candidate.isbn10,
                source=candidate.source,
            ),
            identity,
            0.4,
        )

    def _lookup_isbn(self, isbn13: str) -> ResolvedBook | None:
        data = self._get_json(
            OPENLIBRARY_BOOKS,
            params={"bibkeys": f"ISBN:{isbn13}", "jscmd": "data", "format": "json"},
        )
        if not data:
            return None
        record = data.get(f"ISBN:{isbn13}")
        if not record:
            return None
        return _book_from_ol_data(record, isbn13)

    def _lookup_ol_id(self, ol_id: str) -> ResolvedBook | None:
        if ol_id.endswith("W"):
            data = self._get_json(f"https://openlibrary.org/works/{ol_id}.json")
            if not data:
                return None
            title = data.get("title") or ol_id
            return ResolvedBook(
                title=title,
                openlibrary_work_id=ol_id,
                source="openlibrary",
            )
        data = self._get_json(f"https://openlibrary.org/books/{ol_id}.json")
        if not data:
            return None
        isbn13 = None
        isbn10 = None
        ids = data.get("isbn_13") or []
        if ids:
            isbn13 = ids[0]
        ids10 = data.get("isbn_10") or []
        if ids10:
            isbn10 = ids10[0]
        work = None
        works = data.get("works") or []
        if works:
            work = str(works[0].get("key", "")).rsplit("/", 1)[-1]
        return ResolvedBook(
            title=data.get("title") or ol_id,
            isbn13=isbn13,
            isbn10=isbn10,
            openlibrary_work_id=work,
            openlibrary_book_id=ol_id,
            source="openlibrary",
        )

    def _search_title(self, title: str) -> ResolvedBook | None:
        data = self._get_json(OPENLIBRARY_SEARCH, params={"title": title, "limit": 1})
        if not data:
            return None
        docs = data.get("docs") or []
        if not docs:
            return None
        doc = docs[0]
        isbn13 = None
        for isbn in doc.get("isbn") or []:
            if len(isbn) == 13:
                isbn13 = isbn
                break
        authors = doc.get("author_name") or []
        work = doc.get("key") or ""
        work_id = work.rsplit("/", 1)[-1] if work else None
        return ResolvedBook(
            title=doc.get("title") or title,
            author=authors[0] if authors else None,
            isbn13=isbn13,
            isbn10=isbn13_to_isbn10(isbn13) if isbn13 else None,
            openlibrary_work_id=work_id if work_id and work_id.startswith("OL") else None,
            source="openlibrary",
        )

    def _get_json(self, url: str, params: dict | None = None) -> dict | None:
        self._throttle()
        try:
            response = self.client.get(url, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._last_call + self.delay - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()


def _book_from_ol_data(record: dict, isbn13: str) -> ResolvedBook:
    authors = record.get("authors") or []
    author = None
    if authors:
        author = authors[0].get("name")
    identifiers = record.get("identifiers") or {}
    isbn10s = identifiers.get("isbn_10") or []
    works = record.get("key") or ""
    book_id = None
    if "/books/" in works:
        book_id = works.rsplit("/", 1)[-1]
    return ResolvedBook(
        title=record.get("title") or isbn13,
        author=author,
        isbn13=isbn13,
        isbn10=isbn10s[0] if isbn10s else isbn13_to_isbn10(isbn13),
        openlibrary_book_id=book_id,
        source="openlibrary",
    )


def _fallback_identity(candidate: BookCandidate) -> str:
    if candidate.isbn13:
        return f"isbn:{candidate.isbn13}"
    if candidate.asin:
        return f"amazon:{candidate.asin}"
    if candidate.extra_id:
        return f"{candidate.source}:{candidate.extra_id}"
    return f"url:{candidate.normalized_url}"
