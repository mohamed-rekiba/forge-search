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

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .embedding import Embedder, serialize

__all__ = [
    "Chunk",
    "chunk_text",
    "summary_chunk",
    "breadcrumb",
    "structural_chunks",
    "structural_boundaries",
    "SemanticIndex",
    "vec_available",
    "CHUNK_LINES",
    "CHUNK_OVERLAP",
    "SUMMARY_SCAN_LINES",
]

#: Lines per chunk. Large enough to carry meaning, small enough that a hit
#: still points somewhere specific.
CHUNK_LINES = 12

#: Lines of overlap between consecutive chunks, so a boundary cannot sever
#: the context that makes a passage findable.
CHUNK_OVERLAP = 4


#: How many leading lines to scan for a file's descriptive header.
SUMMARY_SCAN_LINES = 40

#: Comment markers worth harvesting for a summary, across the languages this
#: indexes. Deliberately a small fixed set rather than per-language parsing —
#: the goal is a usable description, not a faithful AST.
_COMMENT_PREFIXES = ("#", "//", "--", "*", '"""', "'''", ";;")


@dataclass(frozen=True, slots=True)
class Chunk:
    start_line: int
    text: str


def summary_chunk(relpath: str, text: str) -> Chunk | None:
    """Build one undiluted chunk describing what a file *is*.

    This exists because of a measured failure. Indexing only sliding windows
    buried each file's description inside surrounding boilerplate, and
    mean-pooling averaged the signal away. Measured against the query
    "what stops me committing secrets" and its correct target:

        header comment alone ............ 0.484
        the 12-line window containing it  0.368   <- what was indexed
        path + header ................... 0.520   <- what this produces

    A 24% signal loss, on the one chunk that most identifies the file. Across
    16,726 chunks competing for ten slots, that difference decides whether the
    right file is findable at all — the first real-model benchmark scored 0%
    recall with the diluted version.

    Returns None when a file has no descriptive header worth indexing; a
    summary of nothing but a path adds noise rather than signal.
    """
    lines = text.splitlines()[:SUMMARY_SCAN_LINES]

    harvested: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            # A blank line after real content ends the header block. Before
            # any content it is just leading whitespace.
            if harvested:
                break
            continue
        if stripped.startswith("#!"):
            continue  # shebang: says nothing about purpose
        if not stripped.startswith(_COMMENT_PREFIXES):
            break  # first line of actual code ends the header
        cleaned = stripped.lstrip("#/-*;\"' ").strip()
        if cleaned:
            harvested.append(cleaned)

    if not harvested:
        return None

    # The path leads: it carries the filename, which is often the single most
    # descriptive token a file has, and it measured highest in the comparison
    # above.
    body = f"{relpath}\n" + "\n".join(harvested)
    return Chunk(start_line=1, text=body)


#: Patterns that name a region of a file, checked in order. Deliberately
#: regex rather than parsing: the goal is a locating label, not a faithful
#: syntax tree, and a wrong label costs a slightly worse breadcrumb rather
#: than a crash.
_CODE_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?def\s+(\w+)"),  # python
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"),  # js/ts
    re.compile(r"^\s*(?:export\s+)?class\s+(\w+)"),  # class
    re.compile(r"^\s*(\w+)\s*\(\)\s*\{"),  # shell function
    re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*="),  # ts const
)

#: Markdown headings, applied ONLY to markdown files.
#:
#: This is extension-gated because `#` is a comment marker in shell, Python,
#: YAML, and TOML — the majority of what gets indexed. Applied blindly, every
#: `# verify signature` in a shell script became a "heading", so a chunk's
#: breadcrumb pointed at an incidental comment instead of the enclosing
#: function. Caught by eyeballing the output on a real file: a chunk inside
#: `validate_token` was labelled `> verify signature`.
_MARKDOWN_HEADING = re.compile(r"^\s*#{1,6}\s+(.*\S)")

#: Extensions whose `#` lines are headings rather than comments.
_MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")


def _label_for(line: str, *, markdown: bool = False) -> str | None:
    """The region name a line declares, if any.

    `markdown` gates heading detection, because `#` means something entirely
    different in the languages that dominate this corpus.
    """
    if markdown:
        match = _MARKDOWN_HEADING.match(line)
        if match:
            return match.group(1).strip()
    for pattern in _CODE_PATTERNS:
        match = pattern.match(line)
        if match:
            return match.group(1).strip()
    return None


def _is_markdown(relpath: str | None) -> bool:
    return bool(relpath) and relpath.lower().endswith(_MARKDOWN_SUFFIXES)  # type: ignore[union-attr]


def breadcrumb(relpath: str, lines: list[str], upto: int) -> str:
    """`path > nearest enclosing heading or function` for a chunk.

    This is the deterministic, no-LLM form of contextual retrieval. The
    published technique generates per-chunk context with an LLM and reports a
    35–49% reduction in retrieval failures; prepending a structural breadcrumb
    captures the same mechanism — telling the embedder *where* a fragment lives
    — at zero inference cost.

    The evidence it works here is local: adding path + doc-comment context to
    one chunk per file moved semantic recall from 0% to 50% on this corpus.
    That fix reached only the summary chunk. This reaches every chunk, which
    is what the published method actually does.

    The breadcrumb is placed in the chunk *text*, not a metadata column, so it
    is seen by both the embedding and the full-text index rather than only one.
    """
    markdown = _is_markdown(relpath)
    label = None
    for i in range(min(upto, len(lines)) - 1, -1, -1):
        label = _label_for(lines[i], markdown=markdown)
        if label:
            break
    return f"{relpath} > {label}" if label else relpath


#: A structural region longer than this is windowed further. A 400-line
#: function embedded whole is as diluted as a 12-line window is contextless —
#: the same failure at the other extreme.
MAX_STRUCTURAL_LINES = 40

#: Below this, a region merges forward rather than being embedded alone. A
#: two-line helper is not enough text to place in meaning-space.
MIN_STRUCTURAL_LINES = 3


def structural_boundaries(lines: list[str], *, markdown: bool = False) -> list[int]:
    """Line indices where a new structural region begins.

    Fixed-line windows bear no relationship to where a function or section
    actually starts, so they routinely cut one in half — severing the very
    context that makes the text findable. Splitting on real boundaries is the
    documented fix (cAST, ACL Findings EMNLP 2025, ~4.3 points of Recall@5 on
    code retrieval).

    Regex rather than a parser is deliberate at this scale: a missed boundary
    degrades to a slightly worse chunk, whereas a parser adds a dependency and
    a per-language failure mode.
    """
    return [
        i
        for i, line in enumerate(lines)
        if _label_for(line, markdown=markdown) is not None
    ]


def _chunk(
    lines: list[str], start: int, body_lines: list[str], relpath: str | None
) -> Chunk:
    body = "\n".join(body_lines)
    if relpath is not None:
        body = f"{breadcrumb(relpath, lines, start + 1)}\n{body}"
    return Chunk(start_line=start + 1, text=body)


def structural_chunks(
    text: str,
    *,
    relpath: str | None = None,
    max_lines: int = MAX_STRUCTURAL_LINES,
    min_lines: int = MIN_STRUCTURAL_LINES,
) -> Iterator[Chunk]:
    """Chunk on structural boundaries, falling back to windows when none exist.

    Three cases, each for a reason:

    * **No boundaries** — plain prose, config, data. Falls back to line
      windows, the right shape for text with no structure to respect.
    * **Region longer than `max_lines`** — windowed *within* the region, so a
      long function is still split but every piece keeps its breadcrumb.
    * **Region shorter than `min_lines`** — merged forward. A two-line helper
      embedded alone is noise competing for a top-k slot.
    """
    lines = text.splitlines()
    if not lines:
        return

    boundaries = structural_boundaries(lines, markdown=_is_markdown(relpath))
    if not boundaries:
        yield from chunk_text(text, relpath=relpath)
        return

    # Anything before the first boundary (imports, a file header) is its own
    # leading region.
    starts = ([0] if boundaries[0] != 0 else []) + boundaries
    regions = list(zip(starts, starts[1:] + [len(lines)], strict=True))

    pending_start: int | None = None
    for start, end in regions:
        if pending_start is not None:
            start, pending_start = pending_start, None

        if 0 < end - start < min_lines:
            pending_start = start
            continue

        region = lines[start:end]
        if not "\n".join(region).strip():
            continue

        if len(region) <= max_lines:
            yield _chunk(lines, start, region, relpath)
            continue

        step = CHUNK_LINES - CHUNK_OVERLAP
        for offset in range(0, len(region), step):
            window = region[offset : offset + CHUNK_LINES]
            if window and "\n".join(window).strip():
                yield _chunk(lines, start + offset, window, relpath)
            if offset + CHUNK_LINES >= len(region):
                break

    if pending_start is not None:
        region = lines[pending_start:]
        if "\n".join(region).strip():
            yield _chunk(lines, pending_start, region, relpath)


def chunk_text(
    text: str,
    *,
    size: int = CHUNK_LINES,
    overlap: int = CHUNK_OVERLAP,
    relpath: str | None = None,
) -> Iterator[Chunk]:
    """Split text into overlapping line windows, each carrying a breadcrumb.

    Blank-only chunks are skipped — embedding whitespace produces a vector
    near the origin that matches everything weakly, which is worse than not
    indexing it.

    When `relpath` is given, every chunk is prefixed with its location. A
    12-line window taken out of context is frequently unidentifiable — the
    same dilution that made this corpus score 0% — and the breadcrumb is what
    tells the embedder which file and which function a fragment belongs to.
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
            if relpath is not None:
                body = f"{breadcrumb(relpath, lines, start + 1)}\n{body}"
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
        chunks = list(structural_chunks(text, relpath=relpath))
        # The summary leads, so the file's own description competes on its own
        # terms rather than diluted inside a sliding window. See summary_chunk.
        summary = summary_chunk(relpath, text)
        if summary is not None:
            chunks.insert(0, summary)
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
        # Marked as a query so a prefix-trained model applies the right role.
        # Falls back cleanly for embedders that do not take the argument.
        try:
            vector = self.embedder.embed([query], is_query=True)  # type: ignore[call-arg]
        except TypeError:
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
