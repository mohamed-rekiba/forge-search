"""`--sub-projects` — expanding a root into the checkouts nested inside it.

The caller states which directories under a root hold other projects, rather
than a component reading a portfolio's own config files to infer the same
thing, as a retired seam once did.
`resolve_roots` is exercised directly here — the same seam the CLI's
`--sub-projects` flag drives (see `cli._parse_sub_projects` for the
comma-split, done once at the CLI boundary rather than in core).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.cli import _parse_sub_projects
from forge_search.core.roots import Root, resolve_roots


def _checkout(base: Path, name: str, *, worktree: bool = False) -> Path:
    """A minimal checkout: a source file plus a `.git` marking it as one.

    `worktree=True` writes `.git` as a *file* — a `gitdir:` pointer to the
    real repository's `worktrees/` directory — which is what `git worktree
    add` actually produces. A plain clone's `.git` is a directory instead.
    """
    project = base / name
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text("x = 1\n")
    if worktree:
        (project / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    else:
        (project / ".git").mkdir()
    return project


class TestNoSubProjects:
    """The flag is additive. Without it, nothing about root resolution changes."""

    def test_roots_used_exactly_as_named(self, tmp_path: Path) -> None:
        given = [Root(label="ws", path=tmp_path)]
        result = resolve_roots(given)
        assert [r.label for r in result.roots] == ["ws"]
        assert result.expansions == ()


class TestExpansion:
    def test_root_plus_each_checkout_is_indexed(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        _checkout(workspace, "alpha")
        _checkout(workspace, "beta")

        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["workspace"]
        )

        assert {r.label for r in result.roots} == {"ws", "alpha", "beta"}

    def test_multiple_comma_separated_dirs(self, tmp_path: Path) -> None:
        # The CLI splits "packages,apps" into this list before calling in;
        # resolve_roots itself just takes the sequence.
        _checkout(tmp_path / "packages", "pkg-a")
        _checkout(tmp_path / "apps", "app-a")

        result = resolve_roots(
            [Root(label="ws", path=tmp_path)],
            sub_projects=["packages", "apps"],
        )

        assert {r.label for r in result.roots} == {"ws", "pkg-a", "app-a"}

    def test_relative_dir_outside_the_root(self, tmp_path: Path) -> None:
        """The split-portfolio case: the workspace lives in a sibling
        directory, reached with `..`, rather than under the named root."""
        root = tmp_path / "ops"
        root.mkdir()
        _checkout(tmp_path / "elsewhere" / "workspace", "gamma")

        result = resolve_roots(
            [Root(label="ops", path=root)],
            sub_projects=["../elsewhere/workspace"],
        )

        assert {r.label for r in result.roots} == {"ops", "gamma"}

    def test_entry_without_git_is_not_indexed(self, tmp_path: Path) -> None:
        (tmp_path / "workspace" / "not-a-checkout").mkdir(parents=True)

        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["workspace"]
        )

        assert [r.label for r in result.roots] == ["ws"]

    def test_worktree_is_indexed(self, tmp_path: Path) -> None:
        """The check that matters: existence of `.git`, not directory-ness.

        A worktree's `.git` is a file. `is_dir()` would silently exclude
        every worktree checkout while accepting every plain clone — a
        one-sided failure invisible until someone actually uses a worktree.
        """
        _checkout(tmp_path / "workspace", "wt", worktree=True)

        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["workspace"]
        )

        assert {r.label for r in result.roots} == {"ws", "wt"}

    def test_hidden_entries_and_plain_files_are_skipped(self, tmp_path: Path) -> None:
        _checkout(tmp_path / "workspace", ".hidden-checkout")
        (tmp_path / "workspace" / "README.md").write_text("not a directory\n")

        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["workspace"]
        )

        assert [r.label for r in result.roots] == ["ws"]

    def test_missing_sub_projects_dir_is_not_an_error(self, tmp_path: Path) -> None:
        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["does-not-exist"]
        )

        assert [r.label for r in result.roots] == ["ws"]

    def test_entries_are_sorted(self, tmp_path: Path) -> None:
        _checkout(tmp_path / "workspace", "zeta")
        _checkout(tmp_path / "workspace", "alpha")
        _checkout(tmp_path / "workspace", "mu")

        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["workspace"]
        )

        assert [r.label for r in result.roots] == ["ws", "alpha", "mu", "zeta"]


class TestReporting:
    def test_expansion_is_reported(self, tmp_path: Path) -> None:
        _checkout(tmp_path / "workspace", "alpha")

        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["workspace"]
        )

        assert "ws expanded into 1 sub-project(s)" in result.describe()

    def test_a_no_op_expansion_is_not_reported(self, tmp_path: Path) -> None:
        # "expanded into 0" describes a non-event, and on a stdio MCP server
        # every stderr line is rendered as a warning by the client.
        result = resolve_roots(
            [Root(label="ws", path=tmp_path)], sub_projects=["does-not-exist"]
        )

        assert result.expansions == ()
        assert "expanded" not in result.describe()

    def test_multiple_roots_report_independently(self, tmp_path: Path) -> None:
        _checkout(tmp_path / "a" / "workspace", "one")
        (tmp_path / "b").mkdir()

        result = resolve_roots(
            [Root(label="a", path=tmp_path / "a"), Root(label="b", path=tmp_path / "b")],
            sub_projects=["workspace"],
        )

        assert {r.label for r in result.roots} == {"a", "b", "one"}
        assert result.expansions == (("a", 1),)


class TestDuplicateLabels:
    """Labels key the indexes, so a collision silently loses a project.

    Reproduced on the first cut of this feature: `packages,apps` with a `foo`
    under each resolved both, printed both at startup, and served one — the
    second reused the first's index and its own content was unreachable.
    """

    def test_same_name_under_two_dirs_raises(self, tmp_path: Path) -> None:
        _checkout(tmp_path / "root" / "packages", "foo")
        _checkout(tmp_path / "root" / "apps", "foo")

        with pytest.raises(ValueError, match="duplicate root label 'foo'"):
            resolve_roots(
                [Root(label="r", path=tmp_path / "root")],
                sub_projects=["packages", "apps"],
            )

    def test_the_error_names_both_paths(self, tmp_path: Path) -> None:
        """ "foo is ambiguous" without them just sends the reader looking."""
        _checkout(tmp_path / "root" / "packages", "foo")
        _checkout(tmp_path / "root" / "apps", "foo")

        with pytest.raises(ValueError) as excinfo:
            resolve_roots(
                [Root(label="r", path=tmp_path / "root")],
                sub_projects=["packages", "apps"],
            )

        message = str(excinfo.value)
        assert "packages/foo" in message
        assert "apps/foo" in message

    def test_a_checkout_colliding_with_its_own_root_raises(self, tmp_path: Path) -> None:
        _checkout(tmp_path / "root" / "workspace", "r")

        with pytest.raises(ValueError, match="duplicate root label 'r'"):
            resolve_roots(
                [Root(label="r", path=tmp_path / "root")], sub_projects=["workspace"]
            )

    def test_the_same_directory_twice_is_repetition_not_ambiguity(
        self, tmp_path: Path
    ) -> None:
        """One right answer, so take it once rather than raising."""
        _checkout(tmp_path / "root" / "workspace", "one")

        result = resolve_roots(
            [Root(label="r", path=tmp_path / "root")],
            sub_projects=["workspace", "workspace", "./workspace"],
        )

        assert [r.label for r in result.roots] == ["r", "one"]
        assert result.expansions == (("r", 1),)


class TestParseSubProjects:
    """The comma-split had no coverage at all.

    Mutation testing found it: deleting the split entirely left every test
    passing, because the one named for it passed an already-split list. That
    gap is exactly where the whitespace bug got in.
    """

    def test_splits_on_commas(self) -> None:
        assert _parse_sub_projects("packages,apps") == ["packages", "apps"]

    def test_strips_whitespace_around_segments(self) -> None:
        """`--sub-projects "packages, apps"` is what a person writes.

        Unstripped, " apps" resolved to nothing and was skipped — while the
        startup line echoed the raw string back, reporting both and indexing
        one.
        """
        assert _parse_sub_projects("packages, apps") == ["packages", "apps"]
        assert _parse_sub_projects(" packages ,\tapps\n") == ["packages", "apps"]

    def test_drops_empty_segments(self) -> None:
        assert _parse_sub_projects("packages,,apps,") == ["packages", "apps"]
        assert _parse_sub_projects(" , ") == []

    def test_absent_or_empty_yields_nothing(self) -> None:
        assert _parse_sub_projects(None) == []
        assert _parse_sub_projects("") == []

    def test_a_single_value_needs_no_comma(self) -> None:
        assert _parse_sub_projects("workspace") == ["workspace"]
