"""The ApexYard adapter — and, through it, the core/adapter seam.

The last class is the one that matters most: it checks the *architecture*
claim, not the adapter. If the core ever grows knowledge of ApexYard, the
standalone property becomes a README assertion rather than a fact, and these
tests are what would catch that.
"""

from __future__ import annotations

import json
from pathlib import Path


from forge_search.adapters.apexyard import MARKER, ApexyardRootProvider
from forge_search.core.roots import DiscoveryLayer, discover_roots


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


class TestDetection:
    def test_matches_at_the_fork_root(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha"])
        assert ApexyardRootProvider().matches(fork)

    def test_matches_from_a_nested_directory(self, tmp_path: Path) -> None:
        """Discovery has to work from wherever the user actually is."""
        fork = _make_fork(tmp_path, ["alpha"])
        assert ApexyardRootProvider().matches(fork / "workspace" / "alpha" / "src")

    def test_does_not_match_an_unrelated_directory(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        assert not ApexyardRootProvider().matches(plain)


class TestRoots:
    def test_includes_the_ops_fork_and_each_clone(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha", "beta"])
        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert {r.label for r in provider.roots()} == {".", "alpha", "beta"}

    def test_registered_project_without_a_clone_is_skipped(
        self, tmp_path: Path
    ) -> None:
        """A partially-cloned portfolio is normal, not an error state."""
        fork = _make_fork(tmp_path, ["alpha", "beta"], clone=["alpha"])
        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert {r.label for r in provider.roots()} == {".", "alpha"}

    def test_empty_registry_still_yields_the_fork(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, [])
        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert [r.label for r in provider.roots()] == ["."]

    def test_missing_registry_still_yields_the_fork(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha"])
        (fork / "apexyard.projects.yaml").unlink()
        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert [r.label for r in provider.roots()] == ["."]

    def test_quoted_names_parse(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, [])
        (fork / "apexyard.projects.yaml").write_text(
            'version: 1\nprojects:\n  - name: "quoted-proj"\n    repo: org/x\n'
        )
        (fork / "workspace" / "quoted-proj").mkdir(parents=True)
        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert "quoted-proj" in {r.label for r in provider.roots()}

    def test_path_traversal_in_a_name_is_rejected(self, tmp_path: Path) -> None:
        """A malicious or malformed name must not select a tree outside the
        workspace — a wrong root returns confident results from the wrong place."""
        fork = _make_fork(tmp_path, [])
        (fork / "apexyard.projects.yaml").write_text(
            "version: 1\nprojects:\n  - name: ../../etc\n  - name: ..\n"
        )
        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert [r.label for r in provider.roots()] == ["."]


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

        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert {r.label for r in provider.roots()} == {".", "gamma"}

    def test_malformed_config_falls_back_instead_of_crashing(
        self, tmp_path: Path
    ) -> None:
        fork = _make_fork(tmp_path, ["alpha"])
        (fork / ".claude").mkdir(exist_ok=True)
        (fork / ".claude" / "project-config.json").write_text("{not valid json")

        provider = ApexyardRootProvider()
        provider.matches(fork)

        assert {r.label for r in provider.roots()} == {".", "alpha"}


class TestDiscoveryIntegration:
    def test_provider_layer_wins_over_git_walk_up(self, tmp_path: Path) -> None:
        fork = _make_fork(tmp_path, ["alpha"])
        (fork / ".git").mkdir()

        result = discover_roots(providers=[ApexyardRootProvider()], start=fork)

        assert result.layer is DiscoveryLayer.PROVIDER
        assert {r.label for r in result.roots} == {".", "alpha"}

    def test_explicit_roots_still_outrank_the_provider(self, tmp_path: Path) -> None:
        """Explicit configuration is the top layer and must stay that way."""
        from forge_search.core.roots import Root

        fork = _make_fork(tmp_path, ["alpha"])
        chosen = Root(label="only", path=tmp_path)

        result = discover_roots(
            explicit=[chosen], providers=[ApexyardRootProvider()], start=fork
        )

        assert result.layer is DiscoveryLayer.EXPLICIT
        assert [r.label for r in result.roots] == ["only"]


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
        offenders = [
            path.name
            for path in core_dir.glob("*.py")
            if "adapters" in path.read_text(encoding="utf-8")
        ]

        assert not offenders, f"dependency must be one-way; violated in: {offenders}"

    def test_core_is_importable_without_the_adapter(self) -> None:
        """A standalone install must not require any adapter to be present."""
        import importlib

        for module in ("roots", "traversal", "cache", "index", "query", "server"):
            importlib.import_module(f"forge_search.core.{module}")
