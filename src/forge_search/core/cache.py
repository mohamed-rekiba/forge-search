"""Where index files live.

Indexes go in the OS cache directory, never inside the tree being searched.
Three reasons, in order of how much trouble each avoids:

1. An index inside the repo gets committed, or has to be gitignored in every
   repo forge-search touches — including ones the operator does not control.
2. An index inside the searched tree is itself searchable, so the tool indexes
   its own output and matches on it.
3. Cache directories are already understood by the OS and by users: they are
   safe to delete, excluded from backups, and cleaned by existing tooling.

The cache key is derived from the root's *resolved absolute path*, but the
absolute path is never stored — only its hash. That gives per-root isolation
without persisting a path that would go stale on move or rename.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

__all__ = ["cache_root", "index_dir_for", "index_path_for"]

_APP = "forge-search"


def cache_root() -> Path:
    """The base cache directory, per OS convention.

    Honours ``FORGE_SEARCH_CACHE_DIR`` first so a caller can redirect it —
    needed for tests, sandboxes, and anyone whose home directory is not
    writable.
    """
    override = os.environ.get("FORGE_SEARCH_CACHE_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / _APP
    if os.name == "nt":  # pragma: no cover - not exercised on CI
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / _APP / "Cache"
    # XDG, and the documented fallback when the variable is unset.
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / _APP


def index_dir_for(root_path: Path, index_dir: str | None) -> Path:
    """Where one root's index lives.

    Two storage models, each with a real cost:

    * **Shared cache dir** (default) — nothing is written into trees you may
      not control, and nothing can be committed by accident. The cost is
      orphans: the index is keyed by a hash of the root path, so deleting,
      moving, or renaming a project leaves its index behind forever with no
      way to tell which is which.
    * **In-tree** (``--index-dir .forge_search``) — the index lives and dies
      with the project, is obvious to find, and is deleted when the project
      is. The cost is that it must be gitignored, and it would be indexed by
      the next run if traversal did not exclude it (it does; see
      DEFAULT_EXCLUDES).

    Neither dominates, which is why this is a flag rather than a decision
    made on the caller's behalf.
    """
    if index_dir is None:
        return cache_root()
    d = Path(index_dir).expanduser()
    return d if d.is_absolute() else root_path / d


def index_path_for(label: str, path: Path, index_dir: str | None = None) -> Path:
    """Return the index file for one root.

    The filename carries the label for human legibility and a short hash of
    the resolved path for uniqueness — two roots labelled ``docs`` in
    different trees must not collide, and the label alone cannot guarantee
    that.
    """
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
    base = index_dir_for(path, index_dir)
    if index_dir is not None:
        # In-tree: one root, one directory, so the path hash that
        # disambiguates a shared cache would be noise in the filename.
        return base / f"{safe_label}.db"
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    return base / f"{safe_label}-{digest}.db"
