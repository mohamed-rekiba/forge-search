# Benchmark: does the semantic arm earn its cost?

**Yes.** Hybrid finds **87.5%** where lexical alone finds 25%.

**Corpus:** 718 files, ~110k lines, markdown + shell + TypeScript.
**Model:** `all-MiniLM-L6-v2`, ONNX, 384-dim, CPU. **Queries:** 8, six of them
deliberately conceptual. **`top_k`:** 10.

## Current result

```text
mode         recall      chars    p50 ms    p95 ms
lexical      25.0%        717       1.4       7.5
semantic     87.5%      2,651       4.8       6.9
hybrid       87.5%      2,774       6.0       8.0
```

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

## Two priors that lost to measurement

Both are implemented, tested, and **off by default**, with tests pinning the
default so they cannot drift back on the strength of the citation alone.

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

## Honest limits

- **8 queries.** The 87.5% vs 25% gap is large and consistent. The 25- and
  12.5-point deltas above are two- and one-query differences — directional, not
  conclusive. Neither prior reproduced here, which is enough to not override a
  local measurement; it is not enough to claim the priors are wrong in general.
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
  --query "how do I know if a merge is allowed::.claude/rules/pr-workflow.md" \
  --query "what stops me committing secrets::.claude/hooks/check-secrets.sh"
```

Every query names the file that should be found, so recall is measured at
**equal answer-recall**. Without that control a mode can "win" on tokens by
returning less — which is losing, more cheaply.

`verdict()` is written to reach the unflattering conclusion as readily as the
flattering one; five tests exist solely to keep it willing to say *"pure
overhead — remove the arm."*
