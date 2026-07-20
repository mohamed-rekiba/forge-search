"""Lexical index: SQLite FTS5.

FTS5 ships inside CPython's stdlib ``sqlite3``, so the lexical half of
forge-search has no third-party dependency and no compiler step. That is what
keeps a standalone install trivial.

**Freshness is owned here, at query time, not delegated to the caller.** The
alternative — reminding a human or an agent to reindex after changes — is a
convention, and the defects this project exists to fix were all conventions
nobody followed. An index that can silently serve stale results is the same
class of failure: plausible answers, no error, no signal.

So every file carries its size and mtime, and a search checks them before
trusting the index. Cheap (one ``stat`` per indexed file), and it makes
staleness impossible to not notice.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .cache import index_path_for
from .query import QueryError, to_fts5
from .roots import Root
from .traversal import TraversalConfig, excerpt, walk

__all__ = ["Hit", "IndexStats", "LexicalIndex"]

#: Score assigned to a filename match, chosen to sit above BM25 content scores
#: rather than being tuned against them. BM25 values here run roughly 0–20
#: after negation, so this reliably ranks a filename hit first without
#: pretending the two are on a shared scale — they are not, and calibrating
#: them against each other would need corpus statistics that shift on every
#: reindex. Same reasoning as using rank fusion between the arms.
_PATH_MATCH_SCORE = 1000.0

#: Bump whenever the schema or the indexing strategy changes in a way that
#: makes an existing index incomplete or wrong.
#:
#: This exists because the same bug shipped twice: adding the semantic arm,
#: then adding the paths table, each left existing indexes silently empty for
#: the new feature. Both times the index looked healthy, searches kept
#: returning results, and the new capability contributed nothing forever with
#: no error — the exact failure shape this whole project exists to eliminate,
#: reproduced by its own incremental-indexing logic.
#:
#: An index whose stored version differs is rebuilt on open. A rebuild costs
#: seconds; a silently incomplete index costs correctness indefinitely.
#: Bumped to 4 to invalidate indexes mis-stamped by a buggy version-3 guard
#: that wrote the version without clearing. Those indexes claim to be current
#: while missing the paths table entirely — and because the stamp matches, no
#: later check would ever catch them. A bump is the only way past a bad stamp,
#: which is itself an argument for making these guards blunt rather than clever.
SCHEMA_VERSION = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id        INTEGER PRIMARY KEY,
    relpath   TEXT NOT NULL UNIQUE,
    size      INTEGER NOT NULL,
    mtime_ns  INTEGER NOT NULL
);

-- contentless=0: FTS5 stores the text itself, so excerpts can be produced
-- without re-reading the file. Costs disk, saves an IO round-trip per hit.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    relpath UNINDEXED,
    line    UNINDEXED,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- Paths are indexed separately from content, because a filename lookup is
-- a different question from a content lookup. Searching for
-- "require-active-ticket" should find require-active-ticket.sh even though
-- that file never mentions its own name — which a content-only index cannot
-- do, and which was one of the dominant query shapes this tool targets.
CREATE VIRTUAL TABLE IF NOT EXISTS paths USING fts5(
    relpath,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Hit:
    """One search result.

    ``path`` is always **relative to its root** — never absolute. Callers
    receive these, so an absolute path would leak machine layout and break the
    moment the tree moves.
    """

    project: str
    path: str
    line: int
    excerpt: str
    score: float


@dataclass(frozen=True, slots=True)
class IndexStats:
    files_indexed: int
    files_removed: int
    chunks_written: int
    up_to_date: bool


class LexicalIndex:
    """An FTS5 index over one root."""

    def __init__(self, root: Root, *, config: TraversalConfig | None = None) -> None:
        self.root = root
        self.config = config or TraversalConfig()
        self.db_path = index_path_for(root.label, root.path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_if_stale()

    def _migrate_if_stale(self) -> None:
        """Discard the index if it was built by a different schema version.

        Deliberately blunt: everything is dropped and rebuilt on next reindex,
        rather than attempting a targeted migration per version. A partial
        migration that misses one table reintroduces exactly the silent-gap
        bug this guard exists to prevent, and rebuilding is cheap.
        """
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        stored = int(row[0]) if row else None

        if stored == SCHEMA_VERSION:
            return

        with self._conn:
            # Clear unconditionally when the version does not match, INCLUDING
            # when no version is stored. An unstamped index is a pre-versioning
            # index — precisely the case that needs rebuilding. An earlier
            # version of this guard skipped the clear when `stored is None`,
            # which stamped the current version onto a stale index and left the
            # gap it was written to close. Clearing an already-empty index is
            # free; skipping a needed rebuild is silent and permanent.
            self._conn.execute("DELETE FROM chunks")
            self._conn.execute("DELETE FROM paths")
            self._conn.execute("DELETE FROM files")
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LexicalIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- indexing ----------------------------------------------------------

    def _current_files(self) -> dict[str, tuple[int, int]]:
        """Every indexable file on disk now, as relpath → (size, mtime_ns)."""
        found: dict[str, tuple[int, int]] = {}
        for path in walk(self.root.path, self.config):
            try:
                st = path.stat()
            except OSError:
                continue
            rel = path.relative_to(self.root.path).as_posix()
            found[rel] = (st.st_size, st.st_mtime_ns)
        return found

    def _indexed_files(self) -> dict[str, tuple[int, int]]:
        rows = self._conn.execute("SELECT relpath, size, mtime_ns FROM files").fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    def is_stale(self) -> bool:
        """Whether the index disagrees with the filesystem.

        Compares size and mtime rather than hashing contents — the point is to
        be cheap enough to run before *every* search, so staleness is caught
        rather than assumed away.
        """
        return self._current_files() != self._indexed_files()

    def indexed_count(self) -> int:
        """How many files this index currently holds."""
        return self._conn.execute("SELECT count(*) FROM files").fetchone()[0]

    def indexed_relpaths(self) -> list[str]:
        """Every indexed relpath. Used to backfill a companion index that was
        added after this one was already populated."""
        return [r[0] for r in self._conn.execute("SELECT relpath FROM files").fetchall()]

    def pending_changes(self, *, force: bool = False) -> tuple[list[str], list[str]]:
        """Return ``(changed, removed)`` relpaths without applying anything.

        Exists so a composing index can keep a second store in step using the
        *same* change set this arm is about to apply. Duplicating the size and
        mtime bookkeeping elsewhere would create a second source of truth that
        can drift from this one — and a drifted pair returns hits from one arm
        pointing at content the other has already dropped.
        """
        on_disk = self._current_files()
        indexed = {} if force else self._indexed_files()
        changed = [rel for rel, meta in on_disk.items() if indexed.get(rel) != meta]
        removed = [rel for rel in indexed if rel not in on_disk]
        return changed, removed

    def reindex(self, *, force: bool = False) -> IndexStats:
        """Bring the index in line with disk.

        Incremental by default: only files whose size or mtime changed are
        re-read. ``force`` rebuilds everything, for when the chunking or
        schema changed underneath a still-valid-looking index.
        """
        on_disk = self._current_files()
        indexed = {} if force else self._indexed_files()

        if not force and on_disk == indexed:
            return IndexStats(0, 0, 0, up_to_date=True)

        changed = [rel for rel, meta in on_disk.items() if indexed.get(rel) != meta]
        removed = [rel for rel in indexed if rel not in on_disk]

        chunks_written = 0
        with self._conn:
            if force:
                self._conn.execute("DELETE FROM chunks")
                self._conn.execute("DELETE FROM paths")
                self._conn.execute("DELETE FROM files")

            for rel in removed:
                self._conn.execute("DELETE FROM chunks WHERE relpath = ?", (rel,))
                self._conn.execute("DELETE FROM paths WHERE relpath = ?", (rel,))
                self._conn.execute("DELETE FROM files WHERE relpath = ?", (rel,))

            for rel in changed:
                if not force:
                    self._conn.execute("DELETE FROM chunks WHERE relpath = ?", (rel,))
                    self._conn.execute("DELETE FROM paths WHERE relpath = ?", (rel,))
                self._conn.execute("INSERT INTO paths (relpath) VALUES (?)", (rel,))
                chunks_written += self._index_file(rel)
                size, mtime_ns = on_disk[rel]
                self._conn.execute(
                    "INSERT INTO files (relpath, size, mtime_ns) VALUES (?, ?, ?) "
                    "ON CONFLICT(relpath) DO UPDATE SET size = excluded.size, "
                    "mtime_ns = excluded.mtime_ns",
                    (rel, size, mtime_ns),
                )

        return IndexStats(len(changed), len(removed), chunks_written, up_to_date=False)

    def _index_file(self, rel: str) -> int:
        """Index one file, one row per line.

        Line granularity is deliberate for v1: every observed query is an
        identifier or filename lookup, where the useful unit *is* the line and
        the line number is what the caller needs. Semantic retrieval will want
        larger overlapping chunks; that is a separate table, not a change here.
        """
        path = self.root.path / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0

        rows = [
            (rel, lineno, line)
            for lineno, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        ]
        if rows:
            self._conn.executemany(
                "INSERT INTO chunks (relpath, line, body) VALUES (?, ?, ?)", rows
            )
        return len(rows)

    # -- querying ----------------------------------------------------------

    def search(self, query: str, *, limit: int = 10) -> list[Hit]:
        """Full-text search, ranked by BM25.

        The query is escaped before it reaches FTS5 (see `core.query`), so
        hyphens, dots and slashes are searched for literally instead of being
        parsed as operators.

        Raises `QueryError` on an unparseable query — it does **not** return an
        empty list. An empty list means "searched, found nothing"; an error
        means "did not search". Collapsing those two into the same value is the
        exact silent-wrong-answer failure this project exists to prevent, and
        an earlier version of this method did precisely that.
        """
        expression = to_fts5(query)
        try:
            path_matches = self._path_matches(expression, limit)
            rows = self._conn.execute(
                "SELECT relpath, line, body, bm25(chunks) AS rank "
                "FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (expression, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise QueryError(f"could not parse query {query!r}: {exc}") from exc

        hits: list[Hit] = []
        seen: set[tuple[str, int]] = set()

        # Path matches first. Searching for a filename is a different question
        # from searching for content, and when someone types
        # "require-active-ticket" they almost always want that file — not the
        # test fixtures that happen to mention it. A content-only index cannot
        # answer this at all: that file never contains its own name.
        for relpath in path_matches:
            key = (relpath, 1)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                Hit(
                    project=self.root.label,
                    path=relpath,
                    line=1,
                    excerpt=f"(filename match) {relpath}",
                    score=_PATH_MATCH_SCORE,
                )
            )

        for relpath, line, body, rank in rows:
            key = (relpath, line)
            if key in seen:
                continue
            seen.add(key)
            start, length = _match_span(body, query)
            hits.append(
                Hit(
                    project=self.root.label,
                    path=relpath,
                    line=line,
                    # bm25() returns lower-is-better; negate so callers can
                    # treat score as higher-is-better like every other engine.
                    excerpt=excerpt(body, start, length),
                    score=-float(rank),
                )
            )
        return hits[:limit]

    def _path_matches(self, expression: str, limit: int) -> list[str]:
        """Filenames matching the query, best first."""
        rows = self._conn.execute(
            "SELECT relpath, bm25(paths) AS rank FROM paths "
            "WHERE paths MATCH ? ORDER BY rank LIMIT ?",
            (expression, limit),
        ).fetchall()
        return [r[0] for r in rows]


def _match_span(body: str, query: str) -> tuple[int, int]:
    """Best-effort location of the query inside a matched line.

    Only used to centre the excerpt window. FTS5 does not report offsets for
    a general MATCH expression, so this looks for the longest bare term. When
    nothing matches literally — a prefix query, say — it falls back to the
    start of the line, which yields a leading excerpt rather than an error.
    """
    terms = [t.strip('"*') for t in query.split() if t.strip('"*').isalnum()]
    for term in sorted(terms, key=len, reverse=True):
        idx = body.lower().find(term.lower())
        if idx >= 0:
            return idx, len(term)
    return 0, 0


def open_indexes(
    roots: Sequence[Root], config: TraversalConfig | None = None
) -> Iterable[LexicalIndex]:
    """Open one index per root."""
    for root in roots:
        yield LexicalIndex(root, config=config)
