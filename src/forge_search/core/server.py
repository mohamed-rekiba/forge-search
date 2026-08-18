"""The MCP server.

Exposes three tools — ``search_code``, ``search_docs``, ``reindex`` — over
whichever transport the caller selects.

**On transport:** the MCP SDK (2.x) supports ``stdio``, ``sse``, and
``streamable-http`` natively via ``MCPServer.run(transport=...)``. No web
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp import types
from mcp.server import ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer

from .embedding import Embedder
from .fusion import ArmWeights
from .hybrid import HybridIndex, SearchMode
from .index import Hit
from .query import QueryError
from .rerank import Reranker
from .roots import Root
from .traversal import TraversalConfig

#: The transports the SDK supports. Spelled out as a Literal rather than `str`
#: because mcp 2.x overloads `MCPServer.run` per transport — an unconstrained
#: `str` matches no overload, and the previous `# type: ignore` merely hid that
#: the type was wider than reality. The CLI already restricts the value to
#: these three via argparse `choices`, so this makes the annotation agree with
#: the behaviour instead of papering over it.
Transport = Literal["stdio", "sse", "streamable-http"]

#: Log levels in ascending severity, which is what makes `.index()` comparable
#: in `_announce`. Membership must match `mcp.types.LoggingLevel` exactly — a
#: level the SDK accepts but this tuple omits raises `ValueError` from
#: `.index()`, and that call sits *above* the `_announced = True` assignment, so
#: the flag never gets set and the failure repeats on every subsequent request.
#: `emergency` was missing for exactly that reason: a client that set it got a
#: cheerful empty response and then a permanently broken server, surfacing to
#: the user as the *tool* failing rather than as a logging problem.
#:
#: Exported so `tests/test_server.py` can pin both halves independently:
#: set-equality against the SDK's vocabulary (membership is the SDK's), and
#: exact-tuple equality plus emit/suppress assertions (order is ours). Both are
#: needed — sorting this tuple alphabetically keeps every member and silently
#: inverts the filter, so a set check alone passes while the behaviour breaks.
LOG_LEVELS: tuple[str, ...] = (
    "debug",
    "info",
    "notice",
    "warning",
    "error",
    "critical",
    "alert",
    "emergency",
)

__all__ = [
    "ForgeSearch",
    "DEFAULT_SERVER_NAME",
    "DOC_SUFFIXES",
    "LOG_LEVELS",
    "Transport",
]

#: Default MCP server name. Configurable, because a standalone tool must not
#: assume an integration's identity — and because registering under another
#: product's name would collide with it wherever both are installed.
DEFAULT_SERVER_NAME = "forge-search"

#: What ``search_docs`` restricts to. Prose, not code.
DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt"})

#: Ceiling on ``top_k``. Without it, one call can return an unbounded result
#: set — which is the token cost this tool exists to avoid.
MAX_TOP_K = 50

#: Upper bound on how far a `doc_only` search will dig past the ranked window
#: looking for documents. Reached only when a query matches overwhelmingly in
#: code; the search then reports itself truncated rather than implying the
#: thin result set is the whole truth.
_FETCH_CEILING = 2000


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
    #: One line naming the resolved roots. Surfaced in the server
    #: instructions rather than on stderr, where an MCP client would render
    #: it as a warning on every launch.
    summary: str | None = None
    #: Re-runs root resolution on demand. Present, an unknown project name
    #: triggers one re-resolution before it is allowed to fail — so a project
    #: registered *after* the server started (a new portfolio clone, say) is
    #: found without a restart. Absent (the default), the root set is fixed for
    #: the process, which is correct for a standalone server given an explicit
    #: `--root`. See `_resolve_selection`.
    root_resolver: Callable[[], Sequence[Root]] | None = None
    _indexes: dict[str, HybridIndex] = field(default_factory=dict, init=False)
    _announced: bool = field(default=False, init=False)
    _min_log_level: str = field(default="info", init=False)

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

    # -- root selection ----------------------------------------------------

    def _resolve_selection(self, project: str | None) -> list[Root]:
        """The roots a request targets — all of them, or the one named.

        A portfolio's project set is not fixed for the life of the server: a
        clone can be registered after startup. Treating "unknown project" as a
        permanent error would push the caller to restart the server — the same
        "rely on a human to remember" convention that freshness-on-read exists
        to remove. So a name that is not yet known triggers one re-resolution
        of the root set before it is allowed to fail. If the project genuinely
        does not exist, the error still fires, still names the known roots.

        A freshly discovered root persists in `self.roots`, so an unnamed
        (search-all) request that follows will include it too — the first
        reference by name is what makes it visible, not a restart.
        """
        selected = [r for r in self.roots if project is None or r.label == project]
        if project is not None and not selected and self.root_resolver is not None:
            self._refresh_roots()
            selected = [r for r in self.roots if r.label == project]
        if project is not None and not selected:
            known = ", ".join(r.label for r in self.roots) or "(none)"
            # A wrong project name must be loud. Silently searching everything
            # would answer a question the caller did not ask.
            raise ValueError(f"unknown project {project!r}; known roots: {known}")
        return selected

    def _refresh_roots(self) -> None:
        """Re-run root resolution to pick up projects added since startup.

        Indexes are cached by label, so roots already known keep their open
        handles and nothing is rebuilt — only newly-appeared labels cost
        anything. A resolver returning fewer roots than before does not drop
        cached indexes; they simply stop being selectable, and `close()` still
        reaches them.
        """
        if self.root_resolver is None:
            return
        self.roots = list(self.root_resolver())

    def _ensure_fresh(self, index: HybridIndex) -> bool:
        """Reindex if the index disagrees with disk. Returns True if it did."""
        if index.is_stale():
            index.reindex()
            return True
        return False

    # -- the search path ---------------------------------------------------

    def _fetch(
        self, index: HybridIndex, query: str, want: int, *, doc_only: bool
    ) -> tuple[list[Hit], bool]:
        """Retrieve at least `want` post-filter hits, or prove none remain.

        A `doc_only` filter cannot be applied after the limit. The ranked
        window fills with code hits, the filter empties it, and the caller is
        told "no documents match" while ten documents sit unread in the index.
        So the fetch widens until enough survive the filter or the index is
        exhausted — `len(raw) < limit` being the proof of exhaustion.

        Returns the hits, and whether the search stopped at `_FETCH_CEILING`
        with the index still not exhausted. In that case more may exist, and
        saying so is the whole point: an incomplete answer that presents
        itself as complete is the failure this project exists to prevent.
        """
        limit = want
        while True:
            raw = index.search(query, limit=limit, mode=self.mode)
            kept = [h for h in raw if _is_doc(h.path)] if doc_only else raw
            if len(kept) >= want or len(raw) < limit:
                return kept, False
            if limit >= _FETCH_CEILING:
                return kept, True
            limit = min(limit * 4, _FETCH_CEILING)

    def _search(
        self,
        query: str,
        *,
        project: str | None,
        top_k: int,
        doc_only: bool,
    ) -> dict[str, Any]:
        top_k = max(1, min(top_k, MAX_TOP_K))

        selected = self._resolve_selection(project)

        hits: list[Hit] = []
        degraded: list[str] = []
        reindexed: list[str] = []
        capped = False

        # Fetch one more than asked for, so "is there anything past this
        # page?" is answered by the data. Fetching exactly `top_k` makes a
        # complete set and a clipped one indistinguishable — which is how
        # `truncated` came to be a constant `false` for any single-root
        # deployment: every root was already capped at `top_k` in SQL, so the
        # comparison it was computed from could never be true.
        want = top_k + 1

        for root in selected:
            index = self._index_for(root)
            try:
                if self._ensure_fresh(index):
                    reindexed.append(root.label)
                found, root_capped = self._fetch(index, query, want, doc_only=doc_only)
            except QueryError:
                # Propagate — the caller wrote an unparseable query and needs
                # to know. Never silently degrade this into "no results".
                raise
            except OSError as exc:
                # One unreadable root must not fail the whole call. Report it
                # rather than dropping it silently.
                degraded.append(f"{root.label}: {exc}")
                continue

            capped = capped or root_capped
            hits.extend(found)

        hits.sort(key=lambda h: h.score, reverse=True)
        truncated = capped or len(hits) > top_k
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

    def build(self) -> MCPServer:
        mcp = MCPServer(
            name=self.server_name,
            instructions=(
                "Search code and docs across one or more repositories. "
                "Queries are matched literally — hyphens, dots, and slashes in "
                "identifiers and paths are searched as written, not parsed as "
                "operators. Prefix a query with 'fts5:' for full-text "
                "expression syntax."
            ),
        )

        # Advertise the `logging` capability. The SDK only declares it when a
        # `logging/setLevel` handler is registered, and MCPServer registers
        # none — so without this the server sends notifications/message that a
        # spec-compliant client is entitled to drop, and VS Code does exactly
        # that. The notification looked correct on the wire and still reached
        # nobody.
        #
        # mcp 2.x removed the 1.x `@server.set_logging_level()` decorator in
        # favour of a generic `add_request_handler(method, params_type,
        # handler)`. The capability check itself is unchanged in substance:
        # `get_capabilities()` looks for the literal `"logging/setLevel"` key in
        # the handler table, so registering it is still what flips the
        # capability on. Reaching through `_lowlevel_server` stays a private-API
        # dependency — the SDK offers no public seam for this — so it is pinned
        # by `tests/test_serve_smoke.py`, which asserts the capability is
        # actually advertised rather than that this line exists.
        async def _set_level(
            ctx: ServerRequestContext[Any],
            params: types.SetLevelRequestParams,
        ) -> types.EmptyResult:
            # Honoured for real: a client asking for `warning` should not keep
            # receiving `info`. Accepting the request and ignoring it would be
            # its own quiet lie.
            self._min_log_level = params.level
            return types.EmptyResult()

        mcp._lowlevel_server.add_request_handler(
            "logging/setLevel", types.SetLevelRequestParams, _set_level
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
        async def search_code(
            ctx: Context,
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
            await self._announce(ctx)
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
        async def search_docs(
            ctx: Context,
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
            await self._announce(ctx)
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
        async def reindex(
            ctx: Context, project: str | None = None, force: bool = False
        ) -> dict[str, Any]:
            """Reindex one or all roots.

            Args:
                project: Restrict to one root by label. Omit for all.
                force: Rebuild from scratch rather than incrementally.
            """
            await self._announce(ctx)
            selected = self._resolve_selection(project)

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

    async def _announce(self, ctx: Context) -> None:
        """Report the active configuration once per session, as `info`.

        The only other channel a stdio server has is stderr, and MCP clients
        render every stderr line as `[warning]` — a healthy server should not
        print a warning on every launch. `notifications/message` is the
        protocol's actual logging channel and carries a level, so this arrives
        as information.

        It cannot be sent at startup: there is no session to send it on until
        a request arrives. So it rides the first tool call, once.
        """
        if self._announced or not self.summary:
            return
        # Ordered by severity; anything below the client's requested level is
        # dropped rather than sent and ignored. See LOG_LEVELS on why membership
        # is pinned to the SDK's vocabulary by a test.
        if LOG_LEVELS.index("info") < LOG_LEVELS.index(self._min_log_level):
            self._announced = True
            return
        self._announced = True
        try:
            await ctx.info(f"Active configuration — {self.summary}")
        except Exception:  # pragma: no cover - a client may not accept logs
            # Never let a log notification break a search.
            pass

    def run(self, transport: Transport = "stdio") -> None:
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

        self.build().run(transport=transport)


def _is_doc(path: str) -> bool:
    dot = path.rfind(".")
    return dot >= 0 and path[dot:].lower() in DOC_SUFFIXES
