# Configuration

Every knob, what it defaults to, and when to reach for it.

## Commands

| Command | Purpose |
|---|---|
| `forge-search serve` | Run the MCP server |
| `forge-search search <query>` | One-shot search — the fastest way to check behaviour |
| `forge-search index` | Build or refresh indexes without serving |
| `forge-search reindex` | Refresh, with an optional "skip if recently done" gate — for hooks and schedulers |
| `forge-search doctor` | Report index health and refresh age per root. Exits non-zero only for an index that exists but cannot be read |
| `forge-search warm` | Fetch the embedding model (or `--reranker`) |
| `forge-search bench` | Measure whether the semantic arm earns its cost |

`index` and `reindex` overlap on purpose. `index` is the plain verb for building
one; `reindex` adds the staleness gate, so an unattended caller can run it on
every session start without paying for a tree walk each time.

`doctor`'s exit code is meant as a liveness probe, which is why a missing or
stale index counts as healthy — search builds and refreshes on the read path, so
both fix themselves. Only an index that exists and cannot be read fails, because
that is the one state that does not self-heal.

Its output also reports each index's refresh age, which is what `--if-stale`
reads:

```
  forge-search: 2589 files indexed, fresh, refreshed 4m ago (…/forge-search.db)
```

`never refreshed` is a separate state from a long-ago refresh, not a very old
one: an index from before the stamp existed has none, and so does one a schema
bump emptied. `--if-stale` treats both as due.

## Roots — always explicit

`--root` is required. Nothing is discovered: no walk-up, no working-directory
inference, no marker sniffing, no fallback guess.

```bash
--root /absolute/path/to/project              # label taken from the directory name
--root api=/absolute/path/to/api              # explicit label
--root api=/abs/api --root web=/abs/web       # several roots
```

Labels appear in results as `project` and are used for filtering. They must
stay relative — never an absolute path — because they end up in logs and
caches and would leak machine layout.

`${workspaceFolder}` is accepted as a fallback that resolves to the working
directory, because several MCP clients pass it through unexpanded. It is a
compatibility shim, not a recommendation — see
[troubleshooting.md](troubleshooting.md#workspacefolder-appears-literally-in-an-error).

Relative paths resolve against the working directory in the ordinary way, hidden
directories (`.smoke/corpus`) and `..` included. An absolute path is still the
right answer for anything you commit to a config file.

## Flags

| Flag | Default | Applies to | What it does |
|---|---|---|---|
| `--root <path>` | *required* | all except warm | A tree to index. Repeatable. |
| `--sub-projects <dir>[,<dir>...]` | none | all except warm | Directories under a root (resolved relative to it) whose checkouts become additional labelled roots |
| `--index-dir <path>` | `.forge_search` | all except warm | Where indexes live. Relative = per root; absolute = shared |
| `--semantic` | off | serve, search, bench | Enable the semantic arm (needs the extra + `warm`) |
| `--model <name>` | `all-minilm` | serve, search, bench, warm | Embedding model short name |
| `--rerank` | off | serve, search, bench | Cross-encoder final pass (measured worse here) |
| `--transport <t>` | `stdio` | serve | `stdio` or `streamable-http` |
| `--name <name>` | `forge-search` | serve | MCP server name |
| `--quiet` | off | serve | Suppress the startup roots line on stderr |
| `--top-k <n>` | 10 | search, bench | Maximum results |
| `--docs` | off | search | Prose files only |
| `--force` | off | index, reindex, warm | Rebuild / re-download from scratch. On `reindex`, also ignores `--if-stale` |
| `--if-stale <secs>` | off | reindex | Skip when the last refresh was under this long ago. Costs one metadata read, not a tree walk. `0` always refreshes; so does an index with no recorded refresh |
| `--incremental` | off | reindex | Accepted for explicitness. Refreshing is already incremental, so it changes nothing |
| `--fixture <file>` | none | bench | File of `query::expected/path` lines |

## Registering with an MCP client

Add to `.mcp.json` in your project, with an **absolute path**:

```json
{
  "mcpServers": {
    "forge-search": {
      "type": "stdio",
      "command": "forge-search",
      "args": ["serve", "--root", "/absolute/path/to/project", "--semantic"]
    }
  }
}
```

Tools then appear as `mcp__forge-search__search_code`, `…__search_docs`,
`…__count_matches`, and `…__reindex`.

To serve a whole portfolio of clones from one server, add `"--sub-projects",
"workspace"` (or whatever directory holds them) and a `"--name"` of your
choosing to those args, with the root pointing at the parent tree — see the
[README](../README.md#register-with-an-mcp-client) for the full block.

The server name is configurable but should stay `forge-search` unless you have
a specific reason: registering under another product's name collides with it
wherever both are installed.

## Enabling the semantic arm

Three steps, and skipping any one leaves you on lexical-only search — which
works, silently, and finds far less on conceptual queries.

```bash
uv tool install --force 'forge-search[semantic]'   # 1. the extra (note the brackets)
forge-search warm                                   # 2. fetch the model (~90 MB, once)
forge-search serve --root /abs/path --semantic      # 3. the flag
```

> **`--with semantic` is not the same as `[semantic]`.** `--with` installs an
> unrelated PyPI package that happens to be named `semantic`, leaving the arm
> dead with `No module named 'onnxruntime'`. Extras go in brackets.

If the model is missing, the server reports it on stderr and continues
lexical-only rather than refusing to start — absence is a supported
configuration, not a failure.

## Index location

| Form | Result | Use when |
|---|---|---|
| *(default)* | `<root>/.forge_search/<label>.db` | Almost always. Dies with the project. |
| `--index-dir .cache` | `<root>/.cache/<label>.db` | You want a different in-tree name |
| `--index-dir ~/.cache/fs` | `~/.cache/fs/<label>-<hash>.db` | The tree is read-only or shared |
| `FORGE_SEARCH_CACHE_DIR` | Redirects the shared location | Tests, sandboxes, unwritable `$HOME` |

The in-tree directory ships a `.gitignore` containing `*`, so it ignores
itself and its contents with no action from you.

An explicit `--index-dir` always wins. Precedence: `--index-dir` >
`.forge_search`.

## Transports

**`stdio`** (default) — the client spawns the server per session, owns its
lifecycle, and there is no port. A crash is a non-zero exit rather than a hung
socket.

**`streamable-http`** — one long-lived server for many sessions. Worth it once
the semantic arm is enabled, since the embedding model then loads once instead
of per session.

```bash
forge-search serve --transport streamable-http --root /abs/path --semantic
```

Both come from the MCP SDK directly; no web framework is involved. **Neither
needs an extra** — `mcp` requires starlette and uvicorn itself, so a plain
`uv tool install forge-search` can already serve HTTP. `forge-search[http]`
still resolves, to nothing, so an existing install line keeps working.

## Sub-projects

Some trees are really several projects — a parent repo with project clones
underneath it. Search finds those files either way, since subdirectories are
traversed like any others. What `--sub-projects` adds is **per-project
labels**, which is what makes `project="name"` filtering work and keeps
result paths relative to each project rather than to the parent.

```bash
forge-search serve --root ws=/abs/portfolio --sub-projects workspace
# → roots: ws=/abs/portfolio, alpha=…, beta=…, gamma=…, delta=…   ws expanded into 4 sub-project(s)
```

Each name in `--sub-projects` is a directory, resolved **relative to the
root** it follows — so `--sub-projects ../elsewhere/workspace` can reach
outside the root entirely, which is how a split-portfolio layout (registry
and clones in a sibling repository) is expressed without any config file.
Comma-separate more than one: `--sub-projects packages,apps`.

An entry becomes a root when it contains `.git` — checked for **existence**,
not for being a directory, because a `git worktree` checkout's `.git` is a
file (a `gitdir:` pointer), not a directory, and a directory-only check would
silently skip every one. Hidden entries and plain files are skipped. A named
directory that does not exist is not an error — the root is still searched,
just without that expansion, and any real expansion is reported because
turning one root into several is more than you literally asked for.

## Environment variables

| Variable | Effect |
|---|---|
| `FORGE_SEARCH_CACHE_DIR` | Overrides the shared cache directory and the model download location |
| `FORGE_SEARCH_LOG_LEVEL` | Raises MCP SDK log verbosity (default suppresses per-request INFO chatter) |

---

Next: [troubleshooting.md](troubleshooting.md) · [decisions.md](decisions.md)
