"""`count` on the read path — the enumeration command.

`search` answers "show me the best hits" and is capped, deliberately, because
excerpts are what cost tokens. That cap makes two ordinary questions
unanswerable from the outside: "what are ALL the call sites" and "does this
appear anywhere at all", because a clipped page and a complete one look the
same. `count` answers both by returning paths and totals instead of text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.cli import main


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    (r / "a.py").write_text("needle_token = 1\nneedle_token = 2\n")
    (r / "b.py").write_text("x = needle_token\n")
    (r / "notes.md").write_text("Prose about needle_token.\n")
    return r


def test_count_reports_totals_and_a_per_file_roster(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["count", "needle_token", "--root", str(root)])
    captured = capsys.readouterr()

    assert rc == 0
    # Totals go to stderr so the roster can be piped without being polluted.
    assert "4 matches in 3 files" in captured.err
    assert "a.py" in captured.out and "b.py" in captured.out
    assert "notes.md" in captured.out
    # Counts are per file, heaviest first — the shape a caller sorts by when
    # deciding where a rename actually bites.
    assert captured.out.splitlines()[0].split()[0] == "2"


def test_docs_flag_restricts_the_totals_not_just_the_listing(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["count", "needle_token", "--root", str(root), "--docs"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "1 matches in 1 files" in captured.err
    assert "notes.md" in captured.out
    assert "a.py" not in captured.out


def test_absence_is_zero_and_still_exits_clean(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Zero on a fresh index is a usable answer, not a failure."""
    rc = main(["count", "nothinglikethis", "--root", str(root)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "0 matches in 0 files" in captured.err
    assert captured.out == ""


def test_an_unparseable_query_exits_two_not_zero(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The distinction `count` exists to make: nothing found vs did not search.

    A zero count is only usable as proof of absence if a broken query cannot
    also produce one.
    """
    rc = main(["count", "fts5:unbalanced (paren", "--root", str(root)])
    captured = capsys.readouterr()

    assert rc == 2
    assert "query error" in captured.err
    assert "0 matches" not in captured.err


def test_count_reads_the_index_at_index_dir(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["index", "--root", str(root), "--index-dir", "custom_idx"])
    capsys.readouterr()

    rc = main(["count", "needle_token", "--root", str(root), "--index-dir", "custom_idx"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "reindexed" not in captured.err
