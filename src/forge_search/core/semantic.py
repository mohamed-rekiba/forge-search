"""The semantic arm: chunking, vector storage, and nearest-neighbour search.

Kept in a separate module from the lexical index for a reason that is more
than tidiness — **this whole arm must be removable.** It ships on a parity
argument, not a measured one: the workload it targets showed zero conceptual
queries, and short keyword lookups are where embedding models perform worst.
So it is built as a bolt-on. Nothing in the lexical path imports this, the
optional dependencies are only imported here, and an installation without them
degrades to lexical-only rather than failing.

**Chunking is coarser than the lexical arm's, and has to be.** The lexical
index stores one row per line, because an identifier lookup wants the line and
its number. An embedding of a single line is mostly noise — too little context
to place it in meaning-space. So this uses overlapping windows of several
lines. Overlap matters: a chunk boundary falling mid-function would otherwise
split the very context that makes the text findable.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .embedding import Embedder, serialize

__all__ = [
    "Chunk",
    "chunk_text",
    "SemanticIndex",
    "vec_available",
    "CHUNK_LINES",
    "CHUNK_OVERLAP",
]

#: Lines per chunk. Large enough to carry meaning, small enough that a hit
#: still points somewhere specific.
CHUNK_LINES = 12

#: Lines of overlap between consecutive chunks, so a boundary cannot sever
#: the context that makes a passage findable.
CHUNK_OVERLAP = 4


@dataclass(frozen=True, slots=True)
class Chunk:
    start_line: int
    text: str


def chunk_text(
    text: str, *, size: int = CHUNK_LINES, overlap: int = CHUNK_OVERLAP
) -> Iterator[Chunk]:
    """Split text into overlapping line windows.

    Blank-only chunks are skipped — embedding whitespace produces a vector
    near the origin that matches everything weakly, which is worse than not
    indexing it.
    """
    if overlap >= size:
        raise ValueError("overlap must be smaller than chunk size")

    lines = text.splitlines()
    if not lines:
        return

    step = size - overlap
    for start in range(0, len(lines), step):
        window = lines[start : start + size]
        if not window:
            break
        body = "\n".join(window)
        if body.strip():
            yield Chunk(start_line=start + 1, text=body)
        if start + size >= len(lines):
            break


def vec_available() -> bool:
    """Whether sqlite-vec can be loaded in this installation.

    Checked rather than assumed: the extension is an optional dependency, and
    some Python builds ship a sqlite3 without extension loading enabled at all.
    A search must degrade to lexical-only in that case, not crash.
    """
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    try:
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
    except AttributeError, sqlite3.OperationalError:
        return False
    try:
        import sqlite_vec

        sqlite_vec.load(conn)
        conn.execute("SELECT vec_version()").fetchone()
        return True
    except Exception:
        return False
    finally:
        conn.close()


class SemanticIndex:
    """Vector index over one root, stored beside the lexical index."""

    def __init__(self, db_path: Path, embedder: Embedder) -> None:
        self.db_path = db_path
        self.embedder = embedder
        self._conn = sqlite3.connect(db_path)
        self._conn.enable_load_extension(True)

        import sqlite_vec

        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0("
            f"embedding float[{embedder.dim}])"
        )
        # vec0 keys on rowid, so the mapping back to a location lives here.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vector_meta ("
            "rowid INTEGER PRIMARY KEY, relpath TEXT NOT NULL, line INTEGER NOT NULL)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS vector_meta_relpath ON vector_meta(relpath)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SemanticIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def drop_file(self, relpath: str) -> None:
        rows = self._conn.execute(
            "SELECT rowid FROM vector_meta WHERE relpath = ?", (relpath,)
        ).fetchall()
        for (rowid,) in rows:
            self._conn.execute("DELETE FROM vectors WHERE rowid = ?", (rowid,))
        self._conn.execute("DELETE FROM vector_meta WHERE relpath = ?", (relpath,))

    def index_file(self, relpath: str, text: str) -> int:
        """Embed and store one file's chunks. Returns the count written.

        Embedding is the expensive half of indexing, so chunks are batched
        into a single call rather than embedded one at a time.
        """
        chunks = list(chunk_text(text))
        if not chunks:
            return 0

        vectors = self.embedder.embed([c.text for c in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            cursor = self._conn.execute(
                "INSERT INTO vector_meta (relpath, line) VALUES (?, ?)",
                (relpath, chunk.start_line),
            )
            self._conn.execute(
                "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
                (cursor.lastrowid, serialize(vector)),
            )
        return len(chunks)

    def commit(self) -> None:
        self._conn.commit()

    def search(self, query: str, *, limit: int = 10) -> list[tuple[str, int, float]]:
        """Nearest neighbours as ``(relpath, line, distance)``, closest first."""
        vector = self.embedder.embed([query])
        if not vector:
            return []

        rows = self._conn.execute(
            "SELECT v.rowid, distance, m.relpath, m.line "
            "FROM vectors v JOIN vector_meta m ON m.rowid = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (serialize(vector[0]), limit),
        ).fetchall()
        return [(relpath, line, float(dist)) for _, dist, relpath, line in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM vector_meta").fetchone()[0]
