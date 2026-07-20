"""Reranking behaviour.

The properties worth pinning are the ones whose failure is *silent*: a
reranker that quietly drops results, or one wired in so that it never
actually reorders anything.
"""

from __future__ import annotations

import pytest

from forge_search.core.hybrid import HybridIndex
from forge_search.core.rerank import rerank
from forge_search.core.roots import Root


class Reversing:
    """Scores in reverse input order, so any reordering is unmistakable."""

    def score(self, query: str, documents: list[str]) -> list[float]:
        return [float(i) for i in range(len(documents))]


class Counting:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def score(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, len(documents)))
        return [0.0] * len(documents)


def test_no_reranker_is_distinguishable_from_a_no_op():
    # None, not the input list: the caller must be able to tell "nothing to
    # do" from "reranked and coincidentally unchanged".
    assert rerank("q", [("a", "x")], None) is None
    assert rerank("q", [], Reversing()) is None


def test_reranker_reorders():
    out = rerank("q", [("a", "1"), ("b", "2"), ("c", "3")], Reversing())
    assert [k for k, _ in out] == ["c", "b", "a"]


def test_candidates_beyond_depth_are_kept_not_dropped():
    # Silently truncating would look exactly like a good rerank from outside.
    given = [(str(i), str(i)) for i in range(10)]
    out = rerank("q", given, Reversing(), depth=3)

    assert len(out) == 10
    assert {k for k, _ in out} == {k for k, _ in given}
    # Reranked head first, then the tail in its original relative order.
    assert [k for k, _ in out[:3]] == ["2", "1", "0"]
    assert [k for k, _ in out[3:]] == [str(i) for i in range(3, 10)]
    # Scores stay monotonically decreasing, so a caller sorting by score
    # cannot resurrect the tail above the reranked head.
    scores = [s for _, s in out]
    assert scores == sorted(scores, reverse=True)


def test_depth_bounds_the_work():
    counter = Counting()
    rerank("q", [(str(i), str(i)) for i in range(100)], counter, depth=5)
    assert counter.calls == [("q", 5)]


def test_hybrid_search_applies_the_reranker(tmp_path):
    (tmp_path / "alpha.md").write_text("token here\n")
    (tmp_path / "beta.md").write_text("token here too\n")
    root = Root(label="r", path=tmp_path)

    with HybridIndex(root) as plain:
        plain.reindex()
        before = [h.path for h in plain.search("token", limit=10)]

    with HybridIndex(root, reranker=Reversing()) as reranked:
        reranked.reindex()
        after = [h.path for h in reranked.search("token", limit=10)]

    assert len(before) == 2
    assert after == list(reversed(before))


def test_reranker_respects_limit(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.md").write_text("token\n")

    with HybridIndex(Root(label="r", path=tmp_path), reranker=Reversing()) as index:
        index.reindex()
        assert len(index.search("token", limit=2)) == 2


def test_missing_model_files_name_the_fix():
    from forge_search.core.rerank import CrossEncoderReranker

    with pytest.raises(FileNotFoundError, match="warm --reranker"):
        CrossEncoderReranker(model_dir=__file__ + "-does-not-exist")


def test_reranking_is_off_by_default(tmp_path):
    # It LOST on the measured corpus (hybrid 57.7% → 50.0%, 10× latency).
    # This test exists so the citation can never quietly turn it back on the
    # way a well-supported prior tends to — same guard as the model and
    # chunking defaults. Changing this line requires changing a measurement.
    with HybridIndex(Root(label="r", path=tmp_path)) as index:
        assert index.reranker is None


def test_reranking_sees_more_candidates_than_it_returns(tmp_path):
    # Reranking can only promote what it is shown. If fusion truncated to
    # `limit` first, the reranker would reorder ten results it cannot add to —
    # recall@10 unchanged by construction, full latency still paid. That bug
    # shipped once and was invisible: the numbers simply did not move.
    for i in range(40):
        (tmp_path / f"f{i:02d}.md").write_text("token\n")

    counter = Counting()
    with HybridIndex(Root(label="r", path=tmp_path), reranker=counter) as index:
        index.reindex()
        assert len(index.search("token", limit=5)) == 5

    assert counter.calls, "the reranker was never called"
    assert counter.calls[0][1] > 5, "reranked only what it was going to return"
