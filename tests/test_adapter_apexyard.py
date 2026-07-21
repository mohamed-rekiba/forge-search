"""The ApexYard adapter — and, through it, the core/adapter seam.

The last class is the one that matters most: it checks the *architecture*
claim, not the adapter. If the core ever grows knowledge of ApexYard, the
standalone property becomes a README assertion rather than a fact, and these
tests are what would catch that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from forge_search.adapters.apexyard import MARKER, ApexyardRootProvider
from forge_search.core.roots import Root, RootProvider, resolve_roots


def _make_fork(
    base: Path, projects: list[str], *, clone: list[str] | None = None
) -> Path:
    """Build a minimal ops fork with a registry and optional clones."""
    fork = base / "ops"
    (fork / "workspace").mkdir(parents=True)
    (fork / MARKER).write_text("# marks an ApexYard ops fork\n")

    entries = "\n".join(
        f"  - name: {p}\n    repo: org/{p}\n    status: active" for p in projects
    )
    (fork / "apexyard.projects.yaml").write_text(f"version: 1\nprojects:\n{entries}\n")

    for name in clone if clone is not None else projects:
        (fork / "workspace" / name / "src").mkdir(parents=True)
        (fork / "workspace" / name / "src" / "main.py").write_text("x = 1\n")

    return fork


class TestExplicitSelection:
    """Adapters are named, never detected.

    Detection was removed deliberately: an adapter that fires because it
    spotted a marker file can fire when unwanted and fail to fire when wanted,
    both silently.
    """

    def test_no_adapter_named_leaves_roots_alone(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha"])
        given = [Root(label="ws", path=fork)]

        result = resolve_roots(given, adapters=[ApexyardRootProvider()])

        assert [r.label for r in result.roots] == ["ws"], (
            "a marker file present in the tree must NOT auto-enable an adapter"
        )

    def test_named_adapter_expands(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha", "beta"])
        given = [Root(label="ws", path=fork)]

        result = resolve_roots(
            given, adapters=[ApexyardRootProvider()], adapter_name="apexyard"
        )

        assert {r.label for r in result.roots} == {"ws", "alpha", "beta"}

    def test_unknown_adapter_name_raises(self, tmp_path: Path) -> None:
        """A typo must fail loudly rather than silently searching unexpanded."""
        given = [Root(label="ws", path=tmp_path)]
        with pytest.raises(ValueError, match="unknown adapter"):
            resolve_roots(
                given, adapters=[ApexyardRootProvider()], adapter_name="apexyrd"
            )

    def test_expansion_is_reported(self, tmp_path: Path) -> None:
        """Expanding one root into several is more than was literally asked
        for, so it is announced rather than done quietly."""
        fork = _make_fork(tmp_path, ["alpha"])
        result = resolve_roots(
            [Root(label="ws", path=fork)],
            adapters=[ApexyardRootProvider()],
            adapter_name="apexyard",
        )

        assert "apexyard expanded ws into 2" in result.describe()


class TestRoots:
    def test_includes_the_ops_fork_and_each_clone(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha", "beta"])
        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert {r.label for r in roots} == {"ws", "alpha", "beta"}

    def test_registered_project_without_a_clone_is_skipped(self, tmp_path: Path) -> None:
        """A partially-cloned portfolio is normal, not an error state."""
        fork = _make_fork(tmp_path, ["alpha", "beta"], clone=["alpha"])
        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert {r.label for r in roots} == {"ws", "alpha"}

    def test_empty_registry_still_yields_the_fork(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, [])
        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert [r.label for r in roots] == ["ws"]

    def test_missing_registry_still_yields_the_fork(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha"])
        (fork / "apexyard.projects.yaml").unlink()
        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert [r.label for r in roots] == ["ws"]

    def test_quoted_names_parse(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, [])
        (fork / "apexyard.projects.yaml").write_text(
            'version: 1\nprojects:\n  - name: "quoted-proj"\n    repo: org/x\n'
        )
        (fork / "workspace" / "quoted-proj").mkdir(parents=True)
        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert "quoted-proj" in {r.label for r in roots}

    def test_path_traversal_in_a_name_is_rejected(self, tmp_path: Path) -> None:
        """A malicious or malformed name must not select a tree outside the
        workspace — a wrong root returns confident results from the wrong place."""
        fork = _make_fork(tmp_path, [])
        (fork / "apexyard.projects.yaml").write_text(
            "version: 1\nprojects:\n  - name: ../../etc\n  - name: ..\n"
        )
        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert [r.label for r in roots] == ["ws"]


class TestSplitPortfolio:
    def test_registry_and_workspace_can_live_in_a_sibling_repo(
        self, tmp_path: Path
    ) -> None:
        fork = tmp_path / "ops"
        fork.mkdir()
        (fork / MARKER).write_text("")
        (fork / ".claude").mkdir()

        sibling = tmp_path / "portfolio"
        (sibling / "workspace" / "gamma").mkdir(parents=True)
        (sibling / "apexyard.projects.yaml").write_text(
            "version: 1\nprojects:\n  - name: gamma\n    repo: org/gamma\n"
        )

        (fork / ".claude" / "project-config.json").write_text(
            json.dumps(
                {
                    "portfolio": {
                        "registry": "../portfolio/apexyard.projects.yaml",
                        "workspace_dir": "../portfolio/workspace",
                    }
                }
            )
        )

        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert {r.label for r in roots} == {"ws", "gamma"}

    def test_malformed_config_falls_back_instead_of_crashing(
        self, tmp_path: Path
    ) -> None:
        fork = _make_fork(tmp_path, ["alpha"])
        (fork / ".claude").mkdir(exist_ok=True)
        (fork / ".claude" / "project-config.json").write_text("{not valid json")

        roots = ApexyardRootProvider().expand(Root(label="ws", path=fork))
        assert {r.label for r in roots} == {"ws", "alpha"}


class TestSeamIntegrity:
    """The architecture claim, checked mechanically.

    Cheap, blunt, and hard to fudge — which is the point. A boundary
    maintained only by intention erodes under delivery pressure; this is what
    turns it into a build failure instead.
    """

    def test_core_contains_no_integration_references(self) -> None:
        import forge_search.core as core_pkg

        core_dir = Path(core_pkg.__file__).parent
        offenders = [
            path.name
            for path in core_dir.glob("*.py")
            if "apexyard" in path.read_text(encoding="utf-8").lower()
        ]

        assert not offenders, (
            f"core/ must not know about any integration, found in: {offenders}. "
            "If the core genuinely needs this, widen RootProvider deliberately "
            "rather than reaching across the seam."
        )

    def test_core_does_not_import_adapters(self) -> None:
        import forge_search.core as core_pkg

        core_dir = Path(core_pkg.__file__).parent
        # The rule is that core must not IMPORT the adapters package. The bare
        # word is legitimate — `resolve_roots(adapters=...)` takes them as a
        # parameter, which is the seam working as designed, not a violation.
        offenders = [
            path.name
            for path in core_dir.glob("*.py")
            if re.search(
                r"^\s*(from|import)\s+\.*adapters", path.read_text("utf-8"), re.M
            )
        ]

        assert not offenders, f"dependency must be one-way; violated in: {offenders}"

    def test_core_is_importable_without_the_adapter(self) -> None:
        """A standalone install must not require any adapter to be present."""
        import importlib

        for module in ("roots", "traversal", "cache", "index", "query", "server"):
            importlib.import_module(f"forge_search.core.{module}")


def test_a_no_op_expansion_is_not_reported():
    # "expanded X into 1" describes nothing happening, and on a stdio MCP
    # server every stderr line is rendered as a warning by the client.
    class Passthrough(RootProvider):
        name = "noop"

        def expand(self, root):
            return [root]

    res = resolve_roots(
        [Root(label="a", path=Path.cwd())],
        adapters=[Passthrough()],
        adapter_name="noop",
    )
    assert res.expansions == ()
    assert "expanded" not in res.describe()


def test_adapter_can_move_the_default_index_dir(tmp_path):
    # A portfolio adapter expands one root into many project clones, and may
    # want one index directory rather than one per clone. It knows the shape
    # of the tree it expanded; the caller usually does not.
    class Central(RootProvider):
        name = "central"
        index_dir = ".indexes"

        def expand(self, root):
            return [root]

    assert (
        resolve_roots(
            [Root(label="a", path=tmp_path)],
            adapters=[Central()],
            adapter_name="central",
        ).index_dir
        == ".indexes"
    )


def test_no_adapter_means_no_preference(tmp_path):
    assert resolve_roots([Root(label="a", path=tmp_path)]).index_dir is None
