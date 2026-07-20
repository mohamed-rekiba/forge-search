"""Regression tests for the four search defects.

Each test reproduces a real measured failure. They are written to fail if the
defaults ever regress to the naive behaviour — which matters more than usual
here, because every one of these defects is *silent*: the naive version
returns zero results rather than an error, so nothing else would catch it.
"""

from __future__ import annotations

from pathlib import Path

from forge_search.core.traversal import TraversalConfig, excerpt, walk


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class TestHiddenDirectories:
    """Defect 1: hidden directories skipped by default hid 20 of 44 files."""

    def test_hidden_directory_is_traversed_by_default(self, tmp_path: Path) -> None:
        _write(tmp_path / ".config" / "hook.sh", "content")
        _write(tmp_path / "src" / "main.py", "content")

        found = {p.name for p in walk(tmp_path)}

        assert found == {"hook.sh", "main.py"}, (
            "hidden directories must be traversed by default — this is the "
            "defect that hid over half a real repository"
        )

    def test_hidden_can_be_disabled_explicitly(self, tmp_path: Path) -> None:
        _write(tmp_path / ".config" / "hook.sh")
        _write(tmp_path / "src" / "main.py")

        found = {p.name for p in walk(tmp_path, TraversalConfig(include_hidden=False))}

        assert found == {"main.py"}


class TestNestedIgnores:
    """Defect 2's inverse: overriding ignore rules wholesale re-surfaces junk.

    The measured contrast was 0 junk hits versus 8. Dependency and build
    directories stay excluded even though the root itself is always traversed.
    """

    def test_dependency_directories_are_excluded(self, tmp_path: Path) -> None:
        _write(tmp_path / "node_modules" / "dep" / "index.js")
        _write(tmp_path / ".venv" / "lib" / "mod.py")
        _write(tmp_path / "dist" / "bundle.js")
        _write(tmp_path / "src" / "real.py")

        found = {p.name for p in walk(tmp_path)}

        assert found == {"real.py"}, f"build/dependency output leaked in: {found}"

    def test_root_is_traversed_even_when_dotted(self, tmp_path: Path) -> None:
        """A configured root is traversed on its own authority.

        Defect 2 proper: an ignore rule *above* a root must not be able to
        veto a tree the operator explicitly named.
        """
        root = tmp_path / ".hidden-workspace" / "project"
        _write(root / "src" / "app.py")

        found = {p.name for p in walk(root)}

        assert found == {"app.py"}


class TestFileFiltering:
    def test_binary_and_unknown_suffixes_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path / "image.png")
        _write(tmp_path / "archive.tar.gz")
        _write(tmp_path / "notes.md")

        assert {p.name for p in walk(tmp_path)} == {"notes.md"}

    def test_oversized_files_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path / "small.py", "x" * 10)
        _write(tmp_path / "generated.py", "x" * 5_000)

        config = TraversalConfig(max_file_bytes=1_000)

        assert {p.name for p in walk(tmp_path, config)} == {"small.py"}


class TestExcerpt:
    """Defect 3: truncation must preserve the match, not discard the line."""

    def test_short_line_returned_whole(self) -> None:
        assert excerpt("short line", 0, 5) == "short line"

    def test_long_line_keeps_the_match(self) -> None:
        needle = "NEEDLE"
        line = ("x" * 500) + needle + ("y" * 500)
        result = excerpt(line, 500, len(needle), width=150)

        assert needle in result, "truncation dropped the matched text — the defect"
        assert len(result) <= 152  # width plus at most two ellipses

    def test_match_at_start_is_not_lost(self) -> None:
        line = "NEEDLE" + ("y" * 500)
        assert "NEEDLE" in excerpt(line, 0, 6, width=150)

    def test_match_at_end_is_not_lost(self) -> None:
        line = ("x" * 500) + "NEEDLE"
        assert "NEEDLE" in excerpt(line, 500, 6, width=150)

    def test_ellipsis_marks_trimmed_sides(self) -> None:
        line = ("x" * 500) + "NEEDLE" + ("y" * 500)
        result = excerpt(line, 500, 6, width=150)

        assert result.startswith("…") and result.endswith("…"), (
            "a trimmed excerpt must be visibly partial, or it reads as a whole line"
        )

    def test_match_wider_than_window_is_still_preserved(self) -> None:
        """Truncating the match itself would reintroduce the defect."""
        needle = "N" * 300
        line = "prefix" + needle + "suffix"
        result = excerpt(line, 6, len(needle), width=150)

        assert "N" * 150 in result


class TestRootNormalisation:
    """A Root must be usable however it was constructed.

    Found by a clean-machine install test: a Root built from a RELATIVE path
    broke every search with "not in the subpath of". Traversal resolves the
    root internally and yields absolute paths, while `relative_to()` compared
    against the unresolved original. Every in-repo fixture uses `tmp_path`,
    which is already absolute — so no existing test could reach this.
    """

    def test_relative_path_is_resolved(self, tmp_path: Path, monkeypatch) -> None:
        from forge_search.core.roots import Root

        (tmp_path / "proj").mkdir()
        monkeypatch.chdir(tmp_path)

        root = Root(label="proj", path=Path("proj"))

        assert root.path.is_absolute()
        assert root.path == (tmp_path / "proj").resolve()

    def test_search_works_from_a_relative_root(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """End-to-end: the failure the clean-install test actually hit."""
        from forge_search.core.index import LexicalIndex
        from forge_search.core.roots import Root

        monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))
        (tmp_path / "proj").mkdir()
        (tmp_path / "proj" / "app.py").write_text("def charge_customer():\n    pass\n")
        monkeypatch.chdir(tmp_path)

        with LexicalIndex(Root(label="proj", path=Path("proj"))) as idx:
            idx.reindex()
            hits = idx.search("charge_customer")

        assert hits and hits[0].path == "app.py"

    def test_user_home_is_expanded(self, tmp_path: Path, monkeypatch) -> None:
        from forge_search.core.roots import Root

        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "code").mkdir()

        assert (
            Root(label="code", path=Path("~/code")).path
            == (tmp_path / "code").resolve()
        )
