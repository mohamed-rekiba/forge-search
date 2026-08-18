"""Measuring whether the semantic arm earns its cost.

The semantic arm ships on a *parity* argument, not a measured one. This module
is the thing that turns that into a number, and it is written so the honest
answer — "it contributes nothing here, weight it to zero" — is as easy to
reach as the flattering one.

**Answer-recall is the control, and it is not optional.** Token counts are
trivial to improve by returning less; a mode that halves its output by
dropping the answer has not won, it has failed more cheaply. So every
comparison here is made *at equal answer-recall*, and any comparison where
recall differs is reported as such rather than quietly averaged in.

**Ablation is the method.** Each arm runs alone and together over the same
queries against the same corpus. If hybrid does not beat lexical on recall,
the semantic arm is overhead — and the fix is a configuration change
(`ArmWeights(semantic=0.0)`), not a release.

A query fixture is supplied by the caller. Inventing queries here would let
the harness flatter whichever arm it was written to favour; the queries have
to come from what people actually search for.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from .hybrid import HybridIndex, SearchMode

__all__ = ["BenchQuery", "ModeResult", "BenchReport", "run_bench"]


@dataclass(frozen=True, slots=True)
class BenchQuery:
    """One query and the file that should be found for it.

    `expect_path` is what makes recall measurable. A query with no known
    correct answer can only be scored on how *much* it returned, which is the
    metric that rewards returning nothing.
    """

    query: str
    expect_path: str
    note: str = ""


@dataclass(slots=True)
class ModeResult:
    mode: str
    recall: float
    total_chars: int
    latencies_ms: list[float] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]


@dataclass(slots=True)
class BenchReport:
    results: dict[str, ModeResult]
    query_count: int

    def verdict(self) -> str:
        """A plain statement of what the numbers support.

        Deliberately blunt, and deliberately willing to say the semantic arm
        should be switched off. A harness that can only produce encouraging
        conclusions is not a measurement.
        """
        lexical = self.results.get(SearchMode.LEXICAL)
        hybrid = self.results.get(SearchMode.HYBRID)
        semantic = self.results.get(SearchMode.SEMANTIC)

        if lexical is None or hybrid is None:
            return "inconclusive: both lexical and hybrid modes are required"

        if semantic is not None and semantic.recall == 0.0:
            return (
                "The semantic arm found nothing on any query. It is pure overhead "
                "on this corpus — set ArmWeights(semantic=0.0), and if that holds "
                "across corpora, remove the arm rather than carrying it."
            )

        delta = hybrid.recall - lexical.recall
        if delta > 0.05:
            return (
                f"Hybrid improves answer-recall by {delta:.1%} over lexical alone. "
                "The semantic arm earns its cost here."
            )
        if delta < -0.05:
            return (
                f"Hybrid is {abs(delta):.1%} WORSE than lexical alone — the semantic "
                "arm is displacing correct results. Reduce its weight or disable it."
            )
        return (
            f"Hybrid and lexical are within {abs(delta):.1%} on answer-recall. "
            "The semantic arm is not earning its cost on this corpus: it adds a "
            "~90 MB model, an embedding pass on every reindex, and a second index, "
            "for no measured retrieval gain. Weight it to zero unless a corpus "
            "with genuinely conceptual queries says otherwise."
        )

    def render(self) -> str:
        lines = [
            f"forge-search bench — {self.query_count} queries",
            "",
            f"{'mode':10} {'recall':>8} {'chars':>10} {'p50 ms':>9} {'p95 ms':>9}",
            "-" * 50,
        ]
        for mode in SearchMode.ALL:
            result = self.results.get(mode)
            if result is None:
                continue
            lines.append(
                f"{result.mode:10} {result.recall:>7.1%} {result.total_chars:>10,} "
                f"{result.p50_ms:>9.1f} {result.p95_ms:>9.1f}"
            )

        lines += ["", "Verdict:", f"  {self.verdict()}"]

        for mode in SearchMode.ALL:
            result = self.results.get(mode)
            if result and result.misses:
                lines.append("")
                lines.append(f"{mode} missed {len(result.misses)}:")
                lines += [f"  - {m}" for m in result.misses[:10]]
        return "\n".join(lines)


def run_bench(
    index: HybridIndex,
    queries: Sequence[BenchQuery],
    *,
    top_k: int = 10,
    modes: Sequence[str] = SearchMode.ALL,
) -> BenchReport:
    """Run every query through every mode and report.

    Token counts are approximated by returned characters. That is honest about
    what it is: a real tokenizer would give exact numbers, but the *comparison*
    between modes is what matters here, and character count is monotonic with
    tokens for text of the same kind. The absolute figure should not be quoted
    as a token count.
    """
    if not queries:
        raise ValueError(
            "bench needs a query fixture; inventing queries here would "
            "let the harness flatter whichever arm it was written to favour.\n"
            "  --fixture bench/apexyard.txt       (the bundled fixture)\n"
            "  --query 'text::expected/path'      (repeatable, ad hoc)\n"
            "Each entry pairs a query with the path it should find, because "
            "recall cannot be measured without a known-correct answer."
        )

    results: dict[str, ModeResult] = {}

    for mode in modes:
        if mode == SearchMode.SEMANTIC and not index.has_semantic:
            continue

        found = 0
        total_chars = 0
        latencies: list[float] = []
        misses: list[str] = []

        for item in queries:
            start = time.perf_counter()
            hits = index.search(item.query, limit=top_k, mode=mode)
            latencies.append((time.perf_counter() - start) * 1000)

            total_chars += sum(len(h.excerpt) for h in hits)
            if any(h.path == item.expect_path for h in hits):
                found += 1
            else:
                misses.append(f"{item.query!r} (expected {item.expect_path})")

        results[mode] = ModeResult(
            mode=mode,
            recall=found / len(queries),
            total_chars=total_chars,
            latencies_ms=latencies,
            misses=misses,
        )

    return BenchReport(results=results, query_count=len(queries))
