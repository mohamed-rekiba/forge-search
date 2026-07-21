# Design decisions

Why the code is shaped the way it is — including three decisions that were
reversed, and three well-supported priors that lost to local measurement.

## Why this exists

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

Each is fixed as a **default**, not an option — because every one was already
fixable by passing the right flag, and nobody did. Correctness that depends on
remembering a flag is not correctness.

Building it produced more of the same shape, every one found by running
against real content rather than by the test suite:

| Found | What happened |
|---|---|
| Hyphenated queries | `require-active-ticket` raised `no such column: active`; the error was caught and returned as an empty list |
| Semantic backfill | Enabling the arm on an existing index left it permanently empty — the lexical arm reported no changes, so nothing was built |
| Filename lookup | Two files were unfindable by name because neither contains its own filename |
| Version guard | The guard added to catch the above only cleared indexes that already had a version stamp — an *unstamped* index is precisely a pre-versioning one |
| Model fingerprint | Switching models silently reused the old model's vectors; recall was understated by 11.5 points and nothing indicated a problem |
| Rerank truncation | Fusion truncated to `limit` before reranking, so recall could not move by construction while full latency was paid |

That consistency is why `bench` exists, why the codebase prefers blunt
mechanisms over clever ones, and why several guards clear **unconditionally**
rather than trying to be precise.

## Nothing is discovered

Roots are named explicitly. Adapters are named explicitly. There is no
walk-up, no cwd inference, no marker sniffing, no fallback guess.

**Reversed once.** The original justification was "your editor substitutes the
project path at launch, so inferring it solves a solved problem." Half of that
was false: `${workspaceFolder}` is a VS Code `launch.json` variable that
Claude Code does not expand and VS Code has open bugs about. It reached a real
client as a literal string.

The conclusion survives on the other half. A wrong *guess* is silent — the
wrong tree gets indexed and every search returns plausible results from
somewhere else. A wrong *absolute path* fails at startup with a message. The
honest cost, which the first version waved away: an absolute path makes a
shared `.mcp.json` per-machine.

Detection has the same flaw as guessing. An adapter that enables itself
because it spotted a marker file can fire when unwanted and stay silent when
wanted, both invisibly. Naming it costs one flag and removes the class.

## Indexes live in the tree

`<root>/.forge_search/` by default.

**Reversed.** The original argument for a shared OS cache directory was sound:
an in-tree index gets committed, and is itself searchable so the tool indexes
its own output. Both are real failures, not preferences.

What outweighed them: a shared cache keys each index by a hash of the root
path. Delete, move, or rename a project and its index is orphaned forever,
with nothing in the filename to say which project it belonged to. Nothing
collects them and nothing announces the leak — ~600 MB had accumulated on the
development machine before anyone looked.

Both objections are answered rather than accepted:

- **Committed by accident** — the directory ships a `.gitignore` containing
  `*`, so it ignores itself and its contents. Nothing to remember.
- **Indexed by itself** — `.forge_search` is in `DEFAULT_EXCLUDES`.

An absolute `--index-dir` opts back into a shared directory, which is still
right for trees you cannot write to.

## Freshness is checked, not delegated

Every search stats every indexed file. The alternative — reminding a human or
an agent to reindex after edits — is a convention, and conventions nobody
follows produced every defect above. A stale index has the same shape as all
of them: plausible results, no error.

## Failures are loud

A query that cannot be parsed **raises**. It does not return an empty list.

"No results" and "your query was invalid" must never look the same, because
the caller's next action differs completely: one means "search elsewhere", the
other means "fix your query". The hyphenated-query bug conflated them and cost
a real debugging session.

The same reasoning drives `degraded` in the tool response: a root that could
not be read is **reported**, not dropped, so a partial answer never looks
complete.

## Blunt guards over precise ones

Both staleness guards discard everything and rebuild rather than migrating in
place.

A targeted migration that misses one table leaves the index looking healthy
while silently missing a capability — a failure this project has shipped
twice. A rebuild costs seconds; a silently incomplete index costs correctness
indefinitely.

Both clear **unconditionally** on mismatch, including when nothing is stamped
at all. An unstamped index predates the guard and is exactly the case that
needs rebuilding. An earlier version skipped the clear when nothing was
stored, stamped the current version onto stale data, and made the gap
permanent.

## Priors that lost to measurement

All three are implemented, tested, and **off by default**, with tests pinning
the default so they cannot drift back on the strength of the citation alone.

### Retrieval-tuned model vs symmetric model

`sentence-transformers`' own docs distinguish symmetric similarity
(`all-MiniLM`'s training regime) from asymmetric retrieval — short query
against long passage — and recommend the multi-QA family for the latter. That
is the model author's stated scope, not folklore.

```text
all-MiniLM-L6-v2        (symmetric, "wrong regime")   87.5%
multi-qa-MiniLM-L6-cos  (asymmetric, "right regime")  62.5%
```

### Structure-aware chunking vs line windows

cAST (ACL Findings EMNLP 2025, peer-reviewed) reports ~4.3 points of Recall@5
*gained* from splitting on AST boundaries rather than line counts.

```text
breadcrumbs + line windows        87.5%
breadcrumbs + structural chunks   75.0%
```

### Cross-encoder reranking

The largest single-stage gain in the retrieval literature — commonly 30–50%
of the failures that survive hybrid search. Here it was neutral on both single
arms and cost hybrid 7.7 points at 10× the latency.

**None of this means the priors are wrong in general.** One corpus of prose
and shell is exactly where you should expect code-retrieval results to travel
badly. It means they did not reproduce here, which is enough not to override a
local measurement — and each remains one flag away.

Full numbers, method, and limits: [BENCHMARK.md](../BENCHMARK.md).

## Two channels for the startup summary

The active configuration is reported on stderr **and** as an MCP
`notifications/message`.

**Reversed twice.** It started on stderr; MCP clients render every stderr line
as `[warning]`, so a healthy launch always looked like a problem. Moving it to
the `instructions` field fixed the label and made it invisible — `instructions`
goes to the model, not to any log. Moving it to `notifications/message` was
correct but reached nobody, because the server did not advertise the `logging`
capability and a spec-compliant client is entitled to drop such notifications.

Both channels now carry it. stderr is guaranteed; the notification is correct
and renders properly where supported. VS Code's MCP logging support is
implemented but undocumented, which is reason enough not to depend on it
alone.

---

Next: [architecture.md](architecture.md) · [BENCHMARK.md](../BENCHMARK.md)
