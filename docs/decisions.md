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

## Dependency majors are adopted, not resolved into

A dependency whose import path this code reaches into has its major bounded.
Adopting the next one is a deliberate change with the import sites migrated.

**Found by shipping it.** `mcp` was declared `>=1.28.1` with no upper bound. 2.0
removed `mcp.server.fastmcp`, so a fresh resolve produced a package that
installed cleanly and then died at import. Nothing failed for existing users,
because `uv.lock` kept developers and CI on a working version — the breakage
reached only people installing clean, and surfaced in their client's stderr
looking like a client fault.

Two options: pin back to `<2`, or adopt 2.x. **Pinning back was rejected.** It
buys time at the cost of doing the same work later against a larger gap, and
this project has no external consumers whose upgrade cadence it needs to
respect. Adopting cost four edit sites and one genuine re-homing (below).

The bound is now expressed as a rule with a test behind it rather than as a
habit: `tests/test_dependency_bounds.py` asserts on the **declaration**, not the
installed environment, because the lockfile makes an import-based test pass
while the published metadata stays broken. It covers `optional-dependencies`
too — the first version of that test policed `mcp` alone while three
distributions in the `semantic` extra, all imported in `src/`, stayed
unbounded. Same defect, still live, one file away.

The rule now also runs the other way. The first version checked listed →
imported, so a declaration could not go stale — but nothing checked imported →
listed, and `numpy` sat outside the guard entirely: used by both the embedder
and the reranker, declared nowhere, arriving only as a transitive of
`onnxruntime` under an unbounded `numpy>=1.21.6`. A third party's requirement
was choosing the major this code ran against. It is declared in the `semantic`
extra now, where its two function-local call sites live.

## The [http] extra is a no-op alias

HTTP transport needs no extra. `mcp` requires starlette and uvicorn
unconditionally, so a base install already has what both the `streamable-http`
and `sse` transports use. Confirmed by installing nothing but the wheel into a
clean venv and starting each transport: both bind, `/mcp` answers on the first
and `/sse` on the second.

So the extra's two entries were a redundant `uvicorn` and a `fastapi` that
nothing in this project or its dependency graph ever imported — a substantial
install carried for no reason, on a project that keeps its base install small
and compiler-free on purpose.

**Kept as an empty alias rather than removed.** Deleting it would make
`forge-search[http]` stop resolving, which breaks an install line for no gain;
an empty extra resolves to nothing and documents itself. Worth revisiting at
1.0, where dropping it is a reasonable thing to do in a major.

This also corrects a reason recorded here earlier — that the two were exempt
from the bound rule because nothing imports them. True as far as it went, and
the wrong conclusion: nothing importing them was the argument for removing
them, not for leaving them declared.

## The logging capability sits on a private SDK attribute

Advertising the `logging` capability requires registering a `logging/setLevel`
handler, and the SDK offers no public seam for one. `MCPServer` takes no
`on_set_logging_level` argument, so the handler is registered through
`mcp._lowlevel_server.add_request_handler(...)` — a public method reached via a
private attribute.

Accepted knowingly. The alternative is losing the capability, which returns the
server to the state described above: notifications that are correct on the wire
and reach nobody. The exposure is pinned behaviourally rather than structurally
— `tests/test_serve_smoke.py` asserts the capability is actually advertised and
that a set level is actually honoured, over a real handshake. If a future SDK
release moves the attribute, those fail rather than the capability silently
going quiet.

Two related notes. The capability was deprecated in mcp 2.0.0 (SEP-2577); this
registration path bypasses the SDK's own deprecation warning, which is
deliberate, because a stdio server must not print to stderr on a healthy
launch. And the level vocabulary is pinned to the SDK's by a set-equality test:
the tuple used to order levels omitted `emergency`, and because the comparison
ran before the announced-flag was set, one missing string turned into a server
that failed every request permanently once a client asked for that level.

## Action pins follow what each action publishes

Where an action ships a floating major tag we use it (`checkout@v7`); where it
ships only exact versions we pin one (`setup-uv@v9.0.0`).

Floating majors take patches without a PR, so they are preferred. But it is not
a choice we get to make uniformly: `setup-uv` publishes no major tag at all, so
a patch pin is the only option there. That, not inconsistency, is why Dependabot
files a PR for every setup-uv release and none for the others.

SHA pinning is stronger — the only form a moved tag cannot subvert — but it
forfeits automatic patches everywhere. Not adopted. Worth revisiting given
`publish.yml` touches PyPI.

## The release re-locks itself, in the same job

release-please bumps `pyproject.toml` and knows nothing about `uv.lock`, so after
0.6.0 shipped the lockfile on `main` still named 0.5.0. Small and constant cost:
the first `uv sync` in a fresh clone rewrote it, so every contributor started
with a diff they had not made. And a lockfile that disagrees with the released
tree fails at the one thing a lockfile is for.

`release-please.yml` now re-locks the release PR and pushes to its branch, via
`scripts/relock-release-pr.sh`.

**It has to be the same job, not its own workflow.** release-please opens that PR
with the default `GITHUB_TOKEN`, and PRs created by that token do not trigger
`pull_request` workflows — the same limitation `publish.yml` already documents
for the release tag. An event-driven re-lock would simply never fire.

**The PR is found by title, not by the action's `pr` output.** Not because that
output is undocumented — `headBranchName` is declared on the `PullRequest`
interface the README links. The reason is stronger: `pr` is **unset in exactly
the case that matters**. release-please returns nothing for a PR whose body it
left unchanged, and the action filters those out, so both `pr` and `prs_created`
go empty on a push that updates no release-note content — the adoption case,
where the PR already exists and only the lock is stale, and the retry case. An
update that *does* change the body sets both, so this is narrower than "updates
never report a PR". The step therefore carries no `if:` gate, and finds the PR by
the title pattern we configure ourselves.

**A release PR has no CI at all.** Not "checks that describe the pre-re-lock
commit" — zero check runs, because the `GITHUB_TOKEN` that opened it cannot
trigger `pull_request` workflows. So the lockfile heading for the tag is
unvalidated by anything downstream, which is why the script runs `uv sync
--locked` itself and refuses to push a lock that does not satisfy
`pyproject.toml`. That check is not belt-and-braces; it is the only one there is.

Rejected: adding `uv lock --check` to CI instead. For the same reason — CI never
runs on a release PR, so a check there cannot see release-time drift. It would
catch the case where someone hand-edits `pyproject.toml` in an ordinary PR, which
is a different and much rarer problem.

## Sub-projects replace the adapter seam

`RootProvider` is gone. Its own docstring said the seam existed to supply
"the part a caller genuinely cannot supply: knowing that one root is a
portfolio containing several projects, and what each should be called." That
was never true. The one shipped implementation read a portfolio's own
`apexyard.projects.yaml` to answer a question the caller already knew the
answer to. `--sub-projects <dir>[,<dir>...]` states it instead: each
directory, resolved relative to the root, is scanned for checkouts, and each
one becomes a labelled root.

This makes `core/roots.py` **more** faithful to "Nothing is discovered," not
less. The adapter was the one place in the module that read a project's own
config to infer structure — every guess this file refuses to make elsewhere,
it was still making, once, for exactly the caller who most needed the module
to mean what it said. With the flag, the root and its sub-project
directories are both stated by the caller. Nothing left reads a config file
to find out.

A checkout is recognised by `.git` **existing**, not by `.git` being a
directory. `git worktree add` produces a `.git` that is a *file* — a
`gitdir:` pointer into the real repository's `worktrees/` directory — and an
`is_dir()` check would silently exclude every worktree while accepting every
plain clone. Silent and one-sided is exactly the failure class this project
exists to catch, so the guard tests existence, not shape.

**Removing `--adapter` is a breaking change, with no compatibility alias.** An
alias would keep `RootProvider`'s shape alive under another name, for a
second implementation that has never existed and that a flag does not need —
exactly the speculative extension point this change exists to remove.
Pre-1.0 projects are entitled to break on a minor, and this one does.

**What this gives up:** the adapter's one behaviour `--sub-projects` does not
reproduce is registry-driven opt-in — only *registered* projects with a local
clone were ever expanded. `--sub-projects` expands every checkout it finds
under the named directory, registered or not. Naming the directory is still
an explicit act; what turns up inside it is no longer filtered against
anything else.

Corrects a line in ["Nothing is discovered"](#nothing-is-discovered) above:
"Adapters are named explicitly" described a component that no longer exists.
The principle it was defending — nothing here infers what the caller can
state directly — is what motivated removing it.

---

Next: [architecture.md](architecture.md) · [BENCHMARK.md](../BENCHMARK.md)
