"""The `doctor` subcommand — a health probe safe to run unconditionally.

It exists because callers need to distinguish "this install is broken" from
"this index needs rebuilding", and an exit code is the only signal a shell hook
can read. apexyard's session-start hook probes with `doctor` for exactly that
reason.

The load-bearing property is that it changes nothing. `LexicalIndex.__init__`
creates its directory and connects, so the naive implementation — construct one
per root and ask it questions — would create an empty index for every
unindexed root as a side effect of *diagnosing* it. A probe that alters what it
measures is worse than no probe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.cli import main
from forge_search.core.cache import index_path_for


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    (r / "src" / "auth.py").write_text("def validate_token():\n    pass\n")
    (r / "guide.md").write_text("How to validate_token.\n")
    return r


class TestExitCode:
    """Exit 0 on a healthy install — that is the whole contract for a probe."""

    def test_exits_zero_on_a_healthy_install(self, root: Path) -> None:
        assert main(["doctor", "--root", str(root)]) == 0

    def test_exits_zero_even_when_no_index_exists_yet(self, root: Path) -> None:
        """An unindexed root is not a broken install, it is a cold start."""
        assert main(["doctor", "--root", str(root)]) == 0

    def test_exits_zero_when_the_index_is_stale(self, root: Path) -> None:
        main(["index", "--root", str(root)])
        (root / "src" / "new.py").write_text("def added():\n    pass\n")
        assert main(["doctor", "--root", str(root)]) == 0, (
            "a stale index is a normal state that search self-heals; doctor "
            "reports it without claiming the install is broken"
        )


class TestSideEffectFree:
    """The property that makes it safe to run on every session start."""

    def test_does_not_create_an_index_for_an_unindexed_root(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db = index_path_for("proj", root, None)
        assert not db.exists(), "precondition: no index yet"

        main(["doctor", "--root", str(root)])

        assert not db.exists(), (
            f"doctor created {db} just by inspecting the root. LexicalIndex's "
            "constructor calls ensure_index_dir + connect, so it must not be "
            "constructed for a root whose index is absent."
        )

    def test_does_not_reindex_a_stale_index(self, root: Path) -> None:
        main(["index", "--root", str(root)])
        db = index_path_for("proj", root, None)
        before = db.stat().st_mtime_ns

        (root / "src" / "new.py").write_text("def added():\n    pass\n")
        main(["doctor", "--root", str(root)])

        assert db.stat().st_mtime_ns == before, (
            "doctor reindexed a stale index. Diagnosing must not repair — the "
            "caller asked what the state is, not to change it."
        )


class TestReport:
    """What it prints. A probe nobody can read is only half useful."""

    def test_names_every_resolved_root(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        a = tmp_path / "alpha"
        (a / "s").mkdir(parents=True)
        (a / "s" / "x.py").write_text("x = 1\n")
        b = tmp_path / "beta"
        b.mkdir()
        (b / "y.py").write_text("y = 2\n")

        main(["doctor", "--root", f"alpha={a}", "--root", f"beta={b}"])
        out = capsys.readouterr().out

        assert "alpha" in out and "beta" in out

    def test_reports_an_absent_index_distinctly_from_a_present_one(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["doctor", "--root", str(root)])
        absent = capsys.readouterr().out

        main(["index", "--root", str(root)])
        capsys.readouterr()
        main(["doctor", "--root", str(root)])
        present = capsys.readouterr().out

        assert absent != present, (
            "an indexed and an unindexed root must not read identically — "
            "telling them apart is the first thing anyone runs this for"
        )

    def test_reports_whether_the_semantic_arm_is_available(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["doctor", "--root", str(root)])
        out = capsys.readouterr().out.lower()
        assert "semantic" in out

    def test_honours_index_dir(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Must look where the index actually is.

        Without `--index-dir`, doctor resolves the default location and reports
        "no index yet" for a root that is correctly indexed elsewhere — a
        diagnosis that is confidently wrong, which is worse than none.
        """
        main(["index", "--root", str(root), "--index-dir", ".forge_search"])
        capsys.readouterr()

        assert main(["doctor", "--root", str(root), "--index-dir", ".forge_search"]) == 0
        out = capsys.readouterr().out

        assert "no index yet" not in out, (
            "doctor looked in the default location instead of --index-dir, so "
            "it reported an indexed root as unindexed"
        )
        assert "files indexed" in out

    def test_reports_staleness(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["index", "--root", str(root)])
        capsys.readouterr()
        (root / "src" / "new.py").write_text("def added():\n    pass\n")

        main(["doctor", "--root", str(root)])
        out = capsys.readouterr().out.lower()

        assert "stale" in out
