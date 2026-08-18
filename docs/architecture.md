# Architecture

forge-search indexes one or more directory trees into SQLite and serves search
over them as MCP tools. There is no server process to manage, no daemon, no
network service, and no external index — a stdio server is a subprocess your
editor starts and stops, and the index is a file.

## Where it sits

```mermaid
flowchart LR
    subgraph client["MCP client"]
        agent["Agent / editor<br/>Claude Code · VS Code · any MCP host"]
    end

    subgraph proc["forge-search (subprocess)"]
        server["MCP server<br/>search_code · search_docs · count_matches · reindex"]
        lex["Lexical arm<br/>SQLite FTS5"]
        sem["Semantic arm<br/>sqlite-vec + ONNX<br/><i>optional</i>"]
        server --> lex
        server --> sem
    end

    subgraph disk["Local disk"]
        repos[("Your repositories<br/>read-only")]
        idx[("&lt;root&gt;/.forge_search/*.db")]
    end

    agent <-->|"JSON-RPC over stdio"| server
    lex --> idx
    sem --> idx
    lex -.->|"read + stat"| repos
    sem -.->|"read"| repos
```

Everything runs locally. Nothing is uploaded, and the only network access is
one explicit `forge-search warm` to fetch the embedding model — never during a
search.

## Modules

```mermaid
flowchart TD
    cli["cli<br/><i>argument surface</i>"]
    server["server<br/><i>MCP tool surface</i>"]

    subgraph core["core/ — generic, knows nothing about any integration"]
        direction TB
        roots["roots<br/>explicit roots + --sub-projects expansion"]
        traversal["traversal<br/>which files exist, and their excerpts"]
        cache["cache<br/>where an index file lives"]
        index["index<br/>FTS5 content + filename matching"]
        query["query<br/>escaping; errors kept distinct from empty"]
        embedding["embedding<br/>Embedder protocol · hashing · ONNX"]
        semantic["semantic<br/>chunking + sqlite-vec"]
        fusion["fusion<br/>reciprocal rank fusion"]
        rerank["rerank<br/>cross-encoder <i>(off by default)</i>"]
        hybrid["hybrid<br/>composes the arms"]
        model["model<br/>explicit model fetch"]
        bench["bench<br/>the measurement that judges it all"]
    end

    cli --> server
    cli --> hybrid
    server --> hybrid
    hybrid --> index
    hybrid --> semantic
    hybrid --> fusion
    hybrid --> rerank
    index --> traversal
    index --> cache
    index --> query
    semantic --> embedding
    semantic --> traversal
    cli --> roots
```

One rule holds this shape:

**`hybrid` composes the arms so neither imports the other.** `index` knows
nothing about embeddings; `semantic` knows nothing about FTS5. Either can be
absent — a lexical-only install never imports `semantic` at all — and the
composition layer is the only place that has to care.

## Sub-project expansion

`--sub-projects` turns one named root into the several roots it really
contains. A directory named by the flag is resolved relative to the root,
scanned for checkouts — anything containing `.git` — and each one becomes an
additional labelled `Root`.

```python
def resolve_roots(
    given: Iterable[Root],
    *,
    sub_projects: Sequence[str] = (),
) -> Resolution: ...
```

This used to be a `RootProvider` seam: a class the caller selected by name
(`--adapter apexyard`), which read a portfolio's own config files to infer
the same structure a flag now states directly. Removing that class is not
core learning a new trick — it is core doing what it already claimed to do
("everything explicit, nothing discovered") without the one exception that
contradicted it. See [decisions.md](decisions.md#nothing-is-discovered) and
the entry recording the seam's removal.

## Startup

```mermaid
sequenceDiagram
    participant C as MCP client
    participant S as forge-search
    participant D as Disk

    C->>S: spawn — forge-search serve --root PATH [--sub-projects DIR]
    S->>S: resolve roots — explicit, --sub-projects may expand
    S-->>C: stderr — roots: LABEL=PATH  LABEL expanded into N sub-project(s)
    C->>S: initialize
    S-->>C: capabilities (tools, logging)
    C->>S: notifications/initialized
    C->>S: logging/setLevel
    C->>S: tools/list
    S-->>C: search_code · search_docs · count_matches · reindex

    Note over C,S: first tool call
    C->>S: tools/call search_code
    S-->>C: notifications/message at level info — active configuration
    S->>D: stat every indexed file
    alt index disagrees with disk
        S->>D: reindex changed files
    end
    S->>D: query
    S-->>C: ranked hits
```

The configuration is reported twice on purpose — once on stderr, once as an
MCP log notification — because clients differ in which they surface. See
[troubleshooting.md](troubleshooting.md#i-cannot-see-which-root-is-being-searched).

## Design constraints worth knowing

**Read-only against your repositories.** The only thing written inside a
searched tree is `<root>/.forge_search/`, which ships a `.gitignore`
containing `*` so it ignores itself.

**No background work.** Nothing runs between requests. Freshness is checked
during a search, not by a watcher — see
[indexing.md](indexing.md#freshness).

**Failures are loud.** A query that cannot be parsed raises rather than
returning an empty list; a root that does not exist aborts startup with a
message naming the fix; an index built by a different model discards itself.
This is the project's whole thesis, and it is
[the reason it exists](decisions.md#why-this-exists).

---

Next: [indexing.md](indexing.md) · [searching.md](searching.md) ·
[configuration.md](configuration.md)
