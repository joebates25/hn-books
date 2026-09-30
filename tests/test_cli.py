from pathlib import Path

from hn_books.cli import main


def test_stats_on_empty_db(tmp_path: Path, capsys):
    db = tmp_path / "hn.sqlite"
    assert main(["--db", str(db), "stats"]) == 0
    out = capsys.readouterr().out
    assert "comments  0" in out
    assert "books     0" in out
    assert "mentions  0" in out


def test_top_empty_db(tmp_path: Path, capsys):
    db = tmp_path / "hn.sqlite"
    assert main(["--db", str(db), "top"]) == 0
    assert "No books indexed yet" in capsys.readouterr().out


def test_build_site_on_empty_db(tmp_path: Path, capsys):
    db = tmp_path / "hn.sqlite"
    out = tmp_path / "site"
    assert main(["--db", str(db), "build-site", "--out", str(out)]) == 0
    assert "0 books" in capsys.readouterr().out
    for name in ("index.html", "recent.html", "all.html", "books.json", "style.css", ".nojekyll"):
        assert (out / name).exists()
