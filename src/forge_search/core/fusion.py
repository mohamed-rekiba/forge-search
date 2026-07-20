"""Reciprocal Rank Fusion.

Combining a lexical arm with a semantic one means reconciling BM25 scores with
cosine similarities. Those live on different, unstable scales — BM25 depends on
corpus statistics that shift on every reindex, cosine does not — so normalising
them against each other requires per-corpus calibration that goes stale
silently. RRF sidesteps that by fusing **ranks**, which are comparable by
construction::

    score(d) = Σ  weight[arm] / (k + rank[arm](d))

Two properties make it the right choice here specifically, beyond the usual
argument:

**It degrades correctly.** If the semantic arm returns nothing — not
installed, model absent, index not built — the fused order is *exactly* the
lexical order, not a distorted version of it. Given that the semantic arm
ships on a parity argument rather than a measured one, a fusion whose failure
mode is "silently reverts to the thing that works" is worth more than a
marginally better-tuned alternative.

**Weights are configuration, not constants.** That is the hedge. If measurement
shows the semantic arm contributes nothing on a given corpus, it can be
weighted to zero without a release — and if it stays at zero, that is the
evidence for removing it rather than carrying it indefinitely.

``k`` is left at the literature default of 60 and deliberately **untuned**.
Tuning it against a corpus with zero conceptual queries would be fabricating
precision.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["RRF_K", "ArmWeights", "fuse"]

#: The standard RRF constant. Damps the difference between top ranks so a
#: single arm's confident first place cannot dominate outright.
RRF_K = 60


@dataclass(frozen=True, slots=True)
class ArmWeights:
    """Relative influence of each arm.

    Defaults favour lexical because that is what the evidence supports: every
    observed query in the workload this targets is an identifier or filename
    lookup, and those are precisely where embedding models underperform. The
    semantic arm is present, and deliberately not dominant, until a
    measurement says otherwise.
    """

    lexical: float = 1.0
    semantic: float = 0.5

    def __post_init__(self) -> None:
        if self.lexical < 0 or self.semantic < 0:
            raise ValueError("arm weights must be non-negative")
        if self.lexical == 0 and self.semantic == 0:
            raise ValueError("at least one arm must have a non-zero weight")


def fuse(
    *,
    lexical: Sequence[str],
    semantic: Sequence[str],
    weights: ArmWeights | None = None,
    k: int = RRF_K,
    limit: int | None = None,
) -> list[tuple[str, float]]:
    """Fuse two ranked ID lists into one, highest score first.

    Each sequence is ordered best-first; identity is the caller's key (here, a
    ``relpath:line`` string). Ties break on the lexical arm's ordering, since
    that is the arm with measured support.

    >>> fuse(lexical=["a", "b"], semantic=["b", "a"])[0][0]
    'a'
    """
    weights = weights or ArmWeights()
    scores: dict[str, float] = {}

    for arm, weight in ((lexical, weights.lexical), (semantic, weights.semantic)):
        if weight == 0:
            continue
        for rank, key in enumerate(arm, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)

    lexical_order = {key: i for i, key in enumerate(lexical)}
    ranked = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], lexical_order.get(kv[0], len(lexical_order))),
    )
    return ranked[:limit] if limit is not None else ranked
