"""Fetching the embedding model.

Separated from `embedding.py` so that module never acquires a network import.
That separation is the enforcement of a rule worth stating plainly:

**A search must never trigger a download.** A ~90 MB fetch on someone's first
query happens at the worst possible moment — mid-task, with no warning, on
whatever connection they have. So obtaining the model is an explicit act
(`forge-search warm`), and `OnnxEmbedder` refuses to construct without it
already on disk.

Only the standard library is used here. Adding an HTTP client dependency for
two file downloads would push a transitive requirement onto every install,
including the lexical-only ones that will never call this.
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .cache import cache_root

__all__ = ["ModelSpec", "DEFAULT_MODEL", "model_dir", "is_present", "download"]


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A downloadable embedding model."""

    name: str
    dim: int
    files: dict[str, str]  # local filename -> source URL

    #: Some retrieval models (E5, BGE) are trained with explicit role prefixes
    #: and lose accuracy without them. Silently omitting a required prefix is
    #: the kind of defect that shows up as mediocre results rather than an
    #: error, so it is part of the model's identity here rather than something
    #: a caller must remember.
    query_prefix: str = ""
    doc_prefix: str = ""

    @property
    def slug(self) -> str:
        return self.name.replace("/", "--")


def _hf(
    repo: str, dim: int, *, query_prefix: str = "", doc_prefix: str = ""
) -> ModelSpec:
    """A HuggingFace sentence-transformer with an ONNX export."""
    base = f"https://huggingface.co/{repo}/resolve/main"
    return ModelSpec(
        name=repo,
        dim=dim,
        files={
            "model.onnx": f"{base}/onnx/model.onnx",
            "tokenizer.json": f"{base}/tokenizer.json",
        },
        query_prefix=query_prefix,
        doc_prefix=doc_prefix,
    )


#: Models are swappable because model choice is an open question here, not a
#: settled one — and because the only way to answer it is to run the same
#: benchmark against each.
#:
#: The distinction that matters: `all-MiniLM-L6-v2` is trained for SYMMETRIC
#: similarity ("are these two sentences alike"). Retrieval is ASYMMETRIC — a
#: short query against a longer passage. `multi-qa-MiniLM-L6-cos-v1` is the
#: same architecture and size trained on question/answer pairs for exactly
#: that. Whether it wins on this corpus is measurable, not assumable.
MODELS: dict[str, ModelSpec] = {
    # Symmetric sentence-similarity. The best-known model, and the one this
    # project started with — which was a popularity choice, not a fit choice.
    "all-minilm": _hf("sentence-transformers/all-MiniLM-L6-v2", 384),
    # Asymmetric, trained for semantic search over question/passage pairs.
    "multi-qa-minilm": _hf("sentence-transformers/multi-qa-MiniLM-L6-cos-v1", 384),
}

DEFAULT_MODEL = MODELS["all-minilm"]


def resolve_model(name: str | None) -> ModelSpec:
    """Look up a model by short name."""
    if not name:
        return DEFAULT_MODEL
    if name not in MODELS:
        known = ", ".join(sorted(MODELS))
        raise KeyError(f"unknown model {name!r}; known: {known}")
    return MODELS[name]


def model_dir(spec: ModelSpec = DEFAULT_MODEL) -> Path:
    """Where this model lives. Beside the indexes, in the OS cache."""
    return cache_root() / "models" / spec.slug


def is_present(spec: ModelSpec = DEFAULT_MODEL) -> bool:
    """Whether every file the model needs is already on disk."""
    directory = model_dir(spec)
    return all((directory / name).is_file() for name in spec.files)


def download(
    spec: ModelSpec = DEFAULT_MODEL, *, force: bool = False, progress=print
) -> Path:
    """Fetch the model, returning its directory.

    Each file downloads to a temporary path and is moved into place only on
    success. A partial file left behind by an interrupted download would look
    present to `is_present()`, and `OnnxEmbedder` would then fail on a
    truncated model with an error pointing at the wrong thing entirely.
    """
    directory = model_dir(spec)
    directory.mkdir(parents=True, exist_ok=True)

    if is_present(spec) and not force:
        progress(f"{spec.name}: already present in {directory}")
        return directory

    for filename, url in spec.files.items():
        target = directory / filename
        if target.is_file() and not force:
            progress(f"  {filename}: present")
            continue

        progress(f"  {filename}: downloading…")
        try:
            with urllib.request.urlopen(url) as response:  # noqa: S310 - fixed https URL
                with tempfile.NamedTemporaryFile(
                    dir=directory, delete=False, suffix=".partial"
                ) as tmp:
                    shutil.copyfileobj(response, tmp)
                    partial = Path(tmp.name)
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"could not download {filename} from {url}: {exc}. "
                "forge-search works without the semantic arm — the lexical "
                "index needs no model."
            ) from exc

        partial.replace(target)
        size_mb = target.stat().st_size / 1_000_000
        progress(f"  {filename}: {size_mb:.1f} MB")

    return directory
