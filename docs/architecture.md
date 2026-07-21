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
        server["MCP server<br/>search_code · search_docs · reindex"]
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
        roots["roots<br/>explicit roots + the RootProvider seam"]
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

    adapters["adapters/<br/><i>integration-specific root expanders</i>"]

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
    adapters -.->|"implements RootProvider"| roots
    cli --> roots

    style adapters stroke-dasharray: 4 4
```

Two rules hold this shape:

**`core/` never imports `adapters/`.** The dependency points one way. A test
asserts it, by checking imports rather than word occurrences, because the
first version of that test passed while a `core/` docstring cheerfully
mentioned `--adapter apexyard`.

**`hybrid` composes the arms so neither imports the other.** `index` knows
nothing about embeddings; `semantic` knows nothing about FTS5. Either can be
absent — a lexical-only install never imports `semantic` at all — and the
composition layer is the only place that has to care.

## The adapter seam

An adapter is a root **expander**, not a root discoverer. It receives a root
the caller already named, decides whether it understands that shape of tree,
and if so expands it into the several labelled roots it really contains.

```python
class RootProvider:
    name: str = ""                      # what `--adapter <name>` selects
    index_dir: str | None = None        # optionally moves the default index location

    def expand(self, root: Root) -> Sequence[Root]:
        return [root]
```

That is the entire interface. It is narrow on purpose: anything wider lets
integration concepts into the core, at which point "works standalone" becomes
a claim in a README rather than a property of the code.

**Adapters are named, never detected.** An adapter that enables itself because
it spotted a marker file can fire when you did not want it and stay silent
when you did — both invisibly. Naming it costs one flag and removes the
failure class entirely. See [decisions.md](decisions.md#nothing-is-discovered).

## Startup

```mermaid
sequenceDiagram
    participant C as MCP client
    participant S as forge-search
    participant D as Disk

    C->>S: spawn — forge-search serve --root PATH [--adapter NAME]
    S->>S: resolve roots — explicit, adapter may expand
    S-->>C: stderr — roots: LABEL=PATH  adapter: NAME
    C->>S: initialize
    S-->>C: capabilities (tools, logging)
    C->>S: notifications/initialized
    C->>S: logging/setLevel
    C->>S: tools/list
    S-->>C: search_code · search_docs · reindex

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
