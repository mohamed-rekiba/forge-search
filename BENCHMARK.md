# Benchmark: does the semantic arm earn its cost?

**Run:** 2026-07-21 · real corpus, real model, verified embedder.

## Result

```
mode         recall      chars    p50 ms    p95 ms
lexical      25.0%        717       0.2       0.6
semantic      0.0%      3,541       4.8       5.7
hybrid       25.0%      3,146       5.9       6.4
```

**The semantic arm found nothing on any query.** It returns ~5× more text, takes
~24× longer, and contributes zero correct answers. Hybrid matches lexical
exactly on recall while costing 4.4× the tokens and 30× the latency.

## Setup

| | |
|---|---|
| Corpus | 718 files, 110,141 indexed lines — markdown and shell, not application code |
| Semantic index | 16,726 chunks embedded (12-line overlapping windows) |
| Model | `sentence-transformers/all-MiniLM-L6-v2`, ONNX, 384-dim, CPU |
| Queries | 8 — two identifier lookups, six deliberately conceptual |
| `top_k` | 10 |

## The embedder was verified working before drawing any conclusion

A broken embedder produces exactly this result, so it was ruled out first:

| Pair | Cosine | Expected |
|---|---|---|
| *"the cat sat on the mat"* vs *"a feline rested upon the rug"* | **0.556** | high — different words, same meaning |
| *"the cat sat on the mat"* vs *"quarterly financial reporting standards"* | **−0.07** | low |
| self-similarity | **1.000** | 1.0 |

It captures meaning correctly. The 0% is a real result, not a bug.

## Queries

Six of the eight were written specifically to favour semantic retrieval —
natural-language questions sharing no vocabulary with their target file:

- *"how do I know if a merge is allowed"* → `pr-workflow.md`
- *"what stops me committing secrets"* → `check-secrets.sh`
- *"checking work is not already done"* → `reconcile-before-build.md`
- *"who reviews a design before building"* → `solution-architect.md`
- *"scoring how ready a codebase is"* → `AgDR-0042-harnessability-scoring-dimensions.md`
- *"writing status updates like a human"* → `reporting-style.md`

The semantic arm answered none of them.

## Honest limits of this result

- **8 queries is a small fixture.** The direction is clear; the precise
  percentages are not load-bearing.
- **Expected answers are one person's judgement.** Several queries have more
  than one defensible target, so absolute recall understates every mode —
  which is why the *comparison* between modes is the finding, not the 25%.
- **Lexical only scored 25% too.** These are hard queries. The finding is not
  "lexical is great"; it is "semantic adds nothing that lexical does not
  already provide, at meaningfully higher cost."
- **This is one corpus.** Prose rules and shell scripts, no deep call graphs.
  A large application codebase with genuinely conceptual queries could plausibly
  reverse this, and that is exactly the condition under which it should be
  re-run.
- **Chunk retrieval over 16,726 chunks at `top_k=10`** is a demanding
  needle-in-haystack. A larger `top_k` before fusion, or finer chunking, might
  move it — untested.

## Decision

**Leave the semantic arm off.** It is already opt-in (`--semantic`), so the
default needs no change — this measurement says not to enable it here.

If it stays at zero across other corpora, remove the arm rather than carrying
it: a ~90 MB model, an embedding pass on every reindex, a second index, and
`sqlite-vec` as a dependency is real weight for measured-zero benefit.

## Reproducing

```bash
forge-search warm
forge-search bench --semantic --top-k 10 \
  --query "how do I know if a merge is allowed::.claude/rules/pr-workflow.md" \
  ...
```

Recall is measured at **equal answer-recall** by construction: every query
names the file that should be found. Without that control, a mode can "win"
on tokens by returning less — which is losing, more cheaply.
