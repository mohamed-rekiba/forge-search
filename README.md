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

Building it turned up five more of the same shape, every one self-inflicted and
every one found by running against real content rather than by the test suite:

| Found | What happened |
|---|---|
| Hyphenated queries | `require-active-ticket` raised `no such column: active` — the full-text engine read the hyphens as syntax. The error was caught and returned as an empty list. Valid query, real matches present, zero results, no error. |
| Semantic backfill | Enabling the semantic arm on an existing index left it permanently empty: the lexical arm reported no changes, so nothing was built. Hybrid degraded to lexical, so nothing looked wrong. |
| Filename lookup | Two files were unfindable by name because neither contains its own filename — a content-only index cannot reach them. |
| Version guard | The guard added to catch the above only cleared indexes that already had a version stamp. An *unstamped* index is precisely a pre-versioning one, so it stamped the current version onto stale data and made the gap permanent. |
| Bad stamp | That buggy guard had already mis-stamped a real index, which the fixed code then correctly skipped. Only a version bump gets past a bad stamp. |

The consistency of that pattern is the reason `bench` exists, and the reason
the codebase prefers blunt mechanisms over clever ones. Three of the five were
caused by an optimisation that was right in the abstract and wrong in effect.

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
forge-search search "validate_token" --root /abs/path/to/project
forge-search search "require-active-ticket" --root api=~/code/api --top-k 5
forge-search search "authentication" --root ~/code/api --docs   # prose only

# Build indexes without serving
forge-search index --root api=~/code/api

# Run as an MCP server
forge-search serve --root api=~/code/api
```

**`--root` is required. Nothing is discovered.**

There is no walk-up, no working-directory inference, no marker sniffing, and
no fallback guess. Your editor already knows the project path and substitutes
it at launch; inferring it would solve a problem that is already solved, and
inferring it *wrongly* is silent — the wrong tree gets indexed and every
search returns plausible results from somewhere you did not ask about.

Omitting `--root` prints the three correct forms rather than picking one.

## Register with an MCP client

Add to `.mcp.json` in your project. `${workspaceFolder}` is substituted by the
editor at launch, so it is always the current project and can never go stale
the way a written-in absolute path can:

```json
{
  "mcpServers": {
    "forge-search": {
      "type": "stdio",
      "command": "forge-search",
      "args": ["serve", "--root", "${workspaceFolder}"]
    }
  }
}
```

Outside an editor, use an absolute path. Several roots are fine — repeat
`--root`, optionally labelling each:

```json
"args": ["serve",
         "--root", "api=/abs/path/api",
         "--root", "web=/abs/path/web"]
```

Tools then appear as `mcp__forge-search__search_code`, `…__search_docs`, and
`…__reindex`.

The server name is configurable (`--name`). It defaults to `forge-search` and
should stay that way unless you have a specific reason: registering under
another product's name collides with it wherever both are installed.

## Portfolio adapters

Some projects are really *several* projects — a parent repo with managed
project clones underneath it. Search finds those files either way, since the
subdirectories are traversed like any others. What an adapter adds is
**per-project labels**, which is what makes `project="name"` filtering work
and keeps result paths relative to each project rather than to the parent.

```bash
forge-search serve --root ws=${workspaceFolder} --adapter apexyard
# → apexyard expanded ws into 4
```

**Adapters are named, never detected.** An adapter that enables itself because
it spotted a marker file can fire when you did not want it and stay quiet when
you did — both invisibly. Naming it costs one flag and removes the failure
class. Any expansion is reported, because turning one root into several is
more than you literally asked for.

An adapter that finds nothing to expand keeps the root unchanged, so naming
one can never make a search cover *less* than requested.

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

Full-text search across all roots, or one named root. Searches **both file
contents and filenames** — a filename match ranks above incidental content
mentions, because someone searching `auth.py` wants that file rather than the
tests referencing it. This matters more than it sounds: a file frequently does
not contain its own name, so a content-only index cannot find it at all.

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

## Is the semantic arm worth it?

`bench` answers that with a number rather than an opinion, and is written so
the unflattering answer is as easy to reach as the flattering one:

```bash
forge-search bench --semantic \
  --query "validate_token::src/auth.py" \
  --query "how sessions expire::docs/sessions.md"
```

```text
mode         recall      chars    p50 ms    p95 ms
lexical      25.0%        717       1.4       7.5
semantic     87.5%      2,651       4.8       6.9
hybrid       87.5%      2,774       6.0       8.0

Verdict:
  Hybrid improves answer-recall by 62.5% over lexical alone. The semantic
  arm earns its cost here.
```

Each query names the file that *should* be found. That is not optional:
token counts are trivially improved by returning less, so a mode that halves
its output by dropping the answer has not won. Every comparison is at **equal
answer-recall**.

**It has been run on a real corpus.** The semantic arm finds **87.5%** where
lexical alone finds 25% — but it took two bug fixes to get there, and the first
run reported 0%. See [BENCHMARK.md](BENCHMARK.md) for the full progression, two
well-supported priors that lost to local measurement, and the limits of an
8-query fixture.

**Cross-encoder reranking** is implemented (`--rerank`, after
`forge-search warm --reranker`) and **off by default because it lost** — the
largest reported single-stage gain in the literature was neutral on both arms
here and cost hybrid 7.7 points at 10× the latency. Two priors before it lost
the same way. Try it on your corpus; the flag is there precisely because these
results are corpus-specific.

`verdict()` will say *"pure overhead — remove the arm"* and *"hybrid is WORSE
than lexical"* as readily as the opposite. If it says the semantic arm earns
nothing on your corpus, set `ArmWeights(semantic=0.0)` — no release needed —
and if that holds, delete the arm rather than carrying it.

## Indexes

One SQLite file per root, in the OS cache directory
(`~/Library/Caches/forge-search` on macOS, `$XDG_CACHE_HOME/forge-search` on
Linux). Never inside the searched tree — an index there would get committed,
and would be indexed by the next run.

Override with `FORGE_SEARCH_CACHE_DIR`. Safe to delete at any time; the next
search rebuilds.

Indexes carry a schema version. When it changes, the index is discarded and
rebuilt rather than migrated in place — a targeted migration that misses one
table leaves the index looking healthy while silently missing a capability,
which is a failure this project has already shipped twice. A rebuild costs
seconds; a silently incomplete index costs correctness indefinitely.

## Known limitation

The lexical index is token-based. A long unbroken run of characters — minified
JavaScript, a base64 blob — is a single token, so searching for a substring
inside it finds nothing. This is correct behaviour for the workload it targets
(identifiers, filenames, short phrases) and is pinned by a test so it stays
deliberate. Substring search over such content would need a trigram index.

## Architecture

```text
core/       generic. No knowledge of any integration.
  roots      explicit roots + the RootProvider seam; nothing discovered
  traversal  the four defects, fixed as defaults
  cache      per-root index paths, no absolute path ever persisted
  index      FTS5 content + filename matching, incremental, schema-versioned
  query      query escaping; errors kept distinct from empty results
  embedding  the Embedder protocol; hashing (no download) and ONNX
  semantic   chunking + sqlite-vec, importable only when installed
  fusion     RRF; degrades to exactly the lexical order when an arm is empty
  hybrid     composes both arms so neither imports the other
  rerank     optional cross-encoder final stage; off by default (it lost)
  bench      the measurement that judges every optional stage
  model      explicit model fetch; separate so embedding never imports network
  server     the MCP tool surface
adapters/   integration-specific root expanders, named with --adapter, never detected
```

Integrations supply roots through `RootProvider` and nothing else. The seam is
narrow on purpose: anything wider lets integration concepts into the core, at
which point "works standalone" is a claim in a README rather than a property of
the code.

## Development

```bash
uv sync
uv run pytest                    # 185 tests, lexical-only
uv run --extra semantic pytest   # same suite with the semantic arm
```

## Licence

MIT
