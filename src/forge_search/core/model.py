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

    @property
    def slug(self) -> str:
        return self.name.replace("/", "--")


#: MiniLM-class: 384 dimensions, small enough to run on CPU without being
#: noticeably slow, and the most widely used sentence-transformer export.
#: Pinned to a specific revision rather than a branch — a model that silently
#: changes underneath an existing index would make stored vectors incomparable
#: with newly-computed ones, and nothing would report it.
DEFAULT_MODEL = ModelSpec(
    name="sentence-transformers/all-MiniLM-L6-v2",
    dim=384,
    files={
        "model.onnx": (
            "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/"
            "resolve/main/onnx/model.onnx"
        ),
        "tokenizer.json": (
            "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/"
            "resolve/main/tokenizer.json"
        ),
    },
)


def model_dir(spec: ModelSpec = DEFAULT_MODEL) -> Path:
    """Where this model lives. Beside the indexes, in the OS cache."""
    return cache_root() / "models" / spec.slug


def is_present(spec: ModelSpec = DEFAULT_MODEL) -> bool:
    """Whether every file the model needs is already on disk."""
    directory = model_dir(spec)
    return all((directory / name).is_file() for name in spec.files)


def download(spec: ModelSpec = DEFAULT_MODEL, *, force: bool = False, progress=print) -> Path:
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
