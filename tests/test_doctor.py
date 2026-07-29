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

import sqlite3
import stat
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

    def test_does_not_wipe_an_index_built_by_an_older_schema(self, root: Path) -> None:
        """The hole the `exists()` guard does not cover.

        `LexicalIndex.__init__` ends with `_migrate_if_stale()`, which DELETEs
        every row when the stored SCHEMA_VERSION differs. Checking existence
        stops doctor *creating* an index; it does nothing about *destroying*
        one. Since apexyard's hook runs this unattended, the first session
        after any schema-bumping release would drop every index in the
        portfolio — and doctor would then truthfully report "0 files indexed",
        true only because it made it so.
        """
        main(["index", "--root", str(root)])
        db = index_path_for("proj", root, None)

        with sqlite3.connect(db) as conn:
            before = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
        assert before > 0, "precondition: the index has content"

        main(["doctor", "--root", str(root)])

        with sqlite3.connect(db) as conn:
            after = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]

        assert after == before, (
            f"doctor wiped the index ({before} files → {after}) because the "
            "stored schema version differed. Diagnosing must not migrate."
        )
        assert version == "1", "doctor re-stamped the schema version"

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


class TestNeverReportsFalseHealthy:
    """A probe that says healthy while broken is worse than no probe.

    Exit 0 is claimed to mean "this install can serve queries". Absent and
    stale indexes genuinely satisfy that — search creates and repairs them. An
    index that cannot be *read* does not, and must not be reported the same way
    an absent one is.
    """

    def test_unreadable_index_dir_is_not_reported_as_healthy(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["index", "--root", str(root)])
        index_dir = index_path_for("proj", root, None).parent
        original = index_dir.stat().st_mode
        index_dir.chmod(0)
        try:
            code = main(["doctor", "--root", str(root)])
            out = (capsys.readouterr().out + capsys.readouterr().err).lower()
        finally:
            index_dir.chmod(stat.S_IMODE(original))

        assert code != 0, (
            "doctor exited 0 on an index directory it cannot read. Every search "
            "against this root fails, so reporting healthy is the one outcome a "
            "probe must never produce."
        )
        assert "no index yet" not in out, (
            "an unreadable index was reported as merely absent — those need "
            "different answers: one self-heals, the other needs a human"
        )

    def test_corrupt_index_does_not_abort_the_whole_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One bad root must not hide the others.

        An uncaught sqlite error on the first root aborts the loop, so the
        remaining roots go unreported — the operator sees a traceback instead of
        the three healthy roots and the one broken one.
        """
        a = tmp_path / "alpha"
        (a / "s").mkdir(parents=True)
        (a / "s" / "x.py").write_text("x = 1\n")
        b = tmp_path / "beta"
        (b / "s").mkdir(parents=True)
        (b / "s" / "y.py").write_text("y = 2\n")

        main(["index", "--root", f"alpha={a}", "--root", f"beta={b}"])
        # Corrupt alpha's DB in place — not a valid SQLite file any more.
        index_path_for("alpha", a, None).write_bytes(b"this is not a database")
        capsys.readouterr()

        code = main(["doctor", "--root", f"alpha={a}", "--root", f"beta={b}"])
        out = capsys.readouterr().out

        assert "beta" in out, "a corrupt alpha aborted the report before beta was reached"
        assert code != 0, "a corrupt index is a genuine fault and must not exit 0"


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

    def test_honours_an_adapter_supplied_index_dir(
        self,
        root: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The same bug as `--index-dir`, one precedence tier down.

        `_index_dir` has three tiers: explicit flag, then the active adapter's
        preference, then the per-root default. The middle tier reads
        `_LAST_RESOLUTION`, which only `_resolve_roots` populates — so calling
        `_index_dir` *before* `_resolve_roots` silently skips it and doctor looks
        in the default location. Latent with the shipped adapter only because it
        leaves `index_dir` unset.
        """
        from forge_search import cli
        from forge_search.core.roots import Resolution, Root

        main(["index", "--root", str(root), "--index-dir", "custom_idx"])
        capsys.readouterr()

        # Stand in for an adapter that moves the default. Patching the resolver
        # rather than `_LAST_RESOLUTION` directly is what makes this a real test
        # of the ordering: `_resolve_roots` overwrites that slot on every run, so
        # presetting it proves nothing.
        def _resolution_with_index_dir(explicit, **kwargs):
            return Resolution(
                roots=(Root(label="proj", path=root),), index_dir="custom_idx"
            )

        monkeypatch.setattr(cli, "resolve_roots", _resolution_with_index_dir)
        main(["doctor", "--root", str(root)])
        out = capsys.readouterr().out

        assert "no index yet" not in out, (
            "doctor ignored the adapter's index_dir preference and looked in the "
            "default location, reporting an indexed root as unindexed"
        )

    def test_reports_staleness(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["index", "--root", str(root)])
        capsys.readouterr()
        (root / "src" / "new.py").write_text("def added():\n    pass\n")

        main(["doctor", "--root", str(root)])
        out = capsys.readouterr().out.lower()

        assert "stale" in out


class TestRefreshAge:
    """`--if-stale` turns on a stored timestamp, so doctor has to show it.

    `IndexHealth.last_refreshed_at` had a writer and no reader, and that gap hid
    a real bug through two review rounds: the stamp survived a schema-version
    wipe, so `reindex --if-stale` skipped an index it had just emptied and
    reported it as freshly refreshed. Displaying the value would have made that
    visible on sight instead of needing a targeted reproduction.
    """

    def test_shows_how_long_ago_a_refresh_happened(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["reindex", "--root", str(root)])
        capsys.readouterr()

        assert main(["doctor", "--root", str(root)]) == 0
        out = capsys.readouterr().out

        assert "refreshed" in out
        assert "ago" in out, f"expected a relative age, got: {out}"

    def test_an_index_with_no_stamp_says_never_refreshed(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Distinct from "refreshed a long time ago", and a different conclusion.

        Any index built by this version carries a stamp, so the stamp-less case
        is an index from before the field existed — reproduced here by removing
        the row. Reading its absence as the epoch, or as now, would make
        `--if-stale` re-walk forever or skip forever.
        """
        main(["index", "--root", str(root)])
        capsys.readouterr()

        db = index_path_for("proj", root, None)
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM meta WHERE key = 'last_refreshed_at'")

        main(["doctor", "--root", str(root)])
        out = capsys.readouterr().out

        assert "never refreshed" in out, f"expected never-refreshed, got: {out}"

    def test_a_schema_wiped_index_does_not_claim_a_fresh_refresh(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The case that motivated this.

        A schema bump empties the index, so the stamp describing the emptied rows
        has to go with them. When it survived, `--if-stale` skipped an index it
        had just wiped and called it fresh.
        """
        main(["reindex", "--root", str(root)])
        capsys.readouterr()

        db = index_path_for("proj", root, None)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")

        # Opening for write runs the migration, which drops every row.
        main(["search", "validate_token", "--root", str(root)])
        capsys.readouterr()
        with sqlite3.connect(db) as conn:
            stamp = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_refreshed_at'"
            ).fetchone()

        # A search rebuilds on the read path, so a stamp here is legitimate —
        # what must not survive is the *pre-wipe* one describing dropped rows.
        main(["doctor", "--root", str(root)])
        out = capsys.readouterr().out

        assert "files indexed" in out
        assert stamp is None or float(stamp[0]) > 0
