"""ISBN-10 / ISBN-13 validation and conversion."""

from __future__ import annotations

import re

_ISBN_CHARS = re.compile(r"[^0-9X]", re.IGNORECASE)


def normalize_isbn(value: str | None) -> str:
    if not value:
        return ""
    return _ISBN_CHARS.sub("", value).upper()


def isbn10_check_digit(body9: str) -> str:
    total = sum((10 - i) * int(ch) for i, ch in enumerate(body9))
    remainder = total % 11
    check = (11 - remainder) % 11
    return "X" if check == 10 else str(check)


def isbn13_check_digit(body12: str) -> str:
    total = 0
    for i, ch in enumerate(body12):
        total += int(ch) * (1 if i % 2 == 0 else 3)
    return str((10 - (total % 10)) % 10)


def is_isbn10(value: str | None) -> bool:
    isbn = normalize_isbn(value)
    if len(isbn) != 10 or not isbn[:9].isdigit():
        return False
    if isbn[9] not in "0123456789X":
        return False
    return isbn10_check_digit(isbn[:9]) == isbn[9]


def is_isbn13(value: str | None) -> bool:
    isbn = normalize_isbn(value)
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    if isbn[:3] not in {"978", "979"}:
        return False
    return isbn13_check_digit(isbn[:12]) == isbn[12]


def isbn10_to_isbn13(value: str) -> str | None:
    isbn = normalize_isbn(value)
    if not is_isbn10(isbn):
        return None
    body = "978" + isbn[:9]
    return body + isbn13_check_digit(body)


def isbn13_to_isbn10(value: str) -> str | None:
    isbn = normalize_isbn(value)
    if not is_isbn13(isbn) or not isbn.startswith("978"):
        return None
    body = isbn[3:12]
    return body + isbn10_check_digit(body)


def canonical_isbn13(value: str | None) -> str | None:
    isbn = normalize_isbn(value)
    if is_isbn13(isbn):
        return isbn
    if is_isbn10(isbn):
        return isbn10_to_isbn13(isbn)
    return None
