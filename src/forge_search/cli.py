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
import sys
from pathlib import Path

from .core.bench import BenchQuery, run_bench
from .core.embedding import HashingEmbedder, OnnxEmbedder
from .core.hybrid import HybridIndex, SearchMode
from .core.index import LexicalIndex
from .core.model import DEFAULT_MODEL, download, is_present, model_dir
from .core.query import QueryError
from .core.roots import Root, RootProvider, discover_roots
from .core.server import DEFAULT_SERVER_NAME, ForgeSearch

__all__ = ["main"]


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


def _resolve_roots(args: argparse.Namespace) -> list[Root]:
    explicit: list[Root] = []
    for raw in args.root or []:
        # "label=path" or just "path", in which case the directory name is the
        # label. Labels stay relative — never an absolute path (see roots.py).
        label, _, path_str = raw.partition("=")
        if not path_str:
            path_str, label = label, ""
        path = Path(path_str).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"not a directory: {path}")
        explicit.append(Root(label=label or path.name, path=path))

    discovery = discover_roots(
        explicit=explicit,
        providers=_providers(),
        start=Path(args.start).resolve() if args.start else None,
    )
    # Always report how roots were chosen. A wrong guess that stays silent is
    # the failure mode; announcing it costs one line.
    print(f"forge-search: {discovery.describe()}", file=sys.stderr)
    return list(discovery.roots)


def _embedder(args: argparse.Namespace):
    """Resolve an embedder, or None for lexical-only.

    Absence is a supported configuration, not a failure — so a missing model
    is reported and the lexical arm carries on, rather than aborting.
    """
    if not getattr(args, "semantic", False):
        return None
    if getattr(args, "hashing_embedder", False):
        return HashingEmbedder()
    if not is_present():
        print(
            "semantic requested but no model is present. Run `forge-search warm` "
            "to fetch it. Continuing with lexical-only search.",
            file=sys.stderr,
        )
        return None
    try:
        return OnnxEmbedder(model_dir(), dim=DEFAULT_MODEL.dim)
    except (FileNotFoundError, ImportError) as exc:
        print(f"semantic unavailable ({exc}); continuing lexical-only.", file=sys.stderr)
        return None


def _cmd_warm(args: argparse.Namespace) -> int:
    """Fetch the embedding model. Explicit by design — a search must never
    trigger a ~90 MB download."""
    try:
        directory = download(force=args.force)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"model ready: {directory}")
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    """Measure whether the semantic arm earns its cost on this corpus."""
    queries: list[BenchQuery] = []
    for raw in args.query or []:
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

    with HybridIndex(roots[0], embedder=_embedder(args)) as index:
        index.reindex()
        print(run_bench(index, queries, top_k=args.top_k).render())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    roots = _resolve_roots(args)
    search = ForgeSearch(
        roots=roots, server_name=args.name, embedder=_embedder(args)
    )
    try:
        search.run(transport=args.transport)
    finally:
        search.close()
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    for root in _resolve_roots(args):
        with LexicalIndex(root) as index:
            stats = index.reindex(force=args.force)
            state = "up to date" if stats.up_to_date else (
                f"{stats.files_indexed} indexed, {stats.files_removed} removed, "
                f"{stats.chunks_written} lines"
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


def _add_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        action="append",
        metavar="[LABEL=]PATH",
        help="A directory to index. Repeatable. Defaults to auto-discovery.",
    )
    parser.add_argument(
        "--start",
        metavar="DIR",
        help="Where auto-discovery begins. Defaults to the current directory.",
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
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="stdio (default) spawns per session; streamable-http serves many.",
    )
    serve.add_argument("--semantic", action="store_true", help="Enable the semantic arm")
    serve.add_argument("--hashing-embedder", action="store_true", help=argparse.SUPPRESS)
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
    search.add_argument("--hashing-embedder", action="store_true", help=argparse.SUPPRESS)
    search.set_defaults(func=_cmd_search)

    warm = sub.add_parser("warm", help="Fetch the embedding model (semantic arm)")
    warm.add_argument("--force", action="store_true", help="Re-download even if present")
    warm.set_defaults(func=_cmd_warm)

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
    bench.add_argument("--top-k", type=int, default=10)
    bench.add_argument("--semantic", action="store_true", help="Include the semantic arm")
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
