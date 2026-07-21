# Troubleshooting

Every entry here is a real failure someone hit, including several found while
building this. They share a shape: **the tool kept working and gave a
plausible-looking answer**, which is exactly why they need writing down.

## Searches return nothing, or far too little

**Check whether the semantic arm is actually on.** This is the most common
cause by a wide margin. Without it, conceptual queries return literally zero:

```text
"how do I know if a merge is allowed"   lexical only  →  no results
                                        with semantic →  3 hits
```

Three things must all be true. Verify each rather than assuming:

```bash
python -c "import onnxruntime, sqlite_vec"   # 1. the extra is installed
forge-search warm                             # 2. the model is present
# 3. --semantic is in your .mcp.json args
```

If you installed with `--with semantic` instead of `'forge-search[semantic]'`,
you have an unrelated PyPI package named `semantic` and no ONNX runtime. The
server says so on stderr and continues lexical-only.

## `${workspaceFolder}` appears literally in an error

```text
--root contains an unexpanded variable: ${workspaceFolder}
```

`${workspaceFolder}` is a **VS Code `launch.json` variable**, and MCP clients
do not agree on it:

| Client | Behaviour |
|---|---|
| Claude Code (`.mcp.json`) | Not supported — expands environment variables only (`${VAR}`, `${VAR:-default}`) |
| VS Code (`.vscode/mcp.json`) | Documented as supported, but [several](https://github.com/microsoft/vscode/issues/251263) [open](https://github.com/microsoft/vscode/issues/245905) [bugs](https://github.com/microsoft/vscode/issues/297794) report it arriving as a literal string |

forge-search accepts it as a fallback resolving to the working directory,
which for a stdio MCP server is the project root in practice — though Claude
Code's documentation explicitly tells servers not to depend on cwd. **Use an
absolute path.** Any *other* unexpanded variable is a hard error, not a second
fallback.

## The wrong tree is being searched

Results look plausible but come from somewhere you did not ask about. Check
the resolved root — it is reported at startup:

```text
forge-search: roots: apexyard=/Users/you/code/apexyard  adapter: apexyard
```

If that path is wrong and you used `${workspaceFolder}`, the working directory
was not what you assumed. This has a real precedent: `uv run --directory`
changed the cwd during testing and quietly indexed a different repository —
the startup line is what caught it.

An absolute `--root` cannot fail this way.

## `[warning] [server stderr] …` in the MCP log

**This is not a warning.** VS Code pipes every stderr line from an MCP server
at a hardcoded `LogLevel.Warning`, with no content parsing, because [stderr
handling is unspecified by MCP](https://github.com/modelcontextprotocol/specification/issues/177).
A server writing anything at all to stderr looks like it is warning you.

The startup roots line is the only thing forge-search writes there. `--quiet`
suppresses it, at the cost of not being able to see which tree is indexed.

## I cannot see which root is being searched

The active configuration is reported on **two** channels, because clients
differ in which they surface:

1. **stderr, at startup** — always, unless `--quiet`. Rendered as `[warning]`.
2. **`notifications/message` at level `info`** — on the first tool call.
   Rendered as a plain `[info]` line.

If the notification never appears, check in this order:

| Check | Why |
|---|---|
| Has a tool call happened? | There is no session to send on until a request arrives, so it cannot fire at startup. Run one search. |
| Is the output channel's level at least Info? | VS Code forwards the channel's level to the server; at `Warning` the server correctly suppresses `info`. The gear icon in the Output pane changes it. |
| Does the server advertise `logging`? | It does — but a server that does not is entitled to have its log notifications dropped, which is what a spec-compliant client does. |

## Results are stale after editing

They should not be — every search stats each indexed file and reindexes on
mismatch. If they genuinely are:

```bash
forge-search index --root /abs/path --force
```

Then say so in an issue, because automatic freshness failing is a bug, not a
usage question.

## Results got worse after switching models

They should not have — the index records a model + chunking fingerprint and
discards itself when either changes. If you are on a build predating that
guard, delete the index and let it rebuild:

```bash
rm -rf /abs/path/.forge_search
```

Reusing vectors across a model change computes cosine distance between two
unrelated embedding spaces. It returns confident nonsense with no error, and
`dim` cannot detect it — `all-MiniLM` and `multi-qa-MiniLM` are both
384-dimensional.

## A substring inside a long token is not found

Expected. The lexical index is token-based, so a minified JS bundle or a
base64 blob is a single token and substring search inside it finds nothing.
Correct for the workload (identifiers, filenames, short phrases), pinned by a
test so it stays deliberate. Substring search there would need a trigram
index.

## The server will not start

```text
no --root given. forge-search does not guess which project you mean.
```

Working as designed — nothing is discovered. Pass an absolute path.

For anything else, the traceback goes to stderr, which your client shows as
`[warning] [server stderr]`. A startup crash exits non-zero rather than
hanging.

## Reporting something not listed here

The useful details:

```bash
forge-search --version 2>/dev/null || pip show forge-search | head -2
forge-search search "<your query>" --root /abs/path --top-k 5   # exact output
python -c "import onnxruntime, sqlite_vec; print('semantic ok')"
```

Plus whether the result was **wrong** or **empty** — they have different
causes, and this project treats a silent empty result as the more serious of
the two.

---

Next: [decisions.md](decisions.md) · [architecture.md](architecture.md)
