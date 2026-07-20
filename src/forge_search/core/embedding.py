"""Embeddings for the semantic arm.

`Embedder` is a protocol, not a class to inherit. Three reasons, and the third
is the important one:

1. Tests need a deterministic embedder with no model download.
2. The model choice is genuinely replaceable — nothing else depends on which
   one is used, only on the dimension.
3. **The semantic arm ships on a parity argument, not a measured one.** The
   workload this was built for showed *zero* conceptual queries, and short
   keyword lookups are where embedding models perform worst. So the arm is
   built to be measurable and removable: swap the embedder, weight it to zero,
   or leave it uninstalled entirely, without touching anything else.

Nothing here downloads on import. Fetching a ~90 MB model is an explicit act
(`forge-search warm`), never a side effect of a search.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "Embedder",
    "HashingEmbedder",
    "OnnxEmbedder",
    "DEFAULT_DIM",
    "serialize",
    "deserialize",
]

#: MiniLM-class output dimension. Fixed because it is written into the vec0
#: table declaration — changing it invalidates every existing index.
DEFAULT_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    """Turns text into a fixed-length float vector."""

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def serialize(vector: list[float]) -> bytes:
    """Pack a vector for sqlite-vec, which stores raw float32."""
    return struct.pack(f"{len(vector)}f", *vector)


def deserialize(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


class HashingEmbedder:
    """A deterministic embedder with no model and no download.

    Not a stub — it is a real, if crude, embedding: hashed token features
    projected into a fixed-dimension space and L2-normalised, so cosine
    similarity behaves sensibly and identical text always produces identical
    vectors.

    It exists so the semantic *plumbing* — storage, retrieval, fusion — can be
    tested and exercised without a 90 MB dependency. It is emphatically not a
    substitute for a real model on real queries: it captures lexical overlap,
    not meaning, so two ways of saying the same thing will not match. That is
    exactly the property a real embedder adds, and exactly what `bench` has to
    measure before the semantic arm is worth its cost.
    """

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _tokenize(text):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dim
            # Sign from a separate byte so collisions can cancel rather than
            # always reinforcing, which would flatten the space.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        norm = sum(v * v for v in vector) ** 0.5
        if norm == 0.0:
            return vector
        return [v / norm for v in vector]


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "_":
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


class OnnxEmbedder:
    """A real sentence-transformer via ONNX Runtime, CPU, fully offline.

    Constructing this requires the model already on disk. It never downloads:
    a search must not silently trigger a 90 MB fetch, and a fetch that happens
    on someone's first query is a fetch that happens at the worst moment.
    Use `forge-search warm` to obtain it.
    """

    def __init__(self, model_dir: Path, dim: int = DEFAULT_DIM) -> None:
        self.model_dir = Path(model_dir)
        self._dim = dim
        self._session = None
        self._tokenizer = None

        model_file = self.model_dir / "model.onnx"
        tokenizer_file = self.model_dir / "tokenizer.json"
        missing = [p.name for p in (model_file, tokenizer_file) if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                f"embedding model incomplete in {self.model_dir} (missing: "
                f"{', '.join(missing)}). Run `forge-search warm` to fetch it."
            )

        import onnxruntime  # imported lazily: optional dependency
        from tokenizers import Tokenizer

        self._session = onnxruntime.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self._tokenizer.enable_truncation(max_length=256)
        self._tokenizer.enable_padding()

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        assert self._tokenizer is not None and self._session is not None

        encoded = self._tokenizer.encode_batch(texts)
        input_ids = [e.ids for e in encoded]
        attention = [e.attention_mask for e in encoded]

        import numpy as np

        feeds = {
            "input_ids": np.array(input_ids, dtype=np.int64),
            "attention_mask": np.array(attention, dtype=np.int64),
        }
        # Some exports also expect token_type_ids; supply zeros when the graph
        # asks for it rather than failing on a model that is otherwise fine.
        expected = {i.name for i in self._session.get_inputs()}
        if "token_type_ids" in expected:
            feeds["token_type_ids"] = np.zeros_like(feeds["input_ids"])

        hidden = self._session.run(None, feeds)[0]

        # Mean-pool over real tokens only — including padding would drag every
        # vector toward the pad embedding and blur short texts most.
        mask = np.array(attention, dtype=np.float32)[..., None]
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        return (pooled / norms).astype(float).tolist()
