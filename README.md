# forge-search

Local code and docs search over one or many repositories, served as an MCP tool.

Point it at a directory and it works. Point it at several and it searches them
together, returning ranked excerpts with the project, relative path, and line
number.

## Why it exists

It was built after auditing why repository search kept quietly returning
nothing. Four defects turned up, and all four were **silent** — zero results
rather than an error, which is indistinguishable from "the content does not
exist". A caller acts on the false negative with full confidence.

| Defect | Measured effect |
|---|---|
| Hidden directories skipped by default | **20 of 44 files invisible** — over half a real repository |
| Ignore rules applied to explicitly-named paths | A directory passed by name returned **zero hits** |
| Line-length limits replacing the match with a placeholder | The matched text **destroyed**, not shortened |
| Absolute index roots going stale on move or re-clone | Server fails to launch; **every search silently returns nothing** |

Each is fixed here as a **default**, not an option — because every one of them
was already fixable by passing the right flag, and nobody did. Correctness that
depends on remembering a flag is not correctness.

A fifth turned up while building this one: searching `require-active-ticket`
raised `no such column: active`, because SQLite's full-text engine read the
hyphens as syntax. The error was caught and returned as an empty list. Valid
query, real matches present, zero results, no error — the same defect, in the
tool built to fix it. See `core/query.py`.

## Install

```bash
uv tool install forge-search              # lexical search
uv tool install 'forge-search[semantic]'  # + semantic (adds an embedding model)
```

Requires Python 3.14+. No compiler: both native dependencies ship prebuilt
wheels for cp314.

## Use

```bash
# One-shot search — the fastest way to check it behaves
forge-search search "validate_token"
forge-search search "require-active-ticket" --root api=~/code/api --top-k 5
forge-search search "authentication" --docs        # prose only

# Build indexes without serving
forge-search index --root api=~/code/api

# Run as an MCP server
forge-search serve --root api=~/code/api
```

With no `--root`, roots are discovered by walking up from the current
directory. How they were chosen is always printed to stderr — a wrong guess
that stays silent is the failure mode.

## Register with an MCP client

Add to `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "forge-search": {
      "type": "stdio",
      "command": "forge-search",
      "args": ["serve", "--root", "myproject=."]
    }
  }
}
```

Tools then appear as `mcp__forge-search__search_code`, `…__search_docs`, and
`…__reindex`.

The server name is configurable (`--name`). It defaults to `forge-search` and
should stay that way unless you have a specific reason: registering under
another product's name collides with it wherever both are installed.

## Transport

`stdio` (default) spawns the server per session — the client owns its
lifecycle, there is no port, and a crash is a non-zero exit rather than a hung
socket.

`--transport streamable-http` runs one long-lived server for many sessions.
Worth it once the semantic arm is enabled, since the embedding model then loads
once instead of per session.

```bash
forge-search serve --transport streamable-http --root api=~/code/api
```

Both are provided by the MCP SDK directly; no web framework is involved.

## Tools

### `search_code(query, project=None, top_k=10)`

Full-text search across all roots, or one named root.

Queries are matched **literally** — hyphens, dots, slashes, and parentheses in
identifiers and paths are searched as written, not parsed as operators. Prefix
with `fts5:` for full-text expression syntax (`NEAR`, `OR`, prefix matching).

Returns:

```json
{
  "results": [
    {"project": "api", "path": "src/auth.py", "line": 42,
     "excerpt": "def validate_token(token):", "score": 9.5}
  ],
  "truncated": false,
  "degraded": null,
  "reindexed": null
}
```

`degraded` lists roots that could not be read — reported rather than dropped,
so a partial answer never looks complete. `reindexed` names roots refreshed
during the call.

### `search_docs(query, project=None, top_k=10)`

The same, restricted to prose (`.md`, `.rst`, `.txt`).

### `reindex(project=None, force=False)`

Rarely needed: searches detect staleness and reindex automatically. Use
`force` after changing traversal settings.

## Freshness

Every search compares the index against the filesystem — file sizes and mtimes
— and reindexes if they disagree. One `stat` per indexed file, cheap enough to
run on every query.

This is deliberate. Delegating it, by reminding a human or an agent to reindex
after edits, is a convention — and conventions nobody follows are what produced
every defect above. A stale index has the same shape: plausible results, no
error, nothing that announces the problem.

## Indexes

One SQLite file per root, in the OS cache directory
(`~/Library/Caches/forge-search` on macOS, `$XDG_CACHE_HOME/forge-search` on
Linux). Never inside the searched tree — an index there would get committed,
and would be indexed by the next run.

Override with `FORGE_SEARCH_CACHE_DIR`. Safe to delete at any time; the next
search rebuilds.

## Known limitation

The lexical index is token-based. A long unbroken run of characters — minified
JavaScript, a base64 blob — is a single token, so searching for a substring
inside it finds nothing. This is correct behaviour for the workload it targets
(identifiers, filenames, short phrases) and is pinned by a test so it stays
deliberate. Substring search over such content would need a trigram index.

## Architecture

```
core/       generic. No knowledge of any integration.
  roots      layered discovery + the RootProvider seam
  traversal  the four defects, fixed as defaults
  cache      per-root index paths, no absolute path ever persisted
  index      FTS5, incremental reindex, staleness detection
  query      query escaping; errors kept distinct from empty results
  server     the MCP tool surface
adapters/   integration-specific root providers, loaded only when detected
```

Integrations supply roots through `RootProvider` and nothing else. The seam is
narrow on purpose: anything wider lets integration concepts into the core, at
which point "works standalone" is a claim in a README rather than a property of
the code.

## Development

```bash
uv sync
uv run pytest        # 62 tests
```

## Licence

MIT
