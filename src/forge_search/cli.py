"""Command-line entry point.

Three subcommands:

  serve     run the MCP server (the normal use)
  index     build or refresh indexes without serving
  search    one-shot search, for checking behaviour without an MCP client

``search`` matters more than its size suggests: it is how you verify the tool
actually finds things in a real repository, without a client in the loop. The
bug that most needed finding in this project — hyphenated queries silently
returning nothing — was invisible to unit tests and obvious the moment a real
query ran against real content.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .core.bench import BenchQuery, run_bench
from .core.embedding import HashingEmbedder, OnnxEmbedder
from .core.hybrid import HybridIndex
from .core.index import LexicalIndex
from .core.model import download, is_present, model_dir, resolve_model, resolve_reranker
from .core.query import QueryError
from .core.rerank import CrossEncoderReranker
from .core.roots import Root, RootProvider, resolve_roots
from .core.server import DEFAULT_SERVER_NAME, ForgeSearch

__all__ = ["main"]

#: The most recent root resolution, so `serve` can surface it through MCP
#: metadata instead of stderr. A module-level slot rather than a return-value
#: change because every caller wants the roots and only one wants the summary.
_LAST_RESOLUTION: dict[str, object] = {}

#: The VS Code variable clients disagree about. Tolerated, never recommended.
_VSCODE_WORKSPACE = "${workspaceFolder}"

#: `${VAR}` or `$VAR` left in a path — a client that did not substitute it.
_UNEXPANDED = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*")


def _providers() -> list[RootProvider]:
    """Root providers available in this installation.

    Import failures are swallowed on purpose: an integration adapter is
    optional by definition, and a standalone install must not require one to
    be importable.
    """
    found: list[RootProvider] = []
    try:
        from .adapters.apexyard import ApexyardRootProvider
    except ImportError:  # pragma: no cover - adapter is optional
        pass
    else:
        found.append(ApexyardRootProvider())
    return found


def _resolve_roots(args: argparse.Namespace) -> list[Root]:  # noqa: D401
    """Roots are always explicit — there is no discovery.

    An absolute path, or `${workspaceFolder}` — which several MCP clients pass
    through unexpanded, so it is accepted as a shorthand for the working
    directory rather than failing. See README.md for the per-client table and
    why cwd is a fallback rather than the recommendation.

    Guessing was removed because the caller already knows the answer, and
    guessing wrongly is silent.
    """
    if not args.root:
        raise SystemExit(
            "no --root given. forge-search does not guess which project you "
            "mean.\n"
            "  --root /absolute/path/to/project\n"
            "  --root myproject=/absolute/path        (labelled)\n"
            "\nUse an absolute path. `${workspaceFolder}` is a VS Code "
            "variable and is not expanded by every MCP client."
        )

    explicit: list[Root] = []
    for raw in args.root:
        # "label=path" or just "path", in which case the directory name is the
        # label. Labels stay relative — never an absolute path (see roots.py).
        label, _, path_str = raw.partition("=")
        if not path_str:
            path_str, label = label, ""
        if path_str.startswith(_VSCODE_WORKSPACE):
            # Compatibility shim for `--root ${workspaceFolder}`, which several
            # MCP clients pass through unexpanded (see README's per-client
            # table). Falls back to the working directory, which for a stdio
            # MCP server is the project root in practice — verified against a
            # live Claude Code server — but is NOT guaranteed by its docs.
            #
            # Accepted because the resolved root is always printed at startup,
            # so a wrong cwd is visible rather than silent. Handles the
            # `${workspaceFolder}/sub` form too, since matching only the bare
            # string would leave that failing for a non-obvious reason.
            path_str = str(Path.cwd()) + path_str[len(_VSCODE_WORKSPACE):]
        path = Path(path_str).expanduser().resolve()
        if not path.is_dir():
            # An unexpanded `${...}` is the most likely cause by far, and the
            # bare "not a directory" that used to print here sent people
            # looking for a missing folder instead of a config that never got
            # substituted. Editors differ on which variables they expand, so
            # name the actual problem.
            if _UNEXPANDED.search(path_str):
                raise SystemExit(
                    f"--root contains an unexpanded variable: {path_str}\n"
                    "Your MCP client did not substitute it. `${workspaceFolder}` "
                    "is a VS Code variable and is NOT expanded by every client "
                    "(Claude Code's .mcp.json does environment-variable "
                    "expansion only).\nUse an absolute path, or a variable your "
                    "client documents as supported."
                )
            raise SystemExit(f"not a directory: {path}")
        explicit.append(Root(label=label or path.name, path=path))

    resolution = resolve_roots(
        explicit,
        adapters=_providers(),
        adapter_name=getattr(args, "adapter", None),
    )
    # The resolved set matters — a wrong root means every later search returns
    # plausible results from the wrong tree — but WHERE it is reported depends
    # on the transport.
    #
    # On stdio, stdout is the JSON-RPC channel, so the only place to write is
    # stderr, and MCP clients render every stderr line as a `[warning]`. A
    # permanent false warning on every launch is worse than useless: it trains
    # the operator to ignore the log, so a real warning gets ignored too.
    # `serve` therefore reports through MCP metadata instead (see
    # ForgeSearch.build), and stays silent here.
    #
    # Every other subcommand is an ordinary CLI run where stderr is just
    # stderr, and the line is printed.
    _LAST_RESOLUTION["value"] = resolution
    if getattr(args, "quiet_roots", False) and not getattr(args, "verbose", False):
        return list(resolution.roots)
    print(f"forge-search: {resolution.describe()}", file=sys.stderr)
    return list(resolution.roots)


def _embedder(args: argparse.Namespace):
    """Resolve an embedder, or None for lexical-only.

    Absence is a supported configuration, not a failure — so a missing model
    is reported and the lexical arm carries on, rather than aborting.
    """
    if not getattr(args, "semantic", False):
        return None
    if getattr(args, "hashing_embedder", False):
        return HashingEmbedder()
    spec = resolve_model(getattr(args, "model", None))
    if not is_present(spec):
        print(
            f"semantic requested but {spec.name} is not present. Run "
            f"`forge-search warm --model {getattr(args, 'model', None) or 'all-minilm'}` "
            "to fetch it. Continuing with lexical-only search.",
            file=sys.stderr,
        )
        return None
    try:
        return OnnxEmbedder(
            model_dir(spec),
            dim=spec.dim,
            query_prefix=spec.query_prefix,
            doc_prefix=spec.doc_prefix,
        )
    except (FileNotFoundError, ImportError) as exc:
        print(f"semantic unavailable ({exc}); continuing lexical-only.", file=sys.stderr)
        return None


def _reranker(args: argparse.Namespace) -> CrossEncoderReranker | None:
    """Build the reranker, or None.

    Same absence policy as the embedder: a missing model is reported and
    search carries on unreranked, rather than aborting. Reranking improves an
    order; it is never the difference between results and no results.
    """
    if not getattr(args, "rerank", False):
        return None
    spec = resolve_reranker(None)
    if not is_present(spec):
        print(
            f"rerank requested but {spec.name} is not present. Run "
            "`forge-search warm --reranker` to fetch it. Continuing unreranked.",
            file=sys.stderr,
        )
        return None
    try:
        return CrossEncoderReranker(model_dir(spec))
    except (FileNotFoundError, ImportError) as exc:
        print(f"rerank unavailable ({exc}); continuing unreranked.", file=sys.stderr)
        return None


def _cmd_warm(args: argparse.Namespace) -> int:
    """Fetch the embedding model. Explicit by design — a search must never
    trigger a ~90 MB download."""
    try:
        spec = (
            resolve_reranker(None)
            if getattr(args, "reranker", False)
            else resolve_model(args.model)
        )
        directory = download(spec, force=args.force)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"model ready: {directory}")
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    """Measure whether the semantic arm earns its cost on this corpus."""
    raw_queries = list(args.query or [])
    for fixture in args.fixture or []:
        # A benchmark that lives only in shell history is an anecdote. Fixture
        # files make a run repeatable and reviewable.
        for line in Path(fixture).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                raw_queries.append(line)

    queries: list[BenchQuery] = []
    for raw in raw_queries:
        text, _, expect = raw.partition("::")
        if not expect:
            print(
                f"bad fixture {raw!r} — expected 'query::expected/path'. Recall "
                "cannot be measured without a known-correct answer, and a "
                "harness without recall rewards returning nothing.",
                file=sys.stderr,
            )
            return 2
        queries.append(BenchQuery(query=text, expect_path=expect))

    roots = _resolve_roots(args)
    if not roots:
        print("no roots to benchmark", file=sys.stderr)
        return 2

    with HybridIndex(
        roots[0],
        embedder=_embedder(args),
        reranker=_reranker(args),
        index_dir=args.index_dir,
    ) as index:
        index.reindex()
        print(run_bench(index, queries, top_k=args.top_k).render())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    args.quiet_roots = True
    roots = _resolve_roots(args)
    resolution = _LAST_RESOLUTION.get("value")
    search = ForgeSearch(
        roots=roots,
        server_name=args.name,
        embedder=_embedder(args),
        reranker=_reranker(args),
        index_dir=args.index_dir,
        summary=resolution.describe() if resolution else None,
    )
    try:
        search.run(transport=args.transport)
    finally:
        search.close()
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    for root in _resolve_roots(args):
        with LexicalIndex(root, index_dir=args.index_dir) as index:
            stats = index.reindex(force=args.force)
            state = (
                "up to date"
                if stats.up_to_date
                else (
                    f"{stats.files_indexed} indexed, {stats.files_removed} removed, "
                    f"{stats.chunks_written} lines"
                )
            )
            print(f"{root.label}: {state}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    roots = _resolve_roots(args)
    search = ForgeSearch(roots=roots, embedder=_embedder(args))
    try:
        result = search._search(
            args.query, project=args.project, top_k=args.top_k, doc_only=args.docs
        )
    except QueryError as exc:
        # Exit non-zero. A malformed query is not an empty result, and the
        # shell should be able to tell them apart too.
        print(f"query error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        search.close()

    if reindexed := result.get("reindexed"):
        print(f"(reindexed: {', '.join(reindexed)})", file=sys.stderr)
    for warning in result.get("degraded") or []:
        print(f"(degraded) {warning}", file=sys.stderr)

    if not result["results"]:
        print("no matches", file=sys.stderr)
        return 1

    for hit in result["results"]:
        print(f"{hit['project']}/{hit['path']}:{hit['line']}: {hit['excerpt']}")
    if result["truncated"]:
        print("(truncated — raise --top-k for more)", file=sys.stderr)
    return 0


def _index_dir_flag(parser: argparse.ArgumentParser) -> None:
    """Where indexes are stored — see cache.index_dir_for for the trade-off."""
    parser.add_argument(
        "--index-dir",
        help=(
            "Store indexes here instead of the OS cache dir. Relative paths "
            "are per-root (e.g. --index-dir .forge_search). Gitignore it."
        ),
    )


def _add_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        action="append",
        metavar="[LABEL=]PATH",
        help="A directory to index, [LABEL=]PATH. Required. Repeatable.",
    )
    parser.add_argument(
        "--adapter",
        metavar="NAME",
        help="Force a portfolio adapter (e.g. apexyard) instead of detecting it.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge-search",
        description="Local code and docs search over one or many repositories.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the MCP server")
    _add_root_args(serve)
    serve.add_argument("--name", default=DEFAULT_SERVER_NAME, help="MCP server name")
    serve.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Print the resolved roots to stderr at startup. Off by default "
            "because MCP clients render every stderr line as a warning."
        ),
    )
    serve.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="stdio (default) spawns per session; streamable-http serves many.",
    )
    serve.add_argument("--semantic", action="store_true", help="Enable the semantic arm")
    serve.add_argument("--model", help="Model short name (default: all-minilm)")
    serve.add_argument("--hashing-embedder", action="store_true", help=argparse.SUPPRESS)
    serve.add_argument(
        "--rerank",
        action="store_true",
        help="Rerank results with a cross-encoder (needs `warm --reranker`)",
    )
    serve.set_defaults(func=_cmd_serve)

    index = sub.add_parser("index", help="Build or refresh indexes")
    _add_root_args(index)
    index.add_argument("--force", action="store_true", help="Rebuild from scratch")
    index.set_defaults(func=_cmd_index)

    search = sub.add_parser("search", help="One-shot search")
    _add_root_args(search)
    search.add_argument("query")
    search.add_argument("--project", help="Restrict to one root label")
    search.add_argument("--top-k", type=int, default=10)
    search.add_argument("--docs", action="store_true", help="Prose files only")
    search.add_argument("--semantic", action="store_true", help="Enable the semantic arm")
    search.add_argument("--model", help="Model short name (default: all-minilm)")
    search.add_argument("--hashing-embedder", action="store_true", help=argparse.SUPPRESS)
    search.add_argument(
        "--rerank",
        action="store_true",
        help="Rerank results with a cross-encoder (needs `warm --reranker`)",
    )
    search.set_defaults(func=_cmd_search)

    for _p in (serve, index, search):
        _index_dir_flag(_p)

    warm = sub.add_parser("warm", help="Fetch the embedding model (semantic arm)")
    warm.add_argument("--force", action="store_true", help="Re-download even if present")
    warm.add_argument("--model", help="Model short name (default: all-minilm)")
    warm.add_argument(
        "--reranker",
        action="store_true",
        help="Fetch the cross-encoder reranker instead of the embedding model",
    )
    warm.set_defaults(func=_cmd_warm, index_dir=None)

    bench = sub.add_parser(
        "bench", help="Measure whether the semantic arm earns its cost"
    )
    _add_root_args(bench)
    bench.add_argument(
        "--query",
        action="append",
        metavar="QUERY::EXPECTED/PATH",
        help="A query and the file that should be found. Repeatable.",
    )
    bench.add_argument(
        "--fixture",
        action="append",
        help="File of 'query::expected/path' lines (# comments ignored)",
    )
    _index_dir_flag(bench)
    bench.add_argument("--top-k", type=int, default=10)
    bench.add_argument("--semantic", action="store_true", help="Include the semantic arm")
    bench.add_argument("--model", help="Model short name (default: all-minilm)")
    bench.add_argument(
        "--rerank",
        action="store_true",
        help="Rerank results with a cross-encoder (needs `warm --reranker`)",
    )
    bench.add_argument(
        "--hashing-embedder",
        action="store_true",
        help="Use the built-in hashing embedder (no model download; plumbing only)",
    )
    bench.set_defaults(func=_cmd_bench)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
