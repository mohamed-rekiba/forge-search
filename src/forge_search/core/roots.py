"""Roots: the directory trees that get indexed and searched.

**Everything here is explicit. Nothing is discovered.**

The caller names the roots as absolute paths. If a root holds several
projects, the caller also names the directories to expand it into, with
`--sub-projects`. There is no walk-up, no working-directory inference, no
marker sniffing, and no fallback guess.

An earlier version of this docstring said to use `--root ${workspaceFolder}`
and let the editor substitute it. That was wrong: it is a VS Code
`launch.json` variable, Claude Code's `.mcp.json` expands environment
variables only, and it reached a real client as a literal string. See
README.md for the per-client table.

This replaced a layered discovery scheme, and the reasoning is worth keeping:

* **The caller already knows the answer.** Inferring it solved a problem the
  caller had already solved, and inferring it wrongly is silent — the wrong
  tree gets indexed and every search returns plausible results from the wrong
  place. A wrong absolute path fails loudly instead.
* **Staleness is the honest cost, and it is the smaller one.** Discovery was
  partly justified by baked absolute paths going stale on move or re-clone.
  That cost is real — an absolute path makes a shared `.mcp.json`
  per-machine — but it fails at startup with a message, where a wrong guess
  fails silently on every query forever.
* **Detection has the same flaw as guessing.** A component that expands
  itself because it spotted a marker file can fire when unwanted and stay
  silent when wanted. Naming it costs one flag and removes the whole failure
  class.

`--sub-projects` answers the one question a root's own path genuinely cannot:
that it holds several projects, and which directories those live under. That
used to be answered by reading a portfolio's own config to infer the same
structure — the one guess this module made anywhere. Stating it as a
flag instead removes that exception rather than keeping it; see
docs/decisions.md for the full reasoning and what it gives up.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Root", "Resolution", "resolve_roots"]


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


def _sub_project_roots(root: Root, sub_project_dirs: Sequence[str]) -> list[Root]:
    """Every checkout under `root`'s named sub-project directories.

    Each directory in `sub_project_dirs` is resolved **relative to `root`**,
    not the caller's working directory — that is what lets
    `--sub-projects ../portfolio/workspace` reach outside the root at all,
    the split-portfolio case a config file used to be read for. A named
    directory that does not exist is not an error: the root is still used,
    just without that expansion.

    An entry counts as a checkout when it contains `.git` — tested for
    **existence, not directory-ness**. A worktree's `.git` is a *file*
    (a `gitdir: /path/to/real/.git/worktrees/name` pointer), so `is_dir()`
    would silently exclude every worktree checkout while a plain clone's
    `.git` directory passed. Hidden entries and plain files are skipped
    before that check even runs.
    """
    found: list[Root] = []
    # Two named directories can resolve to the same place — `workspace` twice,
    # or `workspace` and `./workspace`. Yielding a checkout twice would index
    # it twice and report a count nobody can reconcile with the tree, so the
    # same resolved path is taken once. This is repetition, not the ambiguity
    # `_reject_duplicate_labels` raises on: there is exactly one right answer.
    seen: set[Path] = set()
    for rel in sub_project_dirs:
        base = (root.path / rel).resolve()
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir(), key=lambda p: p.name):
            if entry.name.startswith("."):
                continue
            if not entry.is_dir():
                continue
            if not (entry / ".git").exists():
                continue
            resolved_entry = entry.resolve()
            if resolved_entry in seen:
                continue
            seen.add(resolved_entry)
            found.append(Root(label=entry.name, path=entry))
    return found


@dataclass(frozen=True, slots=True)
class Resolution:
    """The roots that will be searched, and how each came to be.

    `expansions` records which given root's `--sub-projects` scan turned up
    extra roots, so the operator can see it at startup. A root list that
    silently differs from what was asked for is the failure this reporting
    exists to prevent.
    """

    roots: tuple[Root, ...]
    expansions: tuple[tuple[str, int], ...] = ()  # (given label, sub-projects found)

    def describe(self) -> str:
        """One line naming the resolved roots."""
        names = ", ".join(f"{r.label}={r.path}" for r in self.roots) or "(none)"
        parts = [f"roots: {names}"]
        if self.expansions:
            parts.append(
                "; ".join(
                    f"{label} expanded into {count} sub-project(s)"
                    for label, count in self.expansions
                )
            )
        return "  ".join(parts)


def resolve_roots(
    given: Iterable[Root],
    *,
    sub_projects: Sequence[str] = (),
) -> Resolution:
    """Resolve the roots to search from the roots the caller named.

    **Everything is explicit.** Roots are stated by the caller as absolute
    paths; sub-project directories, if any, are named by `--sub-projects` and
    resolved relative to each root in turn. Nothing is discovered, detected,
    or inferred.

    Each given root is always searched — that part never changes. With no
    `--sub-projects`, roots are used exactly as given, same as before this
    flag existed.
    """
    resolved: list[Root] = []
    expansions: list[tuple[str, int]] = []

    for root in given:
        resolved.append(root)
        if not sub_projects:
            continue

        expanded = _sub_project_roots(root, sub_projects)
        if not expanded:
            # Nothing found under any named directory — not worth reporting.
            # "expanded into 0" describes a non-event, and on a stdio MCP
            # server every stderr line is rendered as a warning by the client.
            continue

        resolved.extend(expanded)
        expansions.append((root.label, len(expanded)))

    _reject_duplicate_labels(resolved)
    return Resolution(tuple(resolved), tuple(expansions))


def _reject_duplicate_labels(roots: Sequence[Root]) -> None:
    """Fail loudly when two roots share a label.

    Indexes are keyed by label, so a second root with a label already taken
    silently reuses the first one's index: the tree is resolved, printed at
    startup, and returns nothing of its own. `core/cache.py` already defends
    this on disk by hashing the path into shared-directory filenames; nothing
    defended it at resolution, because until sub-project expansion every label
    was either stated by the caller or came from one flat list of unique names.

    Expansion makes collisions easy and ordinary: `--sub-projects
    packages,apps` with a `foo` under each — the help text's own example
    shape — or a checkout whose name matches its own root's label. Raising
    names both paths, because "foo is ambiguous" without them just sends the
    reader looking.
    """
    seen: dict[str, Path] = {}
    for root in roots:
        first = seen.get(root.label)
        if first is not None and first != root.path:
            raise ValueError(
                f"duplicate root label {root.label!r}: {first} and {root.path}. "
                "Labels key the indexes, so the second would silently reuse the "
                "first's. Rename a directory, or name the roots explicitly with "
                "--root label=path."
            )
        seen[root.label] = root.path
