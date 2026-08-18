"""Command-line entry point.

Subcommands:

  serve     run the MCP server (the normal use)
  index     build or refresh indexes without serving
  reindex   refresh, optionally skipping indexes refreshed recently
  doctor    report index health; non-zero only for an unreadable index
  search    one-shot search, for checking behaviour without an MCP client
  warm      download the embedding model ahead of first use
  bench     measure whether the semantic arm earns its cost

``search`` matters more than its size suggests: it is how you verify the tool
actually finds things in a real repository, without a client in the loop. The
bug that most needed finding in this project — hyphenated queries silently
returning nothing — was invisible to unit tests and obvious the moment a real
query ran against real content.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from .core.bench import BenchQuery, run_bench
from .core.cache import index_path_for
from .core.embedding import HashingEmbedder, OnnxEmbedder
from .core.hybrid import HybridIndex
from .core.index import LexicalIndex, inspect_index
from .core.model import download, is_present, model_dir, resolve_model, resolve_reranker
from .core.query import QueryError
from .core.rerank import CrossEncoderReranker
from .core.roots import Resolution, Root, resolve_roots
from .core.server import DEFAULT_SERVER_NAME, ForgeSearch

__all__ = ["main"]

#: The most recent root resolution, so `serve` can report it without every
#: caller having to thread it through. A module-level slot rather than a
#: return-value change because every caller wants the roots and only one
#: wants the summary.
_LAST_RESOLUTION: Resolution | None = None

#: The VS Code variable clients disagree about. Tolerated, never recommended.
#:
#: `.` used to be listed here too, and it never did anything: for every path the
#: shim matched, splicing cwd on produced the same result `Path.resolve()` gives
#: anyway. What it did do was match `.smoke/corpus` and `..` on a bare prefix and
#: mangle both. Deleting the entry is the whole fix; ordinary relative paths were
#: already handled correctly one line below.
_CURRENT_WORKSPACE = ["${workspaceFolder}"]

#: `${VAR}` or `$VAR` left in a path — a client that did not substitute it.
_UNEXPANDED = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*")


def _parse_sub_projects(raw: str | None) -> list[str]:
    """Split `--sub-projects=a,b` into `["a", "b"]`.

    A list from the start, not a scalar promoted to one later: a
    scalar-to-list change would be a second breaking CLI change, so
    comma-splitting is the whole cost of supporting more than one now. Empty
    segments (a trailing comma, an empty flag value) are dropped rather than
    yielding a directory named "".

    Segments are stripped. `--sub-projects "packages, apps"` is what a person
    writes, and without this the second becomes a directory named " apps",
    which resolves to nothing and is skipped — while the startup line still
    echoes the raw string back, reporting both and indexing one. Silent, and
    actively reassuring, which is the shape of failure this project exists to
    remove.
    """
    if not raw:
        return []
    return [stripped for part in raw.split(",") if (stripped := part.strip())]


def _names_the_workspace(path_str: str, prefix: str) -> bool:
    """Whether a root path is the cwd shim rather than a path that starts alike.

    The prefix has to end at a path boundary, so `${workspaceFolder}` and
    `${workspaceFolder}/sub` match while `${workspaceFolder}suffix` does not —
    that last one is a different path, and treating it as the shim would splice
    cwd onto `suffix` with no separator. Which is precisely what the old bare
    `startswith` did to `.smoke/corpus` back when `.` was on the list.
    """
    if path_str == prefix:
        return True
    if not path_str.startswith(prefix):
        return False
    # os.sep and os.altsep rather than a literal "/": a client on Windows may
    # hand back either separator.
    return path_str[len(prefix)] in (os.sep, os.altsep or os.sep)


def _explicit_roots(args: argparse.Namespace) -> list[Root]:
    """The roots the caller named, before any `--sub-projects` expansion.

    Parsing lives here, apart from `_resolve_roots`, so it can be re-run: the
    `serve` command hands a closure over this to the server, which calls it to
    re-resolve roots when a project registered after startup is first named
    (see ForgeSearch.root_resolver). The caller's `--root` set does not change,
    but each named sub-projects directory is re-scanned every call, so a clone
    that appeared since startup is picked up.
    """
    if not args.root:
        # Fall back to the working directory, and SAY SO.
        #
        # The recorded objection to inferring a root is that a wrong guess is
        # *silent*: the wrong tree gets indexed and every later search returns
        # plausible results from somewhere else, with nothing to look at. That
        # objection is about the silence, not about the fallback -- so the
        # fallback names the absolute path it resolved, on stderr, every time.
        # A stated default can be checked against the tree the caller meant;
        # a silent one cannot, and that is the whole of the difference.
        #
        # Hard-failing here instead is not free. Callers that run
        # `doctor` / `reindex` unattended from inside a project -- shell hooks,
        # session-start probes -- pass no `--root` at all, and read the failure
        # as "search is unavailable" rather than as "you forgot a flag".
        cwd = Path.cwd().resolve()
        print(
            f"no --root given; using the working directory: {cwd}\n"
            "  pass --root /absolute/path to search a different project.",
            file=sys.stderr,
        )
        args.root = [str(cwd)]

    explicit: list[Root] = []
    for raw in args.root:
        # "label=path" or just "path", in which case the directory name is the
        # label. Labels stay relative — never an absolute path (see roots.py).
        label, _, path_str = raw.partition("=")
        if not path_str:
            path_str, label = label, ""
        for _prefix in _CURRENT_WORKSPACE:
            if _names_the_workspace(path_str, _prefix):
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
                path_str = str(Path.cwd()) + path_str[len(_prefix) :]
                break
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
    return explicit


def _resolve_roots(args: argparse.Namespace) -> list[Root]:  # noqa: D401
    """Roots are always explicit — there is no discovery.

    An absolute path, or `${workspaceFolder}` — which several MCP clients pass
    through unexpanded, so it is accepted as a shorthand for the working
    directory rather than failing. See README.md for the per-client table and
    why cwd is a fallback rather than the recommendation.

    Guessing was removed because the caller already knows the answer, and
    guessing wrongly is silent.
    """
    try:
        resolution = resolve_roots(
            _explicit_roots(args),
            sub_projects=_parse_sub_projects(getattr(args, "sub_projects", None)),
        )
    except ValueError as exc:
        # A colliding label is a caller mistake — two directories with the same
        # name — so it gets the same clean one-line exit every other bad-input
        # path here gets. Core raises rather than exiting because core is also
        # a library; turning that into a message is this boundary's job. A
        # traceback would bury the one sentence that says which two paths clash.
        raise SystemExit(str(exc)) from exc
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
    global _LAST_RESOLUTION
    _LAST_RESOLUTION = resolution
    if getattr(args, "quiet_roots", False) and not getattr(args, "verbose", False):
        return list(resolution.roots)
    print(f"forge-search: {resolution.describe()}", file=sys.stderr)
    return list(resolution.roots)


def _index_dir(args: argparse.Namespace) -> str | None:
    """Where indexes go: `--index-dir` if given, else `.forge_search` per root.

    Used to have a middle tier here — an adapter could move the default for a
    tree shape it understood. With no adapters, nothing supplies that
    preference any more, so the explicit flag is the only override left.
    """
    return getattr(args, "index_dir", None)


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
        index_dir=_index_dir(args),
    ) as index:
        index.reindex()
        print(run_bench(index, queries, top_k=args.top_k).render())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    # Print to stderr unless explicitly silenced. This is the ONLY channel
    # proven to reach a VS Code MCP output panel — its own logs show
    # `[warning] [server stderr] …`, so stderr arrives; a notifications/message
    # at level `info` did not appear at all across three attempts.
    #
    # The `[warning]` label is the client's, not ours: stderr carries no
    # severity, so a client has nothing to derive one from. Visible and
    # mislabelled beats correctly-labelled and invisible — an operator who
    # cannot see which root is indexed has no way to catch the wrong tree.
    args.quiet_roots = getattr(args, "quiet", False)
    roots = _resolve_roots(args)
    resolution = _LAST_RESOLUTION

    # Re-resolve on demand so a project registered after the server started is
    # found without a restart. Each named sub-projects directory is re-scanned
    # on every call, so re-running resolution with the same `--root` picks up
    # a clone that appeared since startup. Kept quiet — this fires mid-session,
    # and its stderr would be rendered as a spurious `[warning]` by the client
    # on every discovery.
    sub_projects = _parse_sub_projects(getattr(args, "sub_projects", None))

    def _reresolve() -> list[Root]:
        return list(resolve_roots(_explicit_roots(args), sub_projects=sub_projects).roots)

    search = ForgeSearch(
        roots=roots,
        server_name=args.name,
        embedder=_embedder(args),
        reranker=_reranker(args),
        index_dir=_index_dir(args),
        summary=resolution.describe() if resolution else None,
        root_resolver=_reresolve,
    )
    try:
        search.run(transport=args.transport)
    finally:
        search.close()
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    for root in _resolve_roots(args):
        with LexicalIndex(root, index_dir=_index_dir(args)) as index:
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


def _cmd_reindex(args: argparse.Namespace) -> int:
    """Refresh indexes, optionally only if the last refresh is old enough.

    Same work as `index`; the difference is `--if-stale`, which makes the
    command cheap to call unconditionally from a session-start hook.

    Note which staleness this gate reads. `reindex()` already detects *content*
    staleness, but only by walking the whole tree — so it cannot answer "is this
    worth doing?" without doing most of it. `--if-stale` reads one metadata row
    instead. The trade is explicit: inside the window, a genuinely changed tree
    is skipped. That is safe here because search repairs content staleness on
    the read path anyway; this command exists to keep indexes warm, not to be
    the thing correctness depends on.
    """
    index_dir = _index_dir(args)
    window = getattr(args, "if_stale", None)
    now = time.time()

    for root in _resolve_roots(args):
        with LexicalIndex(root, index_dir=index_dir) as index:
            # `--force` outranks the gate. It means "rebuild", and everywhere
            # else force bypasses a cheap-skip path, so honouring the window
            # ahead of it would make `--force --if-stale N` a silent no-op.
            if window is not None and not args.force:
                last = index.last_refreshed_at()
                # A missing stamp means due, never "skip forever" — an index
                # predating the stamp must still get refreshed.
                if last is not None and (now - last) < window:
                    age = int(now - last)
                    print(f"{root.label}: fresh ({age}s ago), skipped")
                    continue

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


def _semantic_status() -> str:
    """Whether the semantic arm could run, without making it run.

    Importing rather than checking for the distribution is deliberate: a wheel
    can be installed and still fail to load (onnxruntime's native library is the
    usual case), and "installed but unusable" is exactly what a probe should
    catch. It costs ~130ms, which is well under the tree walk below it.

    No model is fetched — `warm` exists for that, and a probe that downloads
    ~90 MB inside a session-start hook would be a trap.
    """
    try:
        import onnxruntime  # noqa: F401
        import sqlite_vec  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError as exc:
        # `exc.name` is None when a package raises ImportError from its own
        # code rather than failing to be found — the normal shape of an
        # onnxruntime native-library failure. Reporting "None not installed"
        # there would be worse than saying nothing, so fall back to the message.
        detail = exc.name or str(exc) or "reason unknown"
        return f"unavailable ({detail}) — lexical-only"
    return "available"


def _refresh_note(stamp: float | None) -> str:
    """What `--if-stale` will be looking at, in a form worth reading.

    Absent is its own answer, not a very old timestamp: an index built before
    the stamp existed has none, and so does one whose rows a schema bump wiped.
    `--if-stale` treats both as due, and reading them as "refreshed long ago"
    would suggest the opposite.
    """
    if stamp is None:
        return "never refreshed"

    seconds = max(0, int(time.time() - stamp))
    if seconds < 60:
        return f"refreshed {seconds}s ago"
    if seconds < 3600:
        return f"refreshed {seconds // 60}m ago"
    if seconds < 86400:
        return f"refreshed {seconds // 3600}h ago"
    return f"refreshed {seconds // 86400}d ago"


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report health without changing any of it.

    Exit 0 means "this install can answer queries". A missing or stale index is
    NOT a failure — search creates and refreshes indexes on the read path, so
    both are ordinary states, and reporting them as errors would make the exit
    code useless as a liveness probe.

    Non-zero is reserved for a root whose index exists but cannot be read — an
    unreadable directory, a corrupt database. Those do NOT self-heal, so
    reporting them the same way as an absent index would be the one outcome a
    probe must never produce: healthy while broken.

    Inspection goes through `inspect_index`, never `LexicalIndex`. That
    constructor creates its directory, connects read-write, and runs
    `_migrate_if_stale`, which DELETEs every row on a schema-version mismatch —
    so using it here would wipe an index built by a previous release and then
    truthfully report it as empty.
    """
    print(f"semantic arm: {_semantic_status()}")

    roots = _resolve_roots(args)
    index_dir = _index_dir(args)

    sub_projects = getattr(args, "sub_projects", None)
    print(
        f"roots: {len(roots)}"
        + (f" (sub-projects: {sub_projects})" if sub_projects else "")
    )

    problems = 0
    for root in roots:
        try:
            health = inspect_index(root, index_dir=index_dir)
        except (OSError, sqlite3.Error) as exc:
            # Report and keep going: one unreadable root must not hide the state
            # of the others, which is what an uncaught error here would do.
            print(f"  {root.label}: PROBLEM — cannot read its index ({exc})")
            problems += 1
            continue

        if health is None:
            # Cold start, not a fault. Name where it *will* go, so a wrong
            # --index-dir shows up here rather than as a confusing empty search.
            db = index_path_for(root.label, root.path, index_dir)
            print(f"  {root.label}: no index yet — will be created at {db}")
            continue

        notes = ["stale" if health.stale else "fresh"]
        notes.append(_refresh_note(health.last_refreshed_at))
        if not health.schema_current:
            # Not a fault either: the next index/search rebuilds it. Worth
            # saying, because it explains a rebuild the operator did not ask for.
            notes.append(f"schema v{health.schema_version} — will rebuild")
        detail = ", ".join(notes)
        print(f"  {root.label}: {health.files} files indexed, {detail} ({health.path})")

    if problems:
        print(f"{problems} root(s) have an index that cannot be read", file=sys.stderr)
        return 1
    return 0


def _cmd_count(args: argparse.Namespace) -> int:
    """Complete enumeration: totals and a file roster, no excerpts."""
    roots = _resolve_roots(args)
    search = ForgeSearch(roots=roots, index_dir=_index_dir(args))
    try:
        result = search._count(args.query, project=args.project, doc_only=args.docs)
    except QueryError as exc:
        # Same rule as search: a malformed query is not an empty result, and a
        # zero count is only usable as proof of absence if the two differ.
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

    for f in result["files"]:
        print(f"{f['matches']:>6}  {f['project']}/{f['path']}")
    print(
        f"{result['total_chunks']} matches in {result['total_files']} files",
        file=sys.stderr,
    )
    if result["truncated"]:
        print("(file roster truncated)", file=sys.stderr)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    roots = _resolve_roots(args)
    search = ForgeSearch(
        roots=roots, embedder=_embedder(args), index_dir=_index_dir(args)
    )
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
        "--sub-projects",
        metavar="DIR[,DIR...]",
        help=(
            "Directories under a root, resolved relative to it, whose "
            "checkouts (anything containing .git) become additional "
            "labelled roots. Comma-separated for more than one."
        ),
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
        "--quiet",
        action="store_true",
        help=(
            "Do not print the resolved roots to stderr at startup. Clients "
            "label all stderr as a warning; this silences it at the cost of "
            "not being able to see which tree is indexed."
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

    # `reindex` and `index` do the same work. `index` is the plain verb for
    # building one; `reindex` adds --if-stale so a scheduler or session hook can
    # call it on every run without paying for it. Neither is deprecated.
    reindex = sub.add_parser(
        "reindex",
        help="Refresh indexes, optionally only when the last refresh is old",
    )
    _add_root_args(reindex)
    reindex.add_argument("--force", action="store_true", help="Rebuild from scratch")
    reindex.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "Accepted for explicitness; refreshing is already incremental, so "
            "this changes nothing."
        ),
    )
    reindex.add_argument(
        "--if-stale",
        type=float,
        metavar="SECONDS",
        help=(
            "Skip entirely when the last refresh was under SECONDS ago. Reads "
            "one metadata row rather than walking the tree, which is what makes "
            "an unconditional call cheap. 0 means always refresh. --force "
            "overrides this."
        ),
    )
    reindex.set_defaults(func=_cmd_reindex)

    doctor = sub.add_parser(
        "doctor",
        help="Report index health. Exits 0 when the install can serve queries.",
    )
    _add_root_args(doctor)
    doctor.set_defaults(func=_cmd_doctor)

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

    count = sub.add_parser(
        "count",
        help="Count every match; print per-file totals instead of excerpts",
    )
    _add_root_args(count)
    count.add_argument("query")
    count.add_argument("--project", help="Restrict to one root label")
    count.add_argument("--docs", action="store_true", help="Prose files only")
    _index_dir_flag(count)
    count.set_defaults(func=_cmd_count)

    # doctor included deliberately: without it, doctor resolves the default
    # index location and reports a correctly-indexed root as unindexed.
    for _p in (serve, index, reindex, doctor, search):
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
