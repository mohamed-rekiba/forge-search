"""`search` on the read path — specifically, that it looks where it is told.

Every command that touches an index takes `--index-dir`. `search` accepted the
flag and ignored it, so a caller who moved indexes out of their tree got one
built back inside it on the next search. Nothing in the suite covered the flag
on this command, which is why it survived.
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
    (r / "auth.py").write_text("def validate_token():\n    pass\n")
    return r


def test_search_reads_the_index_at_index_dir(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["index", "--root", str(root), "--index-dir", "custom_idx"])
    capsys.readouterr()

    rc = main(
        ["search", "validate_token", "--root", str(root), "--index-dir", "custom_idx"]
    )
    captured = capsys.readouterr()

    assert rc == 0
    assert "auth.py" in captured.out
    # The rebuild notice goes to stderr. Its absence is what says the existing
    # index was read; a search that ignores the flag finds the hit either way,
    # so the hit alone proves nothing.
    assert "reindexed" not in captured.err, (
        "search rebuilt from scratch instead of reading the index at --index-dir"
    )


def test_search_does_not_write_an_index_the_caller_opted_out_of(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The consequence that makes this more than a wasted rebuild.

    Moving indexes out of the tree is the reason `--index-dir` exists. Ignoring
    it on search puts one back, into a directory the caller asked to keep clean.
    """
    main(["index", "--root", str(root), "--index-dir", "custom_idx"])
    main(["search", "validate_token", "--root", str(root), "--index-dir", "custom_idx"])
    capsys.readouterr()

    assert (root / "custom_idx").is_dir()
    assert not (root / ".forge_search").exists(), (
        "search wrote a second index at the default location"
    )
