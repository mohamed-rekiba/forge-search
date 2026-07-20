"""The bench harness.

The verdict tests matter most. A measurement harness that can only produce
encouraging conclusions is not a measurement — so these check that it says
"switch the semantic arm off" as readily as it says the opposite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.core.bench import BenchQuery, BenchReport, ModeResult, run_bench
from forge_search.core.embedding import HashingEmbedder
from forge_search.core.hybrid import HybridIndex, SearchMode
from forge_search.core.roots import Root
from forge_search.core.semantic import vec_available

requires_vec = pytest.mark.skipif(not vec_available(), reason="sqlite-vec not installed")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def repo(tmp_path: Path) -> Root:
    src = tmp_path / "repo"
    (src / "src").mkdir(parents=True)
    (src / "src" / "auth.py").write_text(
        "def validate_token(token):\n    return decode(token)\n"
    )
    (src / "src" / "session.py").write_text(
        "def refresh_session(user):\n    return issue(user)\n"
    )
    return Root(label="repo", path=src)


QUERIES = [
    BenchQuery(query="validate_token", expect_path="src/auth.py"),
    BenchQuery(query="refresh_session", expect_path="src/session.py"),
]


class TestHarness:
    def test_reports_per_mode(self, repo: Root) -> None:
        with HybridIndex(repo) as idx:
            idx.reindex()
            report = run_bench(idx, QUERIES)

        assert SearchMode.LEXICAL in report.results
        assert report.query_count == 2

    def test_recall_is_measured_against_expected_paths(self, repo: Root) -> None:
        with HybridIndex(repo) as idx:
            idx.reindex()
            report = run_bench(idx, QUERIES)

        assert report.results[SearchMode.LEXICAL].recall == 1.0

    def test_misses_are_named_not_just_counted(self, repo: Root) -> None:
        """A recall number without the failing queries is not actionable."""
        with HybridIndex(repo) as idx:
            idx.reindex()
            report = run_bench(
                idx, [BenchQuery(query="nonexistent_symbol", expect_path="src/auth.py")]
            )

        result = report.results[SearchMode.LEXICAL]
        assert result.recall == 0.0
        assert result.misses and "nonexistent_symbol" in result.misses[0]

    def test_empty_fixture_is_rejected(self, repo: Root) -> None:
        """Inventing queries would let the harness flatter whichever arm it
        was written to favour."""
        with HybridIndex(repo) as idx:
            with pytest.raises(ValueError, match="query fixture"):
                run_bench(idx, [])

    def test_semantic_mode_skipped_when_unavailable(self, repo: Root) -> None:
        with HybridIndex(repo) as idx:  # no embedder
            idx.reindex()
            report = run_bench(idx, QUERIES)

        assert SearchMode.SEMANTIC not in report.results

    def test_latency_percentiles_are_recorded(self, repo: Root) -> None:
        with HybridIndex(repo) as idx:
            idx.reindex()
            report = run_bench(idx, QUERIES)

        result = report.results[SearchMode.LEXICAL]
        assert result.p50_ms >= 0 and result.p95_ms >= result.p50_ms


class TestVerdict:
    """The harness must be willing to reach an unflattering conclusion."""

    def _report(
        self, lexical: float, hybrid: float, semantic: float | None = None
    ) -> BenchReport:
        results = {
            SearchMode.LEXICAL: ModeResult(SearchMode.LEXICAL, lexical, 100, [1.0]),
            SearchMode.HYBRID: ModeResult(SearchMode.HYBRID, hybrid, 100, [1.0]),
        }
        if semantic is not None:
            results[SearchMode.SEMANTIC] = ModeResult(
                SearchMode.SEMANTIC, semantic, 100, [1.0]
            )
        return BenchReport(results=results, query_count=10)

    def test_says_remove_the_arm_when_it_finds_nothing(self) -> None:
        verdict = self._report(lexical=1.0, hybrid=1.0, semantic=0.0).verdict()
        assert "pure overhead" in verdict
        assert "remove the arm" in verdict

    def test_says_not_earning_when_hybrid_matches_lexical(self) -> None:
        """The most likely real outcome, given zero conceptual queries were
        observed in the target workload."""
        verdict = self._report(lexical=0.9, hybrid=0.9, semantic=0.5).verdict()
        assert "not earning its cost" in verdict
        assert "Weight it to zero" in verdict

    def test_says_worse_when_hybrid_displaces_correct_results(self) -> None:
        verdict = self._report(lexical=0.9, hybrid=0.5, semantic=0.3).verdict()
        assert "WORSE" in verdict

    def test_credits_the_arm_when_it_genuinely_helps(self) -> None:
        verdict = self._report(lexical=0.6, hybrid=0.9, semantic=0.7).verdict()
        assert "earns its cost" in verdict

    def test_inconclusive_without_both_baselines(self) -> None:
        report = BenchReport(
            results={SearchMode.LEXICAL: ModeResult(SearchMode.LEXICAL, 1.0, 10, [1.0])},
            query_count=5,
        )
        assert "inconclusive" in report.verdict()


@requires_vec
class TestAblation:
    def test_all_three_modes_are_measured(self, repo: Root) -> None:
        with HybridIndex(repo, embedder=HashingEmbedder()) as idx:
            idx.reindex()
            report = run_bench(idx, QUERIES)

        assert set(report.results) == set(SearchMode.ALL)

    def test_render_includes_every_mode_and_a_verdict(self, repo: Root) -> None:
        with HybridIndex(repo, embedder=HashingEmbedder()) as idx:
            idx.reindex()
            rendered = run_bench(idx, QUERIES).render()

        for mode in SearchMode.ALL:
            assert mode in rendered
        assert "Verdict:" in rendered
