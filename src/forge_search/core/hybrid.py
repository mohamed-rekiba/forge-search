"""Composes the lexical and semantic arms into one searchable index.

This module exists so neither arm has to know about the other. `index.py`
never imports `semantic.py` — that one-way property is what lets a
lexical-only installation work at all, and it would quietly disappear if
fusion lived inside either arm.

Both arms share one SQLite file per root. FTS5 and vec0 are separate tables in
the same database, so a root is still one file to create, invalidate, and
delete. Keeping them in step matters: an index where the arms disagree about
which files exist would return hits pointing at deleted content from one arm
and not the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .embedding import Embedder
from .fusion import ArmWeights, fuse
from .index import Hit, IndexStats, LexicalIndex
from .roots import Root
from .semantic import SemanticIndex, vec_available
from .traversal import TraversalConfig

__all__ = ["HybridIndex", "SearchMode"]


class SearchMode:
    """Which arms to consult.

    Exposed so `bench` can ablate — measuring each arm alone against the
    hybrid is the only way to know whether the semantic half earns its cost.
    """

    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"

    ALL = (LEXICAL, SEMANTIC, HYBRID)


@dataclass(slots=True)
class HybridIndex:
    """Lexical + optional semantic search over one root."""

    root: Root
    embedder: Embedder | None = None
    config: TraversalConfig = field(default_factory=TraversalConfig)
    weights: ArmWeights = field(default_factory=ArmWeights)

    _lexical: LexicalIndex = field(init=False)
    _semantic: SemanticIndex | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._lexical = LexicalIndex(self.root, config=self.config)
        if self.embedder is not None and vec_available():
            # Same database file as the lexical arm — one file per root.
            self._semantic = SemanticIndex(self._lexical.db_path, self.embedder)

    @property
    def has_semantic(self) -> bool:
        return self._semantic is not None

    def close(self) -> None:
        if self._semantic is not None:
            self._semantic.close()
        self._lexical.close()

    def __enter__(self) -> HybridIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- indexing ----------------------------------------------------------

    def is_stale(self) -> bool:
        return self._lexical.is_stale()

    def semantic_needs_backfill(self) -> bool:
        """Whether the semantic arm is empty while the lexical arm has content.

        This is the "enabled semantic later" case, and it is a silent failure
        without an explicit check. The lexical arm decides what changed, so if
        it is already fresh it reports *no* changes — and the semantic arm,
        which has never been built, is handed nothing to do. Searches then
        keep working, because hybrid degrades to lexical, and the semantic arm
        contributes nothing forever with no error and no signal.

        Found by running against a real repository after the unit tests passed;
        every fixture built both arms from empty, so none of them could catch it.
        """
        if self._semantic is None:
            return False
        return self._semantic.count() == 0 and bool(self._lexical.indexed_count())

    def reindex(self, *, force: bool = False) -> IndexStats:
        """Reindex both arms, keeping them in step.

        The lexical arm decides what changed — it already tracks size and
        mtime per file, and duplicating that bookkeeping in the semantic arm
        would create a second source of truth that can drift from the first.

        The one exception is backfill: an empty semantic arm alongside a
        populated lexical one needs every file, not the (possibly empty) change
        set. See `semantic_needs_backfill`.
        """
        backfill = self.semantic_needs_backfill()
        changed, removed = self._lexical.pending_changes(force=force)
        if backfill:
            changed = self._lexical.indexed_relpaths()
        stats = self._lexical.reindex(force=force)

        if self._semantic is not None and (changed or removed or force):
            for relpath in removed:
                self._semantic.drop_file(relpath)
            for relpath in changed:
                if not force:
                    self._semantic.drop_file(relpath)
                path = self.root.path / relpath
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                self._semantic.index_file(relpath, text)
            self._semantic.commit()

        return stats

    # -- searching ---------------------------------------------------------

    def search(
        self, query: str, *, limit: int = 10, mode: str = SearchMode.HYBRID
    ) -> list[Hit]:
        """Search with the requested arms, fused by rank.

        Falls back to lexical whenever the semantic arm is unavailable, rather
        than erroring — a lexical-only install is a supported configuration.
        Asking explicitly for `semantic` when it is absent returns nothing,
        which is honest: the caller asked for an arm that is not there.
        """
        if mode == SearchMode.LEXICAL or self._semantic is None:
            if mode == SearchMode.SEMANTIC and self._semantic is None:
                return []
            return self._lexical.search(query, limit=limit)

        if mode == SearchMode.SEMANTIC:
            return self._semantic_hits(query, limit)

        # Hybrid: over-fetch each arm so fusion has candidates to reorder.
        # Fusing two already-truncated top-10 lists would mostly reproduce
        # them; the reordering only has room to matter with more candidates.
        depth = limit * 3
        lexical_hits = self._lexical.search(query, limit=depth)
        semantic_hits = self._semantic_hits(query, depth)

        by_key: dict[str, Hit] = {}
        for hit in (*lexical_hits, *semantic_hits):
            by_key.setdefault(_key(hit), hit)

        fused = fuse(
            lexical=[_key(h) for h in lexical_hits],
            semantic=[_key(h) for h in semantic_hits],
            weights=self.weights,
            limit=limit,
        )
        return [
            # Replace the arm-local score with the fused one so a caller
            # sorting by score gets the fused order, not BM25's.
            Hit(
                project=by_key[key].project,
                path=by_key[key].path,
                line=by_key[key].line,
                excerpt=by_key[key].excerpt,
                score=round(score, 6),
            )
            for key, score in fused
            if key in by_key
        ]

    def _semantic_hits(self, query: str, limit: int) -> list[Hit]:
        """Semantic neighbours, shaped as `Hit` for a uniform result type.

        The excerpt is read from disk rather than stored: the semantic arm
        indexes overlapping multi-line chunks, so echoing the whole chunk back
        would return far more text than the caller asked for.
        """
        assert self._semantic is not None
        hits: list[Hit] = []
        for relpath, line, distance in self._semantic.search(query, limit=limit):
            hits.append(
                Hit(
                    project=self.root.label,
                    path=relpath,
                    line=line,
                    excerpt=_line_at(self.root.path / relpath, line),
                    # Distance is lower-is-better; invert so every Hit in the
                    # system means higher-is-better regardless of origin.
                    score=-float(distance),
                )
            )
        return hits


def _key(hit: Hit) -> str:
    return f"{hit.path}:{hit.line}"


def _line_at(path: Path, line: int, width: int = 150) -> str:
    """Read one line from disk, bounded. Empty string if unreadable."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for number, text in enumerate(handle, start=1):
                if number == line:
                    stripped = text.rstrip("\n")
                    return stripped[:width] + ("…" if len(stripped) > width else "")
    except OSError:
        pass
    return ""
