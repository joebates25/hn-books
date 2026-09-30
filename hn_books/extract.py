"""Pull book-ish retailer/publisher URLs out of HN comment HTML."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

from hn_books.isbn import canonical_isbn13, isbn13_to_isbn10, is_isbn10, normalize_isbn
from hn_books.models import BookCandidate

HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
BARE_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

AMAZON_HOSTS = {
    "amazon.com",
    "amazon.co.uk",
    "amazon.de",
    "amazon.ca",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.co.jp",
    "amazon.in",
    "amazon.com.au",
    "amazon.com.br",
    "amazon.com.mx",
    "amazon.nl",
    "amazon.se",
    "amazon.pl",
    "amazon.com.be",
    "amazon.sg",
    "amazon.ae",
    "amazon.sa",
    "amazon.com.tr",
    "amzn.to",
    "amzn.com",
    "a.co",
    "smile.amazon.com",
}

AMAZON_SHORT_HOSTS = {"amzn.to", "amzn.com", "a.co"}

AMAZON_ID_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|exec/obidos/asin|o/asin)/([A-Z0-9]{10}|[0-9]{13})",
    re.IGNORECASE,
)
AMAZON_ISBN_RE = re.compile(r"/(?:isbn|dp)/([0-9]{13}|[0-9Xx]{10})\b")

OREILLY_ISBN_RE = re.compile(
    r"/library/view/[^/]+/(\d{13}|\d{10})(?:/|$)",
    re.IGNORECASE,
)
OREILLY_PRODUCT_RE = re.compile(r"/product/(\d{13}|\d{10})\b", re.IGNORECASE)

GOODREADS_BOOK_RE = re.compile(
    r"/book/show/(\d+)(?:[-.]([A-Za-z0-9._-]+))?",
    re.IGNORECASE,
)
GOODREADS_ISBN_RE = re.compile(r"/isbn/(\d{13}|\d{10}|[0-9Xx]{10})\b", re.IGNORECASE)

OPENLIBRARY_ISBN_RE = re.compile(r"/isbn/(\d{13}|\d{10}|[0-9Xx]{10})\b", re.IGNORECASE)
OPENLIBRARY_WORK_RE = re.compile(r"/works/(OL\d+W)\b", re.IGNORECASE)
OPENLIBRARY_BOOK_RE = re.compile(r"/books/(OL\d+M)\b", re.IGNORECASE)

MANNING_RE = re.compile(r"/books/([a-z0-9-]+)/?", re.IGNORECASE)
PRAGPROG_RE = re.compile(r"/titles/([a-z0-9-]+)/?", re.IGNORECASE)
NOSEARCH_RE = re.compile(r"/(?:books?|product)/([a-z0-9-]+)/?", re.IGNORECASE)

NOSEARCH_SKIP = {
    "about",
    "blog",
    "catalog",
    "cart",
    "catalogs",
    "contact",
    "download",
    "media",
    "news",
    "search",
    "shop",
    "support",
    "account",
    "login",
    "events",
}


def _host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("smile."):
        host = host[6:]
    return host


def _slug_to_title(slug: str | None) -> str | None:
    if not slug:
        return None
    slug = slug.replace(".", " ").replace("_", " ").replace("-", " ")
    slug = re.sub(r"\s+", " ", slug).strip()
    if not slug:
        return None
    return slug


def _ids_from_token(token: str) -> tuple[str | None, str | None, str | None]:
    """Return (isbn13, isbn10, asin) for a product-id-like token."""
    raw = token.strip()
    isbn13 = canonical_isbn13(raw)
    if isbn13:
        isbn10 = None
        compact = normalize_isbn(raw)
        if is_isbn10(compact):
            isbn10 = compact
        elif isbn13.startswith("978"):
            isbn10 = isbn13_to_isbn10(isbn13)
        return isbn13, isbn10, None
    if re.fullmatch(r"[A-Za-z0-9]{10}", raw):
        return None, None, raw.upper()
    return None, None, None


def normalize_url(url: str) -> str:
    url = html.unescape(url).strip()
    url = url.rstrip(").,;]>\"'")
    parsed = urlparse(url)
    if not parsed.scheme:
        return url
    host = _host(parsed.netloc)
    query = parse_qs(parsed.query)
    keep = {}
    if host.startswith("books.google."):
        for key in ("id", "isbn", "vid"):
            if key in query and query[key]:
                keep[key] = query[key][0]
    path = parsed.path or ""
    if path.endswith("/") and path != "/":
        path = path[:-1]
    if keep:
        q = "&".join(f"{k}={v}" for k, v in keep.items())
        return f"https://{host}{path}?{q}"
    return f"https://{host}{path}"


def extract_urls(text: str) -> list[str]:
    text = html.unescape(text or "")
    found: list[str] = []
    seen: set[str] = set()
    for match in HREF_RE.finditer(text):
        url = match.group(1)
        if url not in seen:
            seen.add(url)
            found.append(url)
    stripped = HREF_RE.sub(" ", text)
    for match in BARE_URL_RE.finditer(stripped):
        url = match.group(0)
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def classify_url(url: str) -> BookCandidate | None:
    raw = html.unescape(url).strip()
    parsed = urlparse(raw)
    host = _host(parsed.netloc)
    path = parsed.path or ""
    query = parse_qs(parsed.query)
    normalized = normalize_url(raw)

    if host in AMAZON_HOSTS or host.endswith(".amazon.com"):
        if host in AMAZON_SHORT_HOSTS:
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="amazon",
                detection_method="amazon_short_url",
            )
        match = AMAZON_ID_RE.search(path) or AMAZON_ISBN_RE.search(path)
        if not match:
            return None
        isbn13, isbn10, asin = _ids_from_token(match.group(1))
        title_hint = None
        parts = [p for p in path.split("/") if p]
        if "dp" in parts:
            idx = parts.index("dp")
            if idx > 0:
                title_hint = _slug_to_title(parts[idx - 1])
        return BookCandidate(
            url=raw,
            normalized_url=normalized,
            source="amazon",
            detection_method="amazon_url",
            isbn13=isbn13,
            isbn10=isbn10,
            asin=asin,
            title_hint=title_hint,
            extra_id=asin or isbn13,
        )

    if host in {"goodreads.com"}:
        if "/author/" in path:
            return None
        match = GOODREADS_BOOK_RE.search(path)
        if match:
            book_id = match.group(1)
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="goodreads",
                detection_method="goodreads_url",
                title_hint=_slug_to_title(match.group(2)),
                extra_id=book_id,
            )
        match = GOODREADS_ISBN_RE.search(path)
        if match:
            isbn13, isbn10, _ = _ids_from_token(match.group(1))
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="goodreads",
                detection_method="goodreads_isbn_url",
                isbn13=isbn13,
                isbn10=isbn10,
                extra_id=isbn13,
            )
        return None

    if host.startswith("books.google.") or (host == "google.com" and path.startswith("/books")):
        if "ngrams" in path:
            return None
        isbn_token = (query.get("isbn") or [None])[0]
        google_id = (query.get("id") or [None])[0]
        if isbn_token:
            isbn13, isbn10, _ = _ids_from_token(isbn_token)
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="google_books",
                detection_method="google_books_isbn_url",
                isbn13=isbn13,
                isbn10=isbn10,
                extra_id=isbn13 or google_id,
            )
        if google_id:
            about = re.search(r"/about/([^/]+)", path)
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="google_books",
                detection_method="google_books_url",
                title_hint=_slug_to_title(about.group(1) if about else None),
                extra_id=google_id,
            )
        return None

    if host == "openlibrary.org":
        match = OPENLIBRARY_ISBN_RE.search(path)
        if match:
            isbn13, isbn10, _ = _ids_from_token(match.group(1))
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="openlibrary",
                detection_method="openlibrary_isbn_url",
                isbn13=isbn13,
                isbn10=isbn10,
                extra_id=isbn13,
            )
        match = OPENLIBRARY_WORK_RE.search(path)
        if match:
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="openlibrary",
                detection_method="openlibrary_work_url",
                extra_id=match.group(1),
            )
        match = OPENLIBRARY_BOOK_RE.search(path)
        if match:
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="openlibrary",
                detection_method="openlibrary_book_url",
                extra_id=match.group(1),
            )
        return None

    if host == "oreilly.com" or host.endswith(".oreilly.com"):
        match = OREILLY_ISBN_RE.search(path) or OREILLY_PRODUCT_RE.search(path)
        if not match:
            return None
        isbn13, isbn10, _ = _ids_from_token(match.group(1))
        slug = None
        view = re.search(r"/library/view/([^/]+)/", path)
        if view:
            slug = view.group(1)
        return BookCandidate(
            url=raw,
            normalized_url=normalized,
            source="oreilly",
            detection_method="oreilly_url",
            isbn13=isbn13,
            isbn10=isbn10,
            title_hint=_slug_to_title(slug),
            extra_id=isbn13,
        )

    if host in {"manning.com"}:
        match = MANNING_RE.search(path)
        if not match:
            return None
        slug = match.group(1)
        return BookCandidate(
            url=raw,
            normalized_url=normalized,
            source="manning",
            detection_method="manning_url",
            title_hint=_slug_to_title(slug),
            extra_id=slug,
        )

    if host in {"pragprog.com"}:
        match = PRAGPROG_RE.search(path)
        if not match:
            return None
        slug = match.group(1)
        return BookCandidate(
            url=raw,
            normalized_url=normalized,
            source="pragprog",
            detection_method="pragprog_url",
            title_hint=_slug_to_title(slug),
            extra_id=slug,
        )

    if host in {"nostarch.com"}:
        match = NOSEARCH_RE.search(path)
        slug = match.group(1) if match else None
        if slug is None:
            parts = [p for p in path.split("/") if p]
            if len(parts) == 1 and parts[0].lower() not in NOSEARCH_SKIP:
                slug = parts[0]
        if not slug:
            return None
        return BookCandidate(
            url=raw,
            normalized_url=normalized,
            source="nostarch",
            detection_method="nostarch_url",
            title_hint=_slug_to_title(slug),
            extra_id=slug,
        )

    # Last-ditch: any URL that clearly embeds an ISBN.
    isbn_in_path = re.search(r"(97[89]\d{10})", path)
    if isbn_in_path and host not in {"news.ycombinator.com", "github.com"}:
        isbn13 = canonical_isbn13(isbn_in_path.group(1))
        if isbn13:
            return BookCandidate(
                url=raw,
                normalized_url=normalized,
                source="isbn_url",
                detection_method="isbn_in_url",
                isbn13=isbn13,
                extra_id=isbn13,
            )
    return None


def extract_candidates(text: str) -> list[BookCandidate]:
    candidates: list[BookCandidate] = []
    seen: set[str] = set()
    for url in extract_urls(text):
        candidate = classify_url(url)
        if candidate is None:
            continue
        key = candidate.normalized_url
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates
