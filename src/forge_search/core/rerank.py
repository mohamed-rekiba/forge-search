"""Cross-encoder reranking.

Retrieval and reranking answer different questions, and the difference is the
whole reason this stage exists.

A **bi-encoder** — the semantic arm — embeds the query and each document
*separately*, then compares vectors. That is what makes it fast: documents are
embedded once at index time and never again. It also means the model never
sees a query and a document together, so it judges relevance only through the
geometry of two independently-produced vectors.

A **cross-encoder** takes the pair jointly and outputs one relevance score. It
can attend across both, so it judges far better — and it cannot be
precomputed, because the score depends on the query. That makes it useless for
searching a corpus and well suited to reordering a short list.

Reported gains are the largest of any single stage in the retrieval literature
— roughly a third of the *remaining* failures after hybrid search. The cost
here is small for a reason worth stating: **reranking cost scales with
candidates scored, not corpus size.** Scoring 30 short candidates on CPU is
the same work whether the index holds 700 files or 700,000.

Optional, like the semantic arm. Absent, results keep their fused order.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["Reranker", "CrossEncoderReranker", "rerank"]

#: How many candidates to rerank. Beyond this the latency stops being free and
#: the marginal candidate is unlikely to be the answer — first-stage retrieval
#: is not *that* bad.
DEFAULT_RERANK_DEPTH = 30


@runtime_checkable
class Reranker(Protocol):
    """Scores how well each document answers a query. Higher is better."""

    def score(self, query: str, documents: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    """A cross-encoder via ONNX Runtime, CPU, offline.

    Never downloads on construction, for the same reason the embedder does not:
    a search must not silently trigger a model fetch, and a fetch on someone's
    first query happens at the worst possible moment.
    """

    def __init__(self, model_dir: Path, *, max_length: int = 256) -> None:
        self.model_dir = Path(model_dir)
        self._max_length = max_length

        model_file = self.model_dir / "model.onnx"
        tokenizer_file = self.model_dir / "tokenizer.json"
        missing = [p.name for p in (model_file, tokenizer_file) if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                f"reranker incomplete in {self.model_dir} (missing: "
                f"{', '.join(missing)}). Run `forge-search warm --reranker` to fetch it."
            )

        import onnxruntime
        from tokenizers import Tokenizer

        self._session = onnxruntime.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding()

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Relevance of each document to the query, higher is better."""
        if not documents:
            return []

        import numpy as np

        # The pair is encoded jointly — this is the entire point of a
        # cross-encoder, and what a bi-encoder structurally cannot do.
        encoded = self._tokenizer.encode_batch([(query, doc) for doc in documents])

        feeds: dict[str, object] = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array(
                [e.attention_mask for e in encoded], dtype=np.int64
            ),
        }
        expected = {i.name for i in self._session.get_inputs()}
        if "token_type_ids" in expected:
            feeds["token_type_ids"] = np.array(
                [e.type_ids for e in encoded], dtype=np.int64
            )

        logits = self._session.run(None, feeds)[0]
        # ms-marco cross-encoders emit a single relevance logit per pair.
        # Squeeze rather than assume shape: some exports emit (n, 1), some (n,).
        flat = np.asarray(logits).reshape(len(documents), -1)[:, 0]
        return [float(x) for x in flat]


def rerank(
    query: str,
    candidates: list[tuple[str, str]],
    reranker: Reranker | None,
    *,
    depth: int = DEFAULT_RERANK_DEPTH,
) -> list[tuple[str, float]] | None:
    """Reorder `(key, text)` candidates by relevance, best first.

    Returns None when there is nothing to do — no reranker, or nothing to
    reorder — so the caller can keep its existing order rather than being
    handed a silently unchanged list it cannot distinguish from a reranked one.

    Only the first `depth` candidates are scored. Anything beyond that keeps
    its original position *after* the reranked head, so deep results are never
    silently dropped — they are simply not promoted.
    """
    if reranker is None or not candidates:
        return None

    head = candidates[:depth]
    tail = candidates[depth:]

    scores = reranker.score(query, [text for _, text in head])
    ranked = sorted(zip((k for k, _ in head), scores, strict=True), key=lambda kv: -kv[1])

    # The tail keeps relative order and sits below everything reranked. Scores
    # here are placeholders, monotonically decreasing, and must not be compared
    # against cross-encoder logits — they are on different scales entirely.
    floor = ranked[-1][1] if ranked else 0.0
    ranked.extend((key, floor - 1.0 - i) for i, (key, _) in enumerate(tail))
    return ranked


def sigmoid(x: float) -> float:
    """Squash a relevance logit into 0..1, for display only.

    Cross-encoder logits are unbounded and not comparable across models, so a
    raw one is meaningless to a caller. Never use this to compare against
    BM25 or cosine scores — different scales, no shared meaning.
    """
    return 1.0 / (1.0 + math.exp(-x))
