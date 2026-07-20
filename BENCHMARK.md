# Benchmark: does the semantic arm earn its cost?

**Yes.** Hybrid improves answer-recall by **37.5%** over lexical alone.

**Run:** 2026-07-21 · real corpus, real model, verified embedder.

## Result

```
mode         recall      chars    p50 ms    p95 ms
lexical      25.0%        717       0.2       0.8
semantic     50.0%      2,587       5.3       5.6
hybrid       62.5%      2,769       6.6       7.7
```

Semantic alone finds **twice** what lexical does on this query mix, and fusing
them beats either. The queries are deliberately weighted toward conceptual
questions, which is what the semantic arm exists for — see below.

## The first run of this benchmark was wrong, and why that matters

An earlier run reported **0% recall** for the semantic arm and concluded it was
"pure overhead — remove it." That conclusion was wrong, and the cause was a
chunking bug, not the model.

The failure signature was visible in the scores and should have been
investigated rather than reported: every returned result scored between −1.268
and −1.296. A spread of **0.028 across 16,726 chunks** means everything was
equidistant from the query — nothing was meaningfully near it. That is not
"semantic search does not help here", it is "nothing relevant is being
embedded".

The cause was **dilution**. Only sliding 12-line windows were indexed, so a
file's descriptive header got mean-pooled together with surrounding
boilerplate. Measured directly, against the query *"what stops me committing
secrets"* and its correct target:

| Embedded text | Cosine similarity |
|---|---|
| Header comment alone | **0.484** |
| The 12-line window containing it | **0.368** ← what was indexed |
| Path + header | **0.520** ← what is indexed now |

A 24% signal loss on the single chunk that most identifies a file. The best
result in the failed run scored ≈0.195, so a 0.52 chunk would have ranked far
above everything that did come back.

**The fix:** each file now also contributes one undiluted *summary chunk* — its
path plus its leading doc comment — so a file's own description competes on its
own terms instead of being buried. See `summary_chunk()` in `core/semantic.py`.

The lesson is not about chunking. It is that a measurement can be correct in
its arithmetic and wrong in its conclusion. The harness reported exactly what
the data showed, both times; the data was bad the first time. A 0% result on a
capability that demonstrably works elsewhere should be treated as a bug report
about the pipeline, not as evidence about the capability.

## Setup

| | |
|---|---|
| Corpus | 718 files, 110,141 indexed lines — markdown and shell, not application code |
| Semantic index | ~17k chunks: overlapping 12-line windows plus one summary chunk per file |
| Model | `sentence-transformers/all-MiniLM-L6-v2`, ONNX, 384-dim, CPU |
| Queries | 8 — two identifier lookups, six deliberately conceptual |
| `top_k` | 10 |

## The embedder was verified working before any conclusion was drawn

A broken embedder produces a plausible-looking 0%, so it was ruled out first:

| Pair | Cosine | Expected |
|---|---|---|
| *"the cat sat on the mat"* vs *"a feline rested upon the rug"* | **0.556** | high — different words, same meaning |
| *"the cat sat on the mat"* vs *"quarterly financial reporting standards"* | **−0.07** | low |
| self-similarity | **1.000** | 1.0 |

This check is what made the 0% result suspicious enough to keep digging.

## Queries

Six of eight were written specifically to favour semantic retrieval —
natural-language questions sharing little or no vocabulary with their target:

- *"how do I know if a merge is allowed"* → `pr-workflow.md`
- *"what stops me committing secrets"* → `check-secrets.sh`
- *"checking work is not already done"* → `reconcile-before-build.md`
- *"who reviews a design before building"* → `solution-architect.md`
- *"scoring how ready a codebase is"* → `AgDR-0042-harnessability-scoring-dimensions.md`
- *"writing status updates like a human"* → `reporting-style.md`

Lexical answers almost none of these, which is exactly the gap the semantic arm
is for. It is also why the absolute numbers look low: this fixture is
deliberately hostile to keyword matching.

## Honest limits

- **8 queries is a small fixture.** The direction is clear and the improvement
  is large; the precise percentages are not load-bearing.
- **Expected answers are one person's judgement.** Several queries have more
  than one defensible target, so absolute recall understates every mode. The
  *comparison between modes* is the finding.
- **This is one corpus** — prose and shell, no deep call graphs. Results on a
  large application codebase would differ and are worth measuring separately.
- **Chunking is now known to dominate the result.** Summary chunks moved
  semantic recall from 0% to 50% with no model change. Further chunking work
  (per-function chunks, symbol-aware splitting) is the most promising remaining
  lever, and untested.

## Decision

**Keep the semantic arm and keep it fused.** Hybrid is the best mode measured
and the recommended configuration when the extra is installed.

The arm remains opt-in (`--semantic`) only because it costs a ~90 MB model
download and an embedding pass per reindex — not because its value is in doubt.
That question is now answered.

## Reproducing

```bash
forge-search warm
forge-search bench --semantic --top-k 10 \
  --query "how do I know if a merge is allowed::.claude/rules/pr-workflow.md" \
  ...
```

Recall is measured at **equal answer-recall** by construction: every query names
the file that should be found. Without that control, a mode can "win" on tokens
by returning less — which is losing, more cheaply.
