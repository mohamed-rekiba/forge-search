# Benchmark: does the semantic arm earn its cost?

**Yes.** Hybrid finds **87.5%** where lexical alone finds 25%.

**Corpus:** 718 files, ~110k lines, markdown + shell + TypeScript.
**Model:** `all-MiniLM-L6-v2`, ONNX, 384-dim, CPU. **`top_k`:** 10.
**Queries:** `bench/apexyard.txt` — 26, mostly conceptual. The headline row
below is the original 8-query run; the 26-query numbers appear under
reranking. Absolute recall differs between fixtures (the expanded set is
harder); only same-fixture comparisons mean anything.

## Current result

```text
mode         recall      chars    p50 ms    p95 ms
lexical      25.0%        717       1.4       7.5
semantic     87.5%      2,651       4.8       6.9
hybrid       87.5%      2,774       6.0       8.0
```

On the harder 26-query fixture (`bench/apexyard.txt`), against a **clean**
index:

```text
lexical      11.5%      1,540       0.5       2.8
semantic     61.5%     10,533       4.9       5.2
hybrid       61.5%     11,335       6.1       6.6
```

"Clean" is load-bearing. The same fixture against an index accumulated across
several experiment variants reported **50.0% / 57.7%** — 11.5 and 3.8 points
low. Nothing recorded which model or chunking had produced the stored vectors,
so switching `--model` or toggling structural chunking silently kept the old
ones. Every reranking measurement below was taken on that index and is
correspondingly understated; the conclusion (reranking did not help here) is
unchanged, but the absolute numbers are not trustworthy.

That gap is now closed: the semantic index records a model + chunking
fingerprint and discards itself when either changes.

## How it got there — and what moved the needle

Every row below is the same corpus, same model, same eight queries. **Only the
chunking changed.**

| Chunking | semantic | hybrid |
|---|---:|---:|
| Sliding 12-line windows, no context | **0.0%** | 25.0% |
| \+ one summary chunk per file (path + doc comment) | 50.0% | 62.5% |
| \+ breadcrumb on **every** chunk (`path > heading/function`) | **87.5%** | **87.5%** |

**Context is the dominant variable, by a wide margin.** The model was constant
across all three rows. That is the single most useful finding here, and it was
arrived at backwards: the 0% was first reported as evidence *against* the
semantic arm, then diagnosed as a chunking bug in this code.

The mechanism, measured directly against *"what stops me committing secrets"*
and its correct target:

| Embedded text | Cosine |
|---|---:|
| Header comment alone | 0.484 |
| The 12-line window containing it | 0.368 |
| Path + header | 0.520 |

A 24% signal loss to mean-pooling, on the one chunk that most identifies a
file. Prepending location is the deterministic, no-LLM form of *contextual
retrieval* — the published technique generates per-chunk context with an LLM
and reports a 35–49% reduction in retrieval failures.

## Three priors that lost to measurement

All three are implemented, tested, and **off by default**, with tests pinning
the default so they cannot drift back on the strength of the citation alone.

### Retrieval-tuned model vs symmetric model

`sentence-transformers`' own docs distinguish *symmetric* similarity
(`all-MiniLM`'s training regime) from *asymmetric* retrieval — short query
against long passage — and recommend the multi-QA family for the latter. That
is the model author's own stated scope, not folklore.

```text
all-MiniLM-L6-v2        (symmetric, "wrong regime")   87.5%
multi-qa-MiniLM-L6-cos  (asymmetric, "right regime")  62.5%
```

The theoretically-correct model lost by 25 points. `all-MiniLM` stays default.

### Structure-aware chunking vs line windows

cAST (ACL Findings EMNLP 2025, peer-reviewed) reports ~4.3 points of Recall@5
*gained* from splitting on AST boundaries rather than line counts.

```text
breadcrumbs + line windows        87.5%
breadcrumbs + structural chunks   75.0%
```

It lost 12.5 points here. Plausible reasons, none verified: this corpus is
mostly prose and shell rather than the application code cAST benchmarked;
regex boundaries are cruder than a real AST; variable-size regions may interact
badly with a fixed `top_k`. Available via `USE_STRUCTURAL_CHUNKING`.

### Cross-encoder reranking

The largest single-stage gain in the retrieval literature — commonly reported
as a 30–50% reduction in the failures that survive hybrid search. A bi-encoder
never sees query and document together; a cross-encoder scores the pair
jointly, so it should judge relevance better. It is the one result here that
was expected to be uncontroversial.

Measured on 26 queries with `ms-marco-MiniLM-L-6-v2` (ONNX, CPU), reranking
the fused top-30:

```text
mode        recall (off → on)     p50 ms (off → on)
lexical     11.5%  →  11.5%          0.6  →   0.2
semantic    50.0%  →  50.0%          5.2  →  61.9
hybrid      57.7%  →  50.0%          6.6  →  59.5
```

**It lost.** Neutral on both single arms, and it actively *demoted* two correct
answers that RRF had already placed in the hybrid top 10 — at roughly 10× the
latency. The plausible reading, unverified: RRF over two arms is already a
strong ranker here, and a cross-encoder judging a one-line excerpt through a
256-token window has strictly less information to work with than the fusion
step it is overruling. On longer chunks, or a weaker first stage, the prior may
well hold.

Two things went wrong on the way to that number, and both were invisible:

- **Fusion truncated to `limit` before reranking.** The reranker was handed the
  ten results it was about to return, so recall@10 could not move by
  construction — while the full latency was still paid. The first "no effect"
  reading was this bug, not a finding. Fixed; pinned by a test.
- **A path-prefix variant was tuned on 8 queries.** Prefixing each excerpt with
  its path swung lexical +12.5 and semantic −25 points — which read as a real
  effect and got a branch, a docstring, and a rationale. At 26 queries all
  three variants scored *identically*: it was one or two queries flipping.
  The branch was deleted. **A 12.5-point swing on an 8-query fixture is noise
  wearing a finding's clothes**, and it is why the fixture is now 26 queries in
  version control (`bench/apexyard.txt`) instead of a line of shell history.

Available via `--rerank`.

## Honest limits

- **26 queries.** One query flipping is 3.8 points, so anything under ~8 points
  is not a finding. It was 8 queries until a 12.5-point "effect" turned out to
  be one query moving — see the reranking section. The fixture lives at
  `bench/apexyard.txt`; a benchmark you cannot re-run is an anecdote.
- **Three priors failed to reproduce here.** That is enough to not override a
  local measurement. It is not enough to claim the priors are wrong in general,
  and one corpus of prose and shell is exactly where you should expect
  code-retrieval results to travel badly.
- **Expected answers are one person's judgement.** Several queries have more
  than one defensible target, so absolute recall understates every mode. The
  *comparison between modes* is the finding.
- **One corpus.** Prose and shell, no deep call graphs. On real application
  code both priors may well hold, and both remain one flag away.
- **Chunking now demonstrably dominates.** Further work there — better
  boundaries, tuned sizes, token-based windows — is the most promising
  remaining lever, and untested.

## The embedder was verified before any conclusion

A broken embedder produces a plausible-looking 0%, so it was ruled out first:

| Pair | Cosine |
|---|---:|
| *"the cat sat on the mat"* vs *"a feline rested upon the rug"* | 0.556 |
| *"the cat sat on the mat"* vs *"quarterly financial reporting"* | −0.07 |
| self-similarity | 1.000 |

It captures meaning correctly. That check is what made the original 0% look
suspicious enough to keep digging rather than publish as a verdict.

## Reproducing

```bash
forge-search warm
forge-search bench --root /abs/path --semantic --top-k 10 \
  --fixture bench/apexyard.txt

# or ad-hoc, without a fixture file:
forge-search bench --root /abs/path --semantic \
  --query "what stops me committing secrets::.claude/hooks/check-secrets.sh"
```

Every query names the file that should be found, so recall is measured at
**equal answer-recall**. Without that control a mode can "win" on tokens by
returning less — which is losing, more cheaply.

`verdict()` is written to reach the unflattering conclusion as readily as the
flattering one; five tests exist solely to keep it willing to say *"pure
overhead — remove the arm."*
