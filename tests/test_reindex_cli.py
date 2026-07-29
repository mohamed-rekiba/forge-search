"""The `reindex` subcommand, and the time gate that makes it cheap to call.

Reindexing already existed as an MCP tool and as `index.reindex()`; what was
missing was a CLI verb and a way to call it unconditionally without paying for
it. apexyard's session-start hook wants exactly that: run on every session,
cost nothing when the index was refreshed recently.

`--if-stale` is a *time* gate, deliberately distinct from the content staleness
`reindex()` already detects. The content check has to walk the whole tree to
know the answer, so it is the wrong tool for "should I bother?" — the time gate
answers from one row of metadata.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from forge_search.cli import main
from forge_search.core.cache import index_path_for
from forge_search.core.index import LexicalIndex
from forge_search.core.roots import Root


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    (r / "src" / "auth.py").write_text("def validate_token():\n    pass\n")
    return r


class TestSubcommandExists:
    def test_reindex_runs(self, root: Path) -> None:
        assert main(["reindex", "--root", str(root)]) == 0

    def test_reindex_builds_the_index(self, root: Path) -> None:
        main(["reindex", "--root", str(root)])
        assert index_path_for("proj", root, None).exists()

    def test_incremental_is_accepted(self, root: Path) -> None:
        """A no-op alias for the existing default.

        `reindex()` is already incremental — only files whose size or mtime
        changed are re-read. The flag exists so the documented hook invocation
        works verbatim; it must not be wired to anything.
        """
        assert main(["reindex", "--root", str(root), "--incremental"]) == 0

    def test_force_is_accepted_for_parity_with_index(self, root: Path) -> None:
        main(["reindex", "--root", str(root)])
        assert main(["reindex", "--root", str(root), "--force"]) == 0


class TestLastRefreshedStamp:
    """The metadata the time gate reads."""

    def test_reindex_records_when_it_ran(self, root: Path) -> None:
        before = time.time()
        main(["reindex", "--root", str(root)])

        with LexicalIndex(Root(label="proj", path=root)) as index:
            stamped = index.last_refreshed_at()

        assert stamped is not None, "reindex left no timestamp for --if-stale to read"
        assert stamped >= before - 1

    def test_stamp_is_updated_even_when_nothing_changed(self, root: Path) -> None:
        """ "Checked at T and it was current" is what the window means.

        Stamping only on actual work would mean a repeatedly-current index never
        gets a fresh timestamp, so every call would re-walk the tree — exactly
        the cost the gate exists to avoid.
        """
        main(["reindex", "--root", str(root)])
        with LexicalIndex(Root(label="proj", path=root)) as index:
            first = index.last_refreshed_at()

        time.sleep(1.1)
        main(["reindex", "--root", str(root)])
        with LexicalIndex(Root(label="proj", path=root)) as index:
            second = index.last_refreshed_at()

        assert first is not None and second is not None
        assert second > first, "an up-to-date reindex did not refresh the timestamp"

    def test_absent_stamp_reads_as_none(self, root: Path) -> None:
        """An index built before this feature has no stamp. It must not crash,
        and it must not read as 'refreshed at the epoch' either — None lets the
        caller decide, which is treating it as due."""
        with LexicalIndex(Root(label="proj", path=root)) as index:
            assert index.last_refreshed_at() is None


class TestIfStaleGate:
    def test_skips_when_inside_the_window(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["reindex", "--root", str(root)])
        capsys.readouterr()

        assert main(["reindex", "--root", str(root), "--if-stale", "86400"]) == 0
        out = capsys.readouterr().out.lower()

        assert "skip" in out or "fresh" in out, (
            "the gate should say it did nothing, not stay silent — a hook that "
            "prints nothing is indistinguishable from one that failed"
        )

    def test_does_no_work_when_inside_the_window(self, root: Path) -> None:
        """The point of the gate: not merely a fast answer, but no work."""
        main(["reindex", "--root", str(root)])
        db = index_path_for("proj", root, None)
        before = db.stat().st_mtime_ns

        # Add a file. Content IS stale now, but the time window has not elapsed,
        # so the gate must still skip — that is the trade the flag makes.
        (root / "src" / "new.py").write_text("def added():\n    pass\n")
        main(["reindex", "--root", str(root), "--if-stale", "86400"])

        assert db.stat().st_mtime_ns == before, (
            "reindex touched the index inside the --if-stale window"
        )

    def test_reindexes_when_the_window_has_elapsed(self, root: Path) -> None:
        main(["reindex", "--root", str(root)])
        (root / "src" / "new.py").write_text("def added():\n    pass\n")

        # A zero-second window means "always due".
        assert main(["reindex", "--root", str(root), "--if-stale", "0"]) == 0

        with LexicalIndex(Root(label="proj", path=root)) as index:
            assert index.indexed_count() == 2, "the elapsed window did not reindex"

    def test_unstamped_index_is_treated_as_due(self, root: Path) -> None:
        """An index predating the stamp must be refreshed, not skipped forever."""
        main(["index", "--root", str(root)])  # builds without a reindex stamp path
        with LexicalIndex(Root(label="proj", path=root)) as index:
            index.clear_refresh_stamp()

        (root / "src" / "new.py").write_text("def added():\n    pass\n")
        main(["reindex", "--root", str(root), "--if-stale", "86400"])

        with LexicalIndex(Root(label="proj", path=root)) as index:
            assert index.indexed_count() == 2, (
                "an unstamped index was skipped, so it could never be refreshed"
            )

    def test_no_index_at_all_is_treated_as_due(self, root: Path) -> None:
        assert main(["reindex", "--root", str(root), "--if-stale", "86400"]) == 0
        assert index_path_for("proj", root, None).exists(), (
            "a root with no index was skipped by the time gate"
        )
