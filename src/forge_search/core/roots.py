"""Root discovery.

A *root* is one directory tree forge-search indexes and searches. Standalone
users point at one or more directly; an integration supplies them from
wherever it keeps that list.

Discovery is layered, highest precedence first:

  1. explicit configuration  (--root flags, config file, env)
  2. a marker walked up from the start directory  (integration-supplied)
  3. .git walked up from the start directory      (generic single-repo)
  4. the start directory itself                   (last resort)

**No absolute path is ever persisted.** That is not a style preference: baked
absolute roots are a known silent-failure mode — they go stale on move,
rename, or re-clone, the server then fails to launch, and every search quietly
returns nothing with no error. Roots are resolved fresh on every start so
there is nothing to go stale.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = ["Root", "RootProvider", "DiscoveryLayer", "Discovery", "discover_roots"]


@dataclass(frozen=True, slots=True)
class Root:
    """One indexed tree.

    `label` is how results identify their origin. It is *stable and relative* —
    never an absolute path — because labels are returned to callers and end up
    in logs, caches, and cache keys. An absolute path in a label would leak the
    machine layout and change when the tree moves.
    """

    label: str
    path: Path

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("root label must be non-empty")
        if os.path.isabs(self.label):
            raise ValueError(
                f"root label must not be an absolute path (got {self.label!r}); "
                "labels are returned to callers and must stay portable"
            )


class RootProvider:
    """The single seam between the generic core and any integration.

    An integration supplies roots and *nothing else*. Everything downstream —
    discovery, indexing, ranking, the tool surface — consumes this interface
    and knows nothing about where the list came from.

    Keeping the seam this narrow is deliberate. A wider one would let
    integration concepts leak into the core, at which point "works standalone"
    becomes a claim in a README rather than a property of the code.
    """

    #: Marker filename this provider anchors on during layer-2 discovery,
    #: or None if it does not participate in walk-up discovery.
    marker: str | None = None

    def roots(self) -> Sequence[Root]:  # pragma: no cover - interface
        raise NotImplementedError

    def matches(self, start: Path) -> bool:  # pragma: no cover - interface
        """Whether this provider applies to `start`."""
        return False


class DiscoveryLayer(Enum):
    """Which layer produced the roots — reported so a wrong guess is visible.

    Silent misdiscovery is the failure this whole module is built against: if
    the wrong tree gets indexed, every search returns plausible results from
    the wrong place, and nothing announces it.
    """

    EXPLICIT = "explicit"
    PROVIDER = "provider"
    GIT = "git"
    CWD = "cwd"


@dataclass(frozen=True, slots=True)
class Discovery:
    layer: DiscoveryLayer
    roots: tuple[Root, ...]

    def describe(self) -> str:
        """One line, for logging at startup so the operator can see the choice."""
        names = ", ".join(f"{r.label}={r.path}" for r in self.roots) or "(none)"
        return f"roots via {self.layer.value}: {names}"


def _walk_up(start: Path, predicate) -> Path | None:
    """Walk from `start` to the filesystem root, returning the first hit.

    Resolved first so a symlinked start directory cannot produce a root that
    escapes the tree the caller meant.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if predicate(candidate):
            return candidate
    return None


def discover_roots(
    *,
    explicit: Iterable[Root] | None = None,
    providers: Sequence[RootProvider] = (),
    start: Path | None = None,
) -> Discovery:
    """Resolve roots by the layered strategy documented above.

    `start` defaults to the current working directory — **not** this module's
    location. That distinction matters: under a global tool install the package
    lives in a shared virtualenv far from the user's code, so anchoring on the
    module path would discover the installation directory instead of the
    repository the user is sitting in.
    """
    start = (start or Path.cwd()).resolve()

    explicit_roots = tuple(explicit or ())
    if explicit_roots:
        return Discovery(DiscoveryLayer.EXPLICIT, explicit_roots)

    for provider in providers:
        if provider.matches(start):
            roots = tuple(provider.roots())
            if roots:
                return Discovery(DiscoveryLayer.PROVIDER, roots)

    git_root = _walk_up(start, lambda p: (p / ".git").exists())
    if git_root is not None:
        return Discovery(DiscoveryLayer.GIT, (Root(label=git_root.name, path=git_root),))

    return Discovery(DiscoveryLayer.CWD, (Root(label=start.name or "root", path=start),))
