from hn_books.isbn import canonical_isbn13, isbn10_to_isbn13, isbn13_to_isbn10, is_isbn10, is_isbn13


def test_isbn13_valid():
    assert is_isbn13("9781449373320")
    assert not is_isbn13("9781449373321")
    assert not is_isbn13("123")


def test_isbn10_valid():
    assert is_isbn10("1449373321")
    assert is_isbn10("0-306-40615-2")
    assert not is_isbn10("1449373320")


def test_conversions():
    assert isbn10_to_isbn13("1449373321") == "9781449373320"
    assert isbn13_to_isbn10("9781449373320") == "1449373321"
    assert canonical_isbn13("1449373321") == "9781449373320"
    assert canonical_isbn13("978-1-4493-7332-0") == "9781449373320"
