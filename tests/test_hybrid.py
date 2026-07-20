"""Hybrid search: both arms, fused.

Two things get the most attention here.

**Arms stay in step.** If one arm still holds a deleted file, searches return
hits pointing at content that no longer exists — a confident wrong answer, the
shape this project exists to eliminate.

**Lexical-only remains fully functional.** It is a supported configuration, not
a degraded one, so every hybrid path must have a sensible lexical-only
behaviour rather than an error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.core.embedding import HashingEmbedder
from forge_search.core.fusion import ArmWeights
from forge_search.core.hybrid import HybridIndex, SearchMode
from forge_search.core.roots import Root
from forge_search.core.semantic import vec_available

requires_vec = pytest.mark.skipif(
    not vec_available(), reason="sqlite-vec not installed (lexical-only install)"
)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def repo(tmp_path: Path) -> Root:
    src = tmp_path / "repo"
    (src / "src").mkdir(parents=True)
    (src / "src" / "auth.py").write_text(
        "def validate_token(token):\n"
        "    # check the signature before trusting anything\n"
        "    return decode(token)\n"
    )
    (src / "src" / "session.py").write_text(
        "def refresh_session(user):\n"
        "    # renew credentials when they are close to expiry\n"
        "    return issue(user)\n"
    )
    (src / "README.md").write_text("# Demo\n\nAuthentication and sessions.\n")
    return Root(label="repo", path=src)


class TestLexicalOnly:
    """No embedder supplied — the default, and a supported configuration."""

    def test_search_works_without_an_embedder(self, repo: Root) -> None:
        with HybridIndex(repo) as idx:
            idx.reindex()
            assert idx.search("validate_token")

    def test_has_semantic_is_false(self, repo: Root) -> None:
        with HybridIndex(repo) as idx:
            assert not idx.has_semantic

    def test_hybrid_mode_degrades_to_lexical(self, repo: Root) -> None:
        """Asking for hybrid without a semantic arm must answer, not error."""
        with HybridIndex(repo) as idx:
            idx.reindex()
            assert idx.search("validate_token", mode=SearchMode.HYBRID)

    def test_explicit_semantic_mode_returns_nothing(self, repo: Root) -> None:
        """Honest: the caller asked for an arm that is not installed."""
        with HybridIndex(repo) as idx:
            idx.reindex()
            assert idx.search("validate_token", mode=SearchMode.SEMANTIC) == []


@requires_vec
class TestHybrid:
    @pytest.fixture
    def index(self, repo: Root):
        with HybridIndex(repo, embedder=HashingEmbedder()) as idx:
            idx.reindex()
            yield idx

    def test_semantic_arm_is_active(self, index: HybridIndex) -> None:
        assert index.has_semantic

    def test_all_modes_return_results(self, index: HybridIndex) -> None:
        for mode in SearchMode.ALL:
            assert index.search("validate_token", mode=mode), f"{mode} returned nothing"

    def test_arms_can_be_ablated_independently(self, index: HybridIndex) -> None:
        """The ablation `bench` depends on: each arm measurable alone."""
        lexical = index.search("validate_token", mode=SearchMode.LEXICAL)
        semantic = index.search("validate_token", mode=SearchMode.SEMANTIC)

        assert lexical and semantic
        assert all(h.path for h in lexical + semantic)

    def test_hybrid_scores_are_descending(self, index: HybridIndex) -> None:
        scores = [
            h.score for h in index.search("validate_token", mode=SearchMode.HYBRID)
        ]
        assert scores == sorted(scores, reverse=True)

    def test_zero_semantic_weight_matches_lexical_order(self, repo: Root) -> None:
        """The removability guarantee: weighting the arm to zero must give
        back exactly the lexical ranking, with no residual influence."""
        with HybridIndex(
            repo,
            embedder=HashingEmbedder(),
            weights=ArmWeights(lexical=1.0, semantic=0.0),
        ) as idx:
            idx.reindex()
            hybrid = [h.path for h in idx.search("validate", mode=SearchMode.HYBRID)]
            lexical = [h.path for h in idx.search("validate", mode=SearchMode.LEXICAL)]

        assert hybrid == lexical

    def test_limit_is_respected_across_arms(self, index: HybridIndex) -> None:
        assert len(index.search("def", limit=2, mode=SearchMode.HYBRID)) <= 2

    def test_paths_stay_relative(self, index: HybridIndex) -> None:
        for mode in SearchMode.ALL:
            for hit in index.search("validate_token", mode=mode):
                assert not hit.path.startswith("/")


@requires_vec
class TestArmsStayInStep:
    """Both arms must agree about which files exist."""

    @pytest.fixture
    def index(self, repo: Root):
        with HybridIndex(repo, embedder=HashingEmbedder()) as idx:
            idx.reindex()
            yield idx

    def test_deleted_file_disappears_from_both_arms(
        self, index: HybridIndex, repo: Root
    ) -> None:
        assert index.search("refresh_session", mode=SearchMode.SEMANTIC)

        (repo.path / "src" / "session.py").unlink()
        index.reindex()

        for mode in SearchMode.ALL:
            paths = {h.path for h in index.search("refresh_session", mode=mode)}
            assert "src/session.py" not in paths, (
                f"{mode} still returns a deleted file — a hit pointing at "
                "content that no longer exists is worse than no hit"
            )

    def test_edited_file_updates_in_both_arms(
        self, index: HybridIndex, repo: Root
    ) -> None:
        (repo.path / "src" / "auth.py").write_text(
            "def brand_new_symbol():\n    # entirely different content\n    pass\n"
        )
        index.reindex()

        for mode in (SearchMode.LEXICAL, SearchMode.HYBRID):
            assert index.search("brand_new_symbol", mode=mode), (
                f"{mode} missed the edit"
            )

    def test_new_file_appears_in_both_arms(
        self, index: HybridIndex, repo: Root
    ) -> None:
        (repo.path / "src" / "added.py").write_text(
            "def freshly_added():\n    # a brand new function\n    pass\n"
        )
        index.reindex()

        for mode in (SearchMode.LEXICAL, SearchMode.HYBRID):
            assert index.search("freshly_added", mode=mode), (
                f"{mode} missed the new file"
            )

    def test_reindex_is_idempotent(self, index: HybridIndex) -> None:
        """A second reindex with no changes must not duplicate vectors."""
        before = len(index.search("validate_token", mode=SearchMode.SEMANTIC))
        index.reindex()
        after = len(index.search("validate_token", mode=SearchMode.SEMANTIC))

        assert before == after


@requires_vec
class TestSemanticBackfill:
    """Enabling the semantic arm on an already-indexed root.

    A real-repository regression. Every fixture elsewhere builds both arms
    from empty, so none of them exercises this path — and it fails silently:
    hybrid degrades to lexical, searches keep working, and the semantic arm
    contributes nothing forever with no error.
    """

    def test_semantic_backfills_onto_an_existing_lexical_index(
        self, repo: Root
    ) -> None:
        # First: lexical only, as an existing installation would be.
        with HybridIndex(repo) as lexical_only:
            lexical_only.reindex()
            assert not lexical_only.has_semantic

        # Then the operator installs the extra and restarts.
        with HybridIndex(repo, embedder=HashingEmbedder()) as hybrid:
            assert hybrid.semantic_needs_backfill(), (
                "an empty semantic arm beside a populated lexical one must be "
                "detected — the lexical arm reports no changes, so nothing "
                "else would trigger the build"
            )
            hybrid.reindex()

            assert not hybrid.semantic_needs_backfill()
            assert hybrid.search("validate_token", mode=SearchMode.SEMANTIC), (
                "the semantic arm stayed empty — searches would keep working "
                "while silently contributing nothing"
            )

    def test_backfill_is_not_triggered_on_a_healthy_index(self, repo: Root) -> None:
        with HybridIndex(repo, embedder=HashingEmbedder()) as idx:
            idx.reindex()
            assert not idx.semantic_needs_backfill()

    def test_backfill_flag_is_false_without_a_semantic_arm(self, repo: Root) -> None:
        with HybridIndex(repo) as idx:
            idx.reindex()
            assert not idx.semantic_needs_backfill()
