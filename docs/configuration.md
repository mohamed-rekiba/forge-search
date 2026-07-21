# Configuration

Every knob, what it defaults to, and when to reach for it.

## Commands

| Command | Purpose |
|---|---|
| `forge-search serve` | Run the MCP server |
| `forge-search search <query>` | One-shot search — the fastest way to check behaviour |
| `forge-search index` | Build or refresh indexes without serving |
| `forge-search warm` | Fetch the embedding model (or `--reranker`) |
| `forge-search bench` | Measure whether the semantic arm earns its cost |

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

## Flags

| Flag | Default | Applies to | What it does |
|---|---|---|---|
| `--root <path>` | *required* | all | A tree to index. Repeatable. |
| `--adapter <name>` | none | serve, index, search, bench | Expand one root into the projects it contains |
| `--index-dir <path>` | `.forge_search` | serve, index, search, bench | Where indexes live. Relative = per root; absolute = shared |
| `--semantic` | off | serve, search, bench | Enable the semantic arm (needs the extra + `warm`) |
| `--model <name>` | `all-minilm` | serve, search, bench, warm | Embedding model short name |
| `--rerank` | off | serve, search, bench | Cross-encoder final pass (measured worse here) |
| `--transport <t>` | `stdio` | serve | `stdio` or `streamable-http` |
| `--name <name>` | `forge-search` | serve | MCP server name |
| `--quiet` | off | serve | Suppress the startup roots line on stderr |
| `--top-k <n>` | 10 | search, bench | Maximum results |
| `--docs` | off | search | Prose files only |
| `--force` | off | index, warm | Rebuild / re-download from scratch |
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

Tools then appear as `mcp__forge-search__search_code`, `…__search_docs`, and
`…__reindex`.

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

An adapter can move the default; an explicit `--index-dir` always wins.
Precedence: `--index-dir` > adapter > `.forge_search`.

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

Both come from the MCP SDK directly; no web framework is involved.

## Adapters

Some trees are really several projects — a parent repo with project clones
underneath it. Search finds those files either way, since subdirectories are
traversed like any others. What an adapter adds is **per-project labels**,
which is what makes `project="name"` filtering work and keeps result paths
relative to each project rather than to the parent.

```bash
forge-search serve --root ws=/abs/portfolio --adapter apexyard
# → adapter: apexyard   apexyard expanded ws into 4
```

An adapter that finds nothing to expand leaves the root unchanged, so naming
one can never make a search cover *less* than you asked for. Any expansion is
reported, because turning one root into several is more than you literally
requested.

## Environment variables

| Variable | Effect |
|---|---|
| `FORGE_SEARCH_CACHE_DIR` | Overrides the shared cache directory and the model download location |
| `FORGE_SEARCH_LOG_LEVEL` | Raises MCP SDK log verbosity (default suppresses per-request INFO chatter) |

---

Next: [troubleshooting.md](troubleshooting.md) · [decisions.md](decisions.md)
