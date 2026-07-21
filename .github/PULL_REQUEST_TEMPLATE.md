## What & Why

<!-- What changed, and why it matters to whoever reads this next. -->

Fixes #

## Type

- [ ] `feat` — New feature
- [ ] `fix` — Bug fix
- [ ] `refactor` — Restructure, no behaviour change
- [ ] `docs` — Documentation only
- [ ] `test` — Tests only
- [ ] `chore` — Dependencies, CI, cleanup

## Testing

- [ ] `make test` passes — **both** install shapes, lexical-only and semantic
- [ ] `make lint-check format-check typecheck` clean
- [ ] Ran it against a real corpus, not only fixtures

## Silent-failure check

Every defect this project has shipped returned **zero results or a plausible
wrong answer, with no error** — a hyphenated query parsed as syntax, an index
stamped with the wrong version, vectors reused across two models. A green
suite has never once caught one of them.

- [ ] If this can fail, does it fail **loudly**? No swallowed exception, no
      empty list standing in for an error.
- [ ] If it changes what gets indexed or embedded (chunking, model, schema,
      traversal), does a stale index get **discarded** rather than reused?
- [ ] Is there a test that fails if this regresses — and did you watch it fail?

## Measurement

<!-- Only if this touches retrieval quality. Numbers, or say "n/a". -->

- [ ] `make bench ROOT=<corpus>` before and after, same fixture
- [ ] Deltas are larger than one query's worth of noise (3.8 points at 26
      queries), or reported as directional rather than conclusive

## Pre-merge

- [ ] Under ~400 lines, or justified
- [ ] Conventional-commit messages — release-please builds the CHANGELOG and
      the version bump from these, so a `feat:` that lands as `chore:` is a
      release that never happens
- [ ] README / BENCHMARK updated if behaviour or numbers changed
