"""Where index files live.

Indexes live in ``<root>/.forge_search`` by default. This reverses an earlier
decision, and both sides are worth keeping written down.

The original argument for a shared OS cache directory was that an in-tree
index gets committed, is itself searchable so the tool indexes its own output,
and misses out on tooling that already understands cache directories. The
first two are real failures, not preferences.

What outweighed them is that a shared cache keys each index by a hash of the
root path. Delete, move, or rename a project and its index is orphaned
forever, with nothing in the filename to say which project it belonged to.
Nothing ever collects them, and nothing announces the leak — the machine this
was written on had accumulated ~600 MB before anyone looked. In-tree, the
index lives and dies with the project.

So the two real objections are answered directly rather than avoided:

* **Committed by accident** — ``ensure_index_dir`` writes a ``.gitignore``
  containing ``*``, so the directory ignores itself and its contents. No user
  has to remember anything.
* **Indexed by itself** — ``.forge_search`` is in ``DEFAULT_EXCLUDES``, so
  traversal never descends into it.

An absolute ``--index-dir`` opts back into one shared directory, which is
still the right answer for trees you cannot write to. There, the path hash
returns, because one directory holding many roots cannot disambiguate two
projects that share a label.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

__all__ = [
    "DEFAULT_INDEX_DIR",
    "cache_root",
    "ensure_index_dir",
    "index_dir_for",
    "index_path_for",
]

#: Default index location, relative to each root. In-tree because an index in
#: a shared cache is keyed by a hash of the root path: delete, move, or rename
#: a project and its index is orphaned forever, with nothing in the filename
#: to say which project it belonged to. Junk accumulates silently — the
#: machine this was written on had ~600 MB of it.
#:
#: In-tree, the index lives and dies with the project. The two costs are that
#: it must be gitignored, and that it would be indexed by the next run if
#: traversal did not exclude it (it does — see DEFAULT_EXCLUDES).
DEFAULT_INDEX_DIR = ".forge_search"

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

    Defaults to ``<root>/.forge_search`` — see DEFAULT_INDEX_DIR for why
    in-tree rather than a shared cache.

    A relative ``index_dir`` is resolved per root, so several roots each get
    their own. An absolute one is used as-is, which is how to opt back into a
    single shared directory:

        --index-dir ~/Library/Caches/forge-search

    ``FORGE_SEARCH_CACHE_DIR`` still redirects the shared location, and is
    what the test suite uses to stay out of a developer's real cache.
    """
    d = Path(index_dir or DEFAULT_INDEX_DIR).expanduser()
    return d if d.is_absolute() else root_path / d


def ensure_index_dir(directory: Path) -> Path:
    """Create the index directory, and make it ignore itself.

    The in-tree default writes into repositories forge-search does not own, so
    it must not become someone's accidental commit. Rather than ask every user
    to remember a `.gitignore` entry — correctness that depends on remembering
    is not correctness, which is the whole premise of this project — the
    directory ships one that ignores everything including itself.

    Costs nothing when the tree is not a git repository, and is equally
    invisible to `git status` when it is.
    """
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ".gitignore"
    if not marker.exists():
        # "*" covers this file too, so the directory leaves no trace at all.
        marker.write_text("*\n", encoding="utf-8")
    return directory


def index_path_for(label: str, path: Path, index_dir: str | None = None) -> Path:
    """Return the index file for one root.

    The filename carries the label for human legibility and a short hash of
    the resolved path for uniqueness — two roots labelled ``docs`` in
    different trees must not collide, and the label alone cannot guarantee
    that.
    """
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
    base = index_dir_for(path, index_dir)
    if not base.is_absolute() or _is_per_root(index_dir):
        # Per-root directory: one root, one dir, so a path hash in the
        # filename would be noise.
        return base / f"{safe_label}.db"
    # A shared directory holds indexes for many roots, so the label alone
    # cannot disambiguate — two projects both labelled "docs" would collide.
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    return base / f"{safe_label}-{digest}.db"


def _is_per_root(index_dir: str | None) -> bool:
    """True when the index dir is relative, so each root gets its own."""
    return not Path(index_dir or DEFAULT_INDEX_DIR).expanduser().is_absolute()
