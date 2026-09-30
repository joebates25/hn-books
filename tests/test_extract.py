from hn_books.extract import extract_candidates


DDIA = "https://www.amazon.com/Designing-Data-Intensive-Applications/dp/1449373321/"


def test_amazon_dp_isbn():
    text = f'You should read <a href="{DDIA}">this</a>.'
    cands = extract_candidates(text)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.source == "amazon"
    assert cand.isbn13 == "9781449373320"
    assert cand.isbn10 == "1449373321"
    assert cand.title_hint and "designing" in cand.title_hint.lower()


def test_amazon_asin_not_isbn():
    text = '<a href="https://www.amazon.com/dp/B0F498PG6P?th=1">kindle</a>'
    cand = extract_candidates(text)[0]
    assert cand.asin == "B0F498PG6P"
    assert cand.isbn13 is None


def test_oreilly_library_url():
    text = '<a href="https://www.oreilly.com/library/view/javascript-the-good/9780596517748/">JS</a>'
    cand = extract_candidates(text)[0]
    assert cand.source == "oreilly"
    assert cand.isbn13 == "9780596517748"


def test_goodreads_book_url():
    text = '<a href="https://www.goodreads.com/book/show/10256723-ghost-in-the-wires">gr</a>'
    cand = extract_candidates(text)[0]
    assert cand.source == "goodreads"
    assert cand.extra_id == "10256723"
    assert cand.title_hint and "ghost" in cand.title_hint.lower()


def test_goodreads_author_ignored():
    text = '<a href="https://www.goodreads.com/author/show/25307.Robin_Hobb">author</a>'
    assert extract_candidates(text) == []


def test_google_books_and_ngrams():
    books = extract_candidates(
        '<a href="https://books.google.com/books?id=NkxZcHL1xdYC&pg=PA6">gb</a>'
    )
    assert books[0].source == "google_books"
    assert books[0].extra_id == "NkxZcHL1xdYC"
    ngrams = extract_candidates(
        '<a href="https://books.google.com/ngrams/graph?content=kidney">ng</a>'
    )
    assert ngrams == []


def test_manning_pragprog_nostarch():
    text = """
    <a href="https://www.manning.com/books/just-use-postgres">m</a>
    <a href="https://pragprog.com/titles/tpp20/the-pragmatic-programmer-20th-anniversary-edition/">p</a>
    <a href="https://nostarch.com/python-crash-course">n</a>
    """
    sources = {c.source for c in extract_candidates(text)}
    assert sources == {"manning", "pragprog", "nostarch"}


def test_html_escaped_href_and_duplicates():
    text = (
        'see <a href="https:&#x2F;&#x2F;www.amazon.com&#x2F;dp&#x2F;1449373321">one</a> '
        "and https://www.amazon.com/dp/1449373321 extra"
    )
    cands = extract_candidates(text)
    assert len(cands) == 1
    assert cands[0].isbn13 == "9781449373320"


def test_openlibrary_isbn():
    text = '<a href="https://openlibrary.org/isbn/9781449373320">ol</a>'
    cand = extract_candidates(text)[0]
    assert cand.source == "openlibrary"
    assert cand.isbn13 == "9781449373320"


def test_non_book_url_ignored():
    text = '<a href="https://news.ycombinator.com/item?id=1">hn</a>'
    assert extract_candidates(text) == []
