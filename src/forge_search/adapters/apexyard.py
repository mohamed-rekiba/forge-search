"""ApexYard root provider.

The first — and so far only — integration adapter, which makes it the real
test of whether the `RootProvider` seam holds. Everything ApexYard-specific
lives here:

  * the ``.apexyard-fork`` marker that identifies an ops fork
  * ``apexyard.projects.yaml``, the registry of managed projects
  * the ``workspace/<name>/`` convention for local clones
  * split-portfolio mode, where the registry lives in a sibling repository

None of that appears anywhere in ``core``. If a change here needs a
corresponding change there, the seam is in the wrong place — that is the
signal to widen ``RootProvider`` deliberately, not to reach across it.

The ops fork itself is included as a root because its own docs, rules, and
hooks are searchable content, not just configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.roots import Root, RootProvider

__all__ = ["ApexyardRootProvider", "MARKER"]

#: Presence identifies an ops fork. Content is ignored by design, so the file
#: can carry an explanatory line without becoming a parsing dependency.
MARKER = ".apexyard-fork"

_REGISTRY = "apexyard.projects.yaml"
_CONFIG = ".claude/project-config.json"
_WORKSPACE = "workspace"


class ApexyardRootProvider(RootProvider):
    """Supplies roots from an ApexYard portfolio.

    Yields the ops fork plus every registered project that has a local clone.
    A registered project *without* one is skipped rather than reported as an
    error — a portfolio is routinely only partially cloned, and treating that
    as a failure would make the common case look broken.
    """

    marker = MARKER

    def __init__(self) -> None:
        self._ops_root: Path | None = None

    # -- discovery ---------------------------------------------------------

    def matches(self, start: Path) -> bool:
        self._ops_root = _find_ops_root(start)
        return self._ops_root is not None

    def roots(self) -> list[Root]:
        if self._ops_root is None:  # pragma: no cover - matches() guards this
            return []

        ops = self._ops_root
        # The ops fork under a reserved label. "." matches the convention the
        # registry itself uses for "the fork you are standing in".
        found = [Root(label=".", path=ops)]

        registry, workspace_dir = _resolve_portfolio_paths(ops)
        if registry is None or not registry.is_file():
            return found

        for name in _registered_project_names(registry):
            clone = workspace_dir / name
            if clone.is_dir():
                found.append(Root(label=name, path=clone))
        return found


# -- helpers ---------------------------------------------------------------


def _find_ops_root(start: Path) -> Path | None:
    """Walk up looking for the fork marker.

    Resolved first so a symlinked start cannot select a fork outside the tree
    the caller meant.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / MARKER).is_file():
            return candidate
    return None


def _resolve_portfolio_paths(ops_root: Path) -> tuple[Path | None, Path]:
    """Return (registry path, workspace dir), honouring split-portfolio mode.

    In split-portfolio mode the registry and workspace live in a private
    sibling repository, pointed at by a ``portfolio`` block in the project
    config. Those values are relative to the ops fork, and are resolved
    against it rather than the current directory — resolving against cwd would
    make discovery depend on where the process happened to start.
    """
    registry = ops_root / _REGISTRY
    workspace = ops_root / _WORKSPACE

    config_path = ops_root / _CONFIG
    if not config_path.is_file():
        return registry, workspace

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        # A malformed config must not take the whole provider down. Falling
        # back to single-fork layout is the conservative choice: worst case
        # some roots are missed, and discovery reports what it did find.
        return registry, workspace

    portfolio = config.get("portfolio")
    if not isinstance(portfolio, dict):
        return registry, workspace

    if isinstance(portfolio.get("registry"), str):
        registry = (ops_root / portfolio["registry"]).resolve()
    if isinstance(portfolio.get("workspace_dir"), str):
        workspace = (ops_root / portfolio["workspace_dir"]).resolve()
    return registry, workspace


def _registered_project_names(registry: Path) -> list[str]:
    """Extract project names from the registry.

    Deliberately a narrow line-scanner rather than a YAML parse. The adapter
    reads exactly one field from a file with a fixed, simple shape, and adding
    a YAML dependency to the whole package for that would push a transitive
    requirement onto every standalone user who will never touch ApexYard.

    Handles the two forms the registry uses in practice::

        - name: my-project
        - name: "my-project"

    A name that does not parse is skipped rather than guessed at — a wrong
    root is worse than a missing one, since it would index the wrong tree and
    return confident results from it.
    """
    names: list[str] = []
    try:
        text = registry.read_text(encoding="utf-8")
    except OSError:
        return names

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- name:"):
            continue
        value = stripped[len("- name:") :].strip()
        if value.startswith("#") or not value:
            continue
        value = value.split("#", 1)[0].strip()  # trailing comment
        value = value.strip("\"'")
        # Reject anything that could escape the workspace directory.
        if value and "/" not in value and value not in {".", ".."}:
            names.append(value)
    return names
