# Contributing

## Setup

```bash
uv sync --all-extras
make help
```

## The gate

```bash
make ci     # lint, format, typecheck, tests in both install shapes
```

| Target | What it does |
|---|---|
| `make test` | The suite in **both** install shapes |
| `make typecheck` | mypy + pyright, `src` only |
| `make smoke` | Installs the built wheel in a clean venv and runs it |
| `make security` | bandit + pip-audit |
| `make bench ROOT=<path>` | Answer-recall benchmark against a corpus |

## Tests run twice, on purpose

```bash
make test-lexical    # a genuinely minimal install
make test-semantic   # with the semantic extra
```

A lexical-only install is a **supported** configuration, not a degraded one,
and it runs different code: the semantic modules are never imported, the arm
reports itself absent, search falls back. A suite run only with the extra
installed never executes any of that, and an `ImportError` there reaches a
user as a server that will not start.

The two legs use **separate environments**, not a flag. `uv run --no-extra
semantic` does not uninstall anything — the shared venv still carries the
extra, so the import succeeds and both legs test the same thing. That mistake
shipped here once and reported `210 passed` twice with nothing to indicate the
matrix was a no-op. CI asserts the lexical leg genuinely lacks the extra.

## The silent-failure checklist

Every defect this project has shipped returned **zero results or a plausible
wrong answer, with no error**. A green suite has never once caught one of
them. Before opening a PR:

- **If it can fail, does it fail loudly?** No swallowed exception, no empty
  list standing in for an error. "No results" and "your query was invalid"
  must never look the same — the caller's next action differs completely.
- **If it changes what gets indexed or embedded** — chunking, model, schema,
  traversal — does a stale index get **discarded** rather than reused? Add a
  fingerprint or bump a version. Reused vectors across a model change are
  cosine distance between two unrelated spaces.
- **Is there a test that fails if this regresses, and did you watch it fail?**
  Write the test, break the code, confirm red. A test that has never failed
  proves nothing about what it claims to protect.
- **Did you run it against real content?** Every bug listed in
  [docs/decisions.md](docs/decisions.md) was found this way and none by the
  suite.

## Measuring retrieval changes

Anything touching chunking, models, fusion, or ranking needs numbers:

```bash
make bench ROOT=/abs/path/to/corpus     # before and after, same fixture
```

The fixture at `bench/apexyard.txt` is 26 queries, so **one query flipping is
3.8 points**. Treat anything under ~8 points as directional, not conclusive.
This matters: a path-prefix variant once measured +12.5 / −25 on an 8-query
fixture, earned a code branch and a rationale, and turned out to be one or two
queries moving — at 26 queries all variants scored identically. The branch was
deleted.

Update [BENCHMARK.md](BENCHMARK.md) when numbers change, including when they
get worse.

## Style

Ruff, line length 90. Lint config is deliberately modest — no linter would
have caught a single bug this project shipped, so lint keeps the source tidy
and the tests do the real work.

Type checking covers `src` only and is not `--strict`. It earns its place on
the CLI-to-server seam, which has already shipped a startup crash from a
keyword argument passed to a dataclass with no such field.

Comments explain **why**, not what. If a decision cost a measurement or a
reversal, the number and the reversal belong in the comment — several of the
most useful comments in this codebase are records of something that failed.

## Commits and releases

Conventional commits. release-please builds the CHANGELOG and the version bump
from them, so a `feat:` that lands as `chore:` is a release that never
happens.

Merging the release PR creates a `v0.2.0` tag. Publishing is a separate
workflow — see the note in `.github/workflows/publish.yml` about
`GITHUB_TOKEN` not triggering workflow runs.

## Project layout

```text
src/forge_search/
  cli.py              argument surface
  core/               generic; knows nothing about any integration
  adapters/           integration-specific root expanders
tests/                mirrors core/, plus an end-to-end MCP smoke suite
bench/                benchmark fixtures
docs/                 architecture, indexing, searching, decisions
```

`core/` must never import `adapters/`. A test asserts it — by checking import
statements rather than word occurrences, because the first version passed
while a `core/` docstring cheerfully mentioned `--adapter apexyard`.
