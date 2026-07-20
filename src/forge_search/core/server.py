"""The MCP server.

Exposes three tools — ``search_code``, ``search_docs``, ``reindex`` — over
whichever transport the caller selects.

**On transport:** the MCP SDK (1.28.1) supports ``stdio``, ``sse``, and
``streamable-http`` natively via ``FastMCP.run(transport=...)``. No web
framework is needed for the HTTP case; adding one would duplicate what the SDK
already provides. ``stdio`` is the default because a per-session subprocess has
the simplest lifecycle — the client owns spawn and kill, there is no port, and
a crash is a non-zero exit rather than a hung socket. ``streamable-http`` earns
its keep when one warm server should serve many sessions, which matters once
the semantic arm loads a model at startup.

**On freshness:** every search checks the index against the filesystem first
and reindexes if it has drifted. That check is deliberately on the read path.
Delegating it — reminding a human or an agent to reindex after edits — is a
convention, and conventions nobody follows are what produced every defect this
project was built to fix. A stale index is the same failure shape: plausible
results, no error, nothing that announces the problem.

**On errors:** a bad query is returned as a *tool execution error*, not a
protocol error, so the model can read the message and correct itself. A
protocol error would fail the call at the transport layer where the model
never sees it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import FastMCP

from .embedding import Embedder
from .fusion import ArmWeights
from .hybrid import HybridIndex, SearchMode
from .index import Hit
from .query import QueryError
from .rerank import Reranker
from .roots import Root
from .traversal import TraversalConfig

__all__ = ["ForgeSearch", "DEFAULT_SERVER_NAME", "DOC_SUFFIXES"]

#: Default MCP server name. Configurable, because a standalone tool must not
#: assume an integration's identity — and because registering under another
#: product's name would collide with it wherever both are installed.
DEFAULT_SERVER_NAME = "forge-search"

#: What ``search_docs`` restricts to. Prose, not code.
DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt"})

#: Ceiling on ``top_k``. Without it, one call can return an unbounded result
#: set — which is the token cost this tool exists to avoid.
MAX_TOP_K = 50


@dataclass(slots=True)
class ForgeSearch:
    """Search over a set of roots, exposed as MCP tools."""

    roots: Sequence[Root]
    server_name: str = DEFAULT_SERVER_NAME
    config: TraversalConfig = field(default_factory=TraversalConfig)
    #: Supplying an embedder enables the semantic arm. Absent (the default)
    #: means lexical-only, which is a supported configuration rather than a
    #: degraded one.
    embedder: Embedder | None = None
    #: Optional final ranking stage. Absent, fused order stands.
    reranker: Reranker | None = None
    weights: ArmWeights = field(default_factory=ArmWeights)
    mode: str = SearchMode.HYBRID
    #: Where indexes live. None = shared OS cache dir. See cache.index_dir_for.
    index_dir: str | None = None
    _indexes: dict[str, HybridIndex] = field(default_factory=dict, init=False)

    # -- index lifecycle ---------------------------------------------------

    def _index_for(self, root: Root) -> HybridIndex:
        index = self._indexes.get(root.label)
        if index is None:
            index = HybridIndex(
                root,
                embedder=self.embedder,
                reranker=self.reranker,
                config=self.config,
                weights=self.weights,
                index_dir=self.index_dir,
            )
            self._indexes[root.label] = index
        return index

    def close(self) -> None:
        for index in self._indexes.values():
            index.close()
        self._indexes.clear()

    def _ensure_fresh(self, index: HybridIndex) -> bool:
        """Reindex if the index disagrees with disk. Returns True if it did."""
        if index.is_stale():
            index.reindex()
            return True
        return False

    # -- the search path ---------------------------------------------------

    def _search(
        self,
        query: str,
        *,
        project: str | None,
        top_k: int,
        doc_only: bool,
    ) -> dict[str, Any]:
        top_k = max(1, min(top_k, MAX_TOP_K))

        selected = [r for r in self.roots if project is None or r.label == project]
        if project is not None and not selected:
            known = ", ".join(r.label for r in self.roots) or "(none)"
            # A wrong project name must be loud. Silently searching everything
            # would answer a question the caller did not ask.
            raise ValueError(f"unknown project {project!r}; known roots: {known}")

        hits: list[Hit] = []
        degraded: list[str] = []
        reindexed: list[str] = []

        for root in selected:
            index = self._index_for(root)
            try:
                if self._ensure_fresh(index):
                    reindexed.append(root.label)
                found = index.search(query, limit=top_k, mode=self.mode)
            except QueryError:
                # Propagate — the caller wrote an unparseable query and needs
                # to know. Never silently degrade this into "no results".
                raise
            except OSError as exc:
                # One unreadable root must not fail the whole call. Report it
                # rather than dropping it silently.
                degraded.append(f"{root.label}: {exc}")
                continue

            if doc_only:
                found = [h for h in found if _is_doc(h.path)]
            hits.extend(found)

        hits.sort(key=lambda h: h.score, reverse=True)
        truncated = len(hits) > top_k
        hits = hits[:top_k]

        return {
            "results": [
                {
                    "project": h.project,
                    "path": h.path,
                    "line": h.line,
                    "excerpt": h.excerpt,
                    "score": round(h.score, 4),
                }
                for h in hits
            ],
            "truncated": truncated,
            "degraded": degraded or None,
            "reindexed": reindexed or None,
        }

    # -- MCP wiring --------------------------------------------------------

    def build(self) -> FastMCP:
        mcp = FastMCP(
            name=self.server_name,
            instructions=(
                "Search code and docs across one or more repositories. "
                "Queries are matched literally — hyphens, dots, and slashes in "
                "identifiers and paths are searched as written, not parsed as "
                "operators. Prefix a query with 'fts5:' for full-text "
                "expression syntax."
            ),
        )

        @mcp.tool(
            name="search_code",
            title="Search code",
            description=(
                "Full-text search across indexed repositories. Returns ranked "
                "excerpts with project, relative path, and line number. Use for "
                "finding identifiers, call sites, filenames, and precedents."
            ),
        )
        def search_code(
            query: str,
            project: str | None = None,
            top_k: int = 10,
        ) -> dict[str, Any]:
            """Search code and docs.

            Args:
                query: Text to find. Matched literally; prefix with 'fts5:' for
                    expression syntax.
                project: Restrict to one root by label. Omit to search all.
                top_k: Maximum results (capped at 50).
            """
            return self._search(query, project=project, top_k=top_k, doc_only=False)

        @mcp.tool(
            name="search_docs",
            title="Search documentation",
            description=(
                "Like search_code, but restricted to prose files "
                "(.md, .rst, .txt). Use when looking for written guidance "
                "rather than implementation."
            ),
        )
        def search_docs(
            query: str,
            project: str | None = None,
            top_k: int = 10,
        ) -> dict[str, Any]:
            """Search documentation only.

            Args:
                query: Text to find. Matched literally.
                project: Restrict to one root by label. Omit to search all.
                top_k: Maximum results (capped at 50).
            """
            return self._search(query, project=project, top_k=top_k, doc_only=True)

        @mcp.tool(
            name="reindex",
            title="Reindex",
            description=(
                "Rebuild the index for one or all roots. Rarely needed — "
                "searches detect staleness and reindex automatically. Use "
                "`force` after changing traversal settings."
            ),
        )
        def reindex(project: str | None = None, force: bool = False) -> dict[str, Any]:
            """Reindex one or all roots.

            Args:
                project: Restrict to one root by label. Omit for all.
                force: Rebuild from scratch rather than incrementally.
            """
            selected = [r for r in self.roots if project is None or r.label == project]
            if project is not None and not selected:
                known = ", ".join(r.label for r in self.roots) or "(none)"
                raise ValueError(f"unknown project {project!r}; known roots: {known}")

            report = {}
            for root in selected:
                stats = self._index_for(root).reindex(force=force)
                report[root.label] = {
                    "files_indexed": stats.files_indexed,
                    "files_removed": stats.files_removed,
                    "chunks_written": stats.chunks_written,
                    "up_to_date": stats.up_to_date,
                }
            return {"roots": report}

        return mcp

    def run(self, transport: str = "stdio") -> None:
        """Serve. See the module docstring on transport choice."""
        # The MCP SDK logs every request at INFO ("Processing request of type
        # ListToolsRequest"). On stdio that goes to stderr — the only channel
        # available, since stdout carries JSON-RPC — and MCP clients render
        # every stderr line as a warning. So routine traffic shows up in the
        # user's log looking like something went wrong.
        #
        # Raised to WARNING unless the operator asked for detail via the
        # standard env var, so real problems still surface.
        if not os.environ.get("FORGE_SEARCH_LOG_LEVEL"):
            logging.getLogger("mcp").setLevel(logging.WARNING)
        else:
            logging.getLogger("mcp").setLevel(
                os.environ["FORGE_SEARCH_LOG_LEVEL"].upper()
            )

        self.build().run(transport=transport)  # type: ignore[arg-type]


def _is_doc(path: str) -> bool:
    dot = path.rfind(".")
    return dot >= 0 and path[dot:].lower() in DOC_SUFFIXES
