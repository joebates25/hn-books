"""Core dataclasses for the V0 pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Comment:
    id: int
    author: str | None
    created_at: datetime
    text: str
    parent_id: int | None = None
    story_id: int | None = None
    story_title: str | None = None
    story_url: str | None = None
    story_score: int | None = None


@dataclass(frozen=True)
class BookCandidate:
    """A URL in a comment that might point at a book."""

    url: str
    normalized_url: str
    source: str
    detection_method: str
    isbn13: str | None = None
    isbn10: str | None = None
    asin: str | None = None
    title_hint: str | None = None
    extra_id: str | None = None


@dataclass(frozen=True)
class ResolvedBook:
    title: str
    author: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    openlibrary_work_id: str | None = None
    openlibrary_book_id: str | None = None
    cover_id: int | None = None
    source: str = "openlibrary"
