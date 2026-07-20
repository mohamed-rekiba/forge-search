"""Roots: the directory trees that get indexed and searched.

**Everything here is explicit. Nothing is discovered.**

The caller names the roots — in an editor as `--root ${workspaceFolder}`,
which the editor substitutes at launch; elsewhere as an absolute path. The
adapter, if any, is named with `--adapter`. There is no walk-up, no
working-directory inference, no marker sniffing, and no fallback guess.

This replaced a layered discovery scheme, and the reasoning is worth keeping:

* **The caller already knows the answer.** Editors substitute the project path
  at launch. Inferring it solved a problem that was already solved, and
  inferring it wrongly is silent — the wrong tree gets indexed and every
  search returns plausible results from the wrong place.
* **The staleness argument does not survive.** Discovery was partly justified
  by baked absolute paths going stale on move or re-clone. A variable
  substituted at launch cannot go stale that way, so the justification
  evaporated.
* **Detection has the same flaw as guessing.** An adapter that enables itself
  because it spotted a file can fire when unwanted and stay silent when
  wanted. Naming it costs one flag and removes the whole failure class.

What adapters still do is the part a caller genuinely cannot supply: knowing
that one root is a portfolio containing several projects, and what each should
be called.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Root", "RootProvider", "Resolution", "resolve_roots"]


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
        # Normalise the path to resolved-absolute at construction, so every
        # consumer can rely on it. Without this, a Root built from a relative
        # path breaks: traversal resolves internally and yields absolute
        # paths, while `relative_to()` compares against the unresolved
        # original and raises. Found by a clean-machine install test — the
        # in-repo fixtures all use tmp_path, which is already absolute, so
        # nothing else could reach it.
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())


class RootProvider:
    """The single seam between the generic core and any integration.

    An adapter is a root **expander**, not a root discoverer. It is handed a
    root the caller has already named, decides whether that root is one it
    understands, and if so expands it into the several labelled roots it
    really contains.

    **Adapters are chosen explicitly, never detected.** `--adapter <name>`
    selects one; without it, roots are used exactly as given.

    Nothing is inferred — not from a marker file in the project, not from the
    working directory, not by walking parents. Detection was removed on
    purpose: an adapter that fires because it spotted a file is an adapter
    that can fire when you did not mean it to, and can fail to fire when you
    did, both silently. The caller already knows which shape of project this
    is; asking them costs one flag and removes an entire class of wrong-guess
    failure.

    Keeping the seam this narrow is deliberate. A wider one would let
    integration concepts into the core, at which point "works standalone"
    becomes a claim in a README rather than a property of the code.
    """

    #: The name `--adapter` selects this by.
    name: str = ""

    def expand(self, root: Root) -> Sequence[Root]:  # pragma: no cover - interface
        """Turn one root into the roots it actually contains."""
        return [root]


@dataclass(frozen=True, slots=True)
class Resolution:
    """The roots that will be searched, and how each came to be.

    `expansions` records which given root an adapter turned into which set, so
    the operator can see it at startup. A root list that silently differs from
    what was asked for is the failure this reporting exists to prevent.
    """

    roots: tuple[Root, ...]
    expansions: tuple[tuple[str, str, int], ...] = ()  # (adapter, given label, count)

    def describe(self) -> str:
        """One line for stderr at startup."""
        names = ", ".join(f"{r.label}={r.path}" for r in self.roots) or "(none)"
        if not self.expansions:
            return f"roots: {names}"
        expanded = "; ".join(
            f"{adapter} expanded {label} into {count}"
            for adapter, label, count in self.expansions
        )
        return f"roots: {names}  ({expanded})"


def resolve_roots(
    given: Iterable[Root],
    *,
    adapters: Sequence[RootProvider] = (),
    adapter_name: str | None = None,
) -> Resolution:
    """Resolve the roots to search from the roots the caller named.

    **Everything is explicit.** Roots are stated by the caller — in an editor
    via ``--root ${workspaceFolder}``, elsewhere as an absolute path — and the
    adapter, if any, is named by ``--adapter``. Nothing is discovered, detected,
    or inferred.

    That is the whole design. Editors already know the project path and
    substitute it at launch, so inferring it solved a problem the caller had
    already solved. Detecting the adapter from a marker file had the same
    shape: it can fire when unwanted and fail to fire when wanted, both
    silently, and silence is the failure this project exists to eliminate.
    With no adapter named, roots are used exactly as given.
    """
    resolved: list[Root] = []
    expansions: list[tuple[str, str, int]] = []

    chosen = None
    if adapter_name:
        chosen = next((a for a in adapters if a.name == adapter_name), None)
        if chosen is None:
            known = ", ".join(a.name for a in adapters if a.name) or "(none available)"
            raise ValueError(f"unknown adapter {adapter_name!r}; available: {known}")

    for root in given:
        if chosen is None:
            resolved.append(root)
            continue

        expanded = list(chosen.expand(root))
        if not expanded:
            # An adapter that claims a root but yields nothing would silently
            # drop it. Keep the original rather than searching less than asked.
            resolved.append(root)
            continue

        resolved.extend(expanded)
        expansions.append(
            (chosen.name or type(chosen).__name__, root.label, len(expanded))
        )

    return Resolution(tuple(resolved), tuple(expansions))
