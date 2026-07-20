"""RRF fusion.

The degradation tests carry the most weight. The semantic arm ships on a
parity argument rather than a measured one, so "what happens when it
contributes nothing" is the case most likely to occur in practice — and it
must produce the lexical ranking exactly, not a perturbed version of it.
"""

from __future__ import annotations

import pytest

from forge_search.core.fusion import RRF_K, ArmWeights, fuse


class TestDegradation:
    """Behaviour when one arm is absent or disabled."""

    def test_empty_semantic_arm_yields_exactly_the_lexical_order(self) -> None:
        lexical = ["a", "b", "c", "d"]

        fused = [key for key, _ in fuse(lexical=lexical, semantic=[])]

        assert fused == lexical, (
            "with no semantic results the fused order must be identical to "
            "lexical — a distorted ranking would be worse than no fusion"
        )

    def test_zero_weighted_semantic_arm_is_ignored_entirely(self) -> None:
        """The escape hatch: if measurement shows the arm earns nothing, it can
        be switched off by configuration rather than by a release."""
        lexical = ["a", "b", "c"]
        semantic = ["c", "b", "a"]

        fused = [
            key
            for key, _ in fuse(
                lexical=lexical,
                semantic=semantic,
                weights=ArmWeights(lexical=1.0, semantic=0.0),
            )
        ]

        assert fused == lexical

    def test_empty_lexical_arm_yields_the_semantic_order(self) -> None:
        semantic = ["x", "y", "z"]
        fused = [key for key, _ in fuse(lexical=[], semantic=semantic)]
        assert fused == semantic

    def test_both_empty_is_empty_not_an_error(self) -> None:
        assert fuse(lexical=[], semantic=[]) == []


class TestFusion:
    def test_agreement_ranks_first(self) -> None:
        """A document both arms rank highly should outrank one only a single
        arm likes — the entire point of fusing."""
        fused = [
            key
            for key, _ in fuse(lexical=["shared", "lex"], semantic=["shared", "sem"])
        ]
        assert fused[0] == "shared"

    def test_documents_unique_to_one_arm_still_appear(self) -> None:
        fused = {key for key, _ in fuse(lexical=["a"], semantic=["b"])}
        assert fused == {"a", "b"}

    def test_higher_lexical_weight_favours_lexical_disagreement(self) -> None:
        fused = [
            key
            for key, _ in fuse(
                lexical=["lex"],
                semantic=["sem"],
                weights=ArmWeights(lexical=1.0, semantic=0.1),
            )
        ]
        assert fused[0] == "lex"

    def test_higher_semantic_weight_favours_semantic_disagreement(self) -> None:
        fused = [
            key
            for key, _ in fuse(
                lexical=["lex"],
                semantic=["sem"],
                weights=ArmWeights(lexical=0.1, semantic=1.0),
            )
        ]
        assert fused[0] == "sem"

    def test_ties_break_toward_the_lexical_arm(self) -> None:
        """Equal weights and mirrored orders make scores tie; the arm with
        measured support should win the coin flip."""
        fused = [
            key
            for key, _ in fuse(
                lexical=["a", "b"],
                semantic=["b", "a"],
                weights=ArmWeights(lexical=1.0, semantic=1.0),
            )
        ]
        assert fused == ["a", "b"]

    def test_scores_are_descending(self) -> None:
        scores = [s for _, s in fuse(lexical=["a", "b", "c"], semantic=["c", "a", "b"])]
        assert scores == sorted(scores, reverse=True)

    def test_limit_truncates(self) -> None:
        fused = fuse(lexical=["a", "b", "c", "d"], semantic=[], limit=2)
        assert len(fused) == 2

    def test_k_damps_top_rank_dominance(self) -> None:
        """A small k makes rank 1 dominate; the default should not."""
        big_k = fuse(lexical=["a"], semantic=["b"], k=RRF_K)
        tiny_k = fuse(lexical=["a"], semantic=["b"], k=1)

        spread_big = abs(big_k[0][1] - big_k[1][1])
        spread_tiny = abs(tiny_k[0][1] - tiny_k[1][1])

        assert spread_big < spread_tiny


class TestWeightValidation:
    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError):
            ArmWeights(lexical=-1.0)

    def test_both_arms_zero_rejected(self) -> None:
        """Disabling both arms means every search returns nothing — a
        configuration mistake that would otherwise fail silently."""
        with pytest.raises(ValueError):
            ArmWeights(lexical=0.0, semantic=0.0)
