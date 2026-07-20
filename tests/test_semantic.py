"""The semantic arm.

Runs against `HashingEmbedder`, so no model download is needed. That embedder
is real (hashed features, L2-normalised) but captures lexical overlap rather
than meaning — enough to exercise storage, retrieval, and fusion, not enough
to claim anything about semantic quality. Quality is `bench`'s job, against a
real model, and is deliberately not asserted here.

Every test skips cleanly when sqlite-vec is unavailable, because a
lexical-only installation is a supported configuration rather than a broken
one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.core.embedding import (
    DEFAULT_DIM,
    HashingEmbedder,
    deserialize,
    serialize,
)
from forge_search.core.semantic import (
    CHUNK_LINES,
    CHUNK_OVERLAP,
    SemanticIndex,
    breadcrumb,
    chunk_text,
    summary_chunk,
    vec_available,
)

requires_vec = pytest.mark.skipif(
    not vec_available(), reason="sqlite-vec not installed (lexical-only install)"
)


class TestChunking:
    def test_chunks_overlap(self) -> None:
        """Overlap keeps a boundary from severing the context that makes a
        passage findable."""
        text = "\n".join(f"line {i}" for i in range(40))
        starts = [c.start_line for c in chunk_text(text)]

        assert starts[1] - starts[0] == CHUNK_LINES - CHUNK_OVERLAP
        assert starts[0] == 1

    def test_chunks_are_coarser_than_lines(self) -> None:
        """The lexical arm indexes lines; embedding a single line is mostly
        noise, so these windows must be meaningfully larger."""
        text = "\n".join(f"line {i}" for i in range(40))
        chunks = list(chunk_text(text))

        assert all(c.text.count("\n") >= 1 for c in chunks)
        assert len(chunks) < 40

    def test_blank_only_chunks_are_skipped(self) -> None:
        """A whitespace vector sits near the origin and weakly matches
        everything — worse than not indexing it."""
        assert list(chunk_text("\n\n\n   \n\n")) == []

    def test_short_text_yields_one_chunk(self) -> None:
        chunks = list(chunk_text("just one line"))
        assert len(chunks) == 1
        assert chunks[0].start_line == 1

    def test_empty_text_yields_nothing(self) -> None:
        assert list(chunk_text("")) == []

    def test_overlap_must_be_smaller_than_size(self) -> None:
        with pytest.raises(ValueError):
            list(chunk_text("a\nb\nc", size=4, overlap=4))


class TestHashingEmbedder:
    def test_dimension_is_respected(self) -> None:
        vectors = HashingEmbedder(dim=64).embed(["hello world"])
        assert len(vectors[0]) == 64

    def test_deterministic(self) -> None:
        """Identical text must embed identically, or nothing downstream is
        reproducible."""
        e = HashingEmbedder()
        assert e.embed(["same text"]) == e.embed(["same text"])

    def test_vectors_are_normalised(self) -> None:
        vector = HashingEmbedder().embed(["some words here"])[0]
        norm = sum(v * v for v in vector) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_different_text_gives_different_vectors(self) -> None:
        e = HashingEmbedder()
        assert e.embed(["alpha beta"])[0] != e.embed(["gamma delta"])[0]

    def test_empty_input_is_empty_output(self) -> None:
        assert HashingEmbedder().embed([]) == []

    def test_empty_string_does_not_divide_by_zero(self) -> None:
        assert HashingEmbedder().embed([""])[0] == [0.0] * DEFAULT_DIM


class TestSerialisation:
    def test_round_trips(self) -> None:
        original = [0.5, -0.25, 0.125]
        assert deserialize(serialize(original)) == pytest.approx(original)

    def test_packs_as_float32(self) -> None:
        assert len(serialize([0.0] * 384)) == 384 * 4


@requires_vec
class TestSemanticIndex:
    @pytest.fixture
    def index(self, tmp_path: Path):
        with SemanticIndex(tmp_path / "idx.db", HashingEmbedder()) as idx:
            yield idx

    def test_indexes_and_retrieves(self, index: SemanticIndex) -> None:
        index.index_file(
            "auth.py", "def validate_token(token):\n    return decode(token)\n"
        )
        index.commit()

        hits = index.search("validate_token", limit=5)

        assert hits
        assert hits[0][0] == "auth.py"

    def test_results_are_ordered_by_distance(self, index: SemanticIndex) -> None:
        for name in ("a.py", "b.py", "c.py"):
            index.index_file(name, f"def {name[0]}_function():\n    pass\n")
        index.commit()

        distances = [d for _, _, d in index.search("function", limit=5)]

        assert distances == sorted(distances)

    def test_line_numbers_point_into_the_file(self, index: SemanticIndex) -> None:
        body = "\n".join(f"line {i}" for i in range(60))
        index.index_file("big.md", body)
        index.commit()

        for _, line, _ in index.search("line 40", limit=5):
            assert 1 <= line <= 60

    def test_drop_file_removes_its_vectors(self, index: SemanticIndex) -> None:
        """A hit pointing at a deleted file is worse than no hit."""
        index.index_file("gone.py", "def temporary():\n    pass\n")
        index.commit()
        assert index.count() > 0

        index.drop_file("gone.py")
        index.commit()

        assert index.count() == 0
        assert index.search("temporary", limit=5) == []

    def test_drop_file_leaves_other_files_alone(self, index: SemanticIndex) -> None:
        index.index_file("keep.py", "def kept():\n    pass\n")
        index.index_file("drop.py", "def dropped():\n    pass\n")
        index.commit()

        index.drop_file("drop.py")
        index.commit()

        assert {relpath for relpath, _, _ in index.search("def", limit=10)} == {"keep.py"}

    def test_empty_file_indexes_nothing(self, index: SemanticIndex) -> None:
        assert index.index_file("empty.py", "") == 0

    def test_index_persists_across_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "persist.db"
        with SemanticIndex(db, HashingEmbedder()) as idx:
            idx.index_file("a.py", "def hello():\n    pass\n")
            idx.commit()

        with SemanticIndex(db, HashingEmbedder()) as reopened:
            assert reopened.count() > 0
            assert reopened.search("hello", limit=5)


class TestGracefulAbsence:
    def test_vec_available_is_a_bool_not_a_crash(self) -> None:
        """A lexical-only install is supported, so this must answer rather
        than raise — including on a Python whose sqlite3 lacks extension
        loading entirely."""
        assert isinstance(vec_available(), bool)


class TestSummaryChunk:
    """Per-file summary chunks — the fix that took semantic recall 0% -> 50%.

    Sliding windows alone buried each file's description inside surrounding
    boilerplate, and mean-pooling averaged the signal away. Measured on the
    real corpus: header alone 0.484, the window containing it 0.368, path +
    header 0.520.
    """

    def test_harvests_the_leading_comment_block(self) -> None:
        text = (
            "#!/bin/bash\n"
            "# Scans staged files for hardcoded secrets before git commit.\n"
            "# Blocks the commit if secrets are detected.\n"
            "\n"
            "INPUT=$(cat)\n"
        )
        chunk = summary_chunk("hooks/check-secrets.sh", text)

        assert chunk is not None
        assert "Scans staged files for hardcoded secrets" in chunk.text
        assert "Blocks the commit" in chunk.text

    def test_includes_the_path(self) -> None:
        """The path carries the filename, which is often a file's single most
        descriptive token — and it measured highest of the three variants."""
        chunk = summary_chunk("hooks/check-secrets.sh", "# Does a thing.\n")
        assert chunk is not None and "hooks/check-secrets.sh" in chunk.text

    def test_excludes_code_and_boilerplate(self) -> None:
        """The whole point: code lines are what diluted the signal."""
        text = "# Real description here.\n\nINPUT=$(cat)\njq -r '.tool_input'\n"
        chunk = summary_chunk("f.sh", text)

        assert chunk is not None
        assert "INPUT=" not in chunk.text
        assert "jq" not in chunk.text

    def test_shebang_is_dropped(self) -> None:
        chunk = summary_chunk("f.sh", "#!/bin/bash\n# The description.\n")
        assert chunk is not None and "/bin/bash" not in chunk.text

    def test_none_when_there_is_no_header(self) -> None:
        """A summary of nothing but a path is noise, not signal."""
        assert summary_chunk("f.py", "x = 1\ny = 2\n") is None

    def test_handles_slash_comments(self) -> None:
        chunk = summary_chunk("a.ts", "// Derives gate config.\nconst x = 1;\n")
        assert chunk is not None and "Derives gate config" in chunk.text

    def test_stops_at_the_first_blank_after_content(self) -> None:
        text = "# Header line.\n\n# A much later unrelated comment.\nx = 1\n"
        chunk = summary_chunk("f.py", text)

        assert chunk is not None
        assert "Header line" in chunk.text
        assert "unrelated" not in chunk.text


@requires_vec
class TestSummaryChunkIsIndexed:
    def test_summary_is_stored_alongside_windows(self, tmp_path: Path) -> None:
        with SemanticIndex(tmp_path / "idx.db", HashingEmbedder()) as idx:
            body = "# Blocks commits containing secrets.\n" + "\n".join(
                f"line {i}" for i in range(30)
            )
            written = idx.index_file("check-secrets.sh", body)
            idx.commit()

            # windows + one summary
            assert written == len(list(chunk_text(body))) + 1
            assert idx.search("Blocks commits containing secrets", limit=3)


class TestBreadcrumb:
    """Per-chunk location context — the deterministic form of contextual
    retrieval, and the research's highest-ROI recommendation.

    A 12-line window lifted out of a file is frequently unidentifiable. The
    breadcrumb tells the embedder which file and which region a fragment
    belongs to, which is the same mechanism that took this corpus from 0% to
    50% when applied to one chunk per file.
    """

    def test_finds_the_enclosing_markdown_heading(self) -> None:
        lines = ["# Overview", "prose", "## Merge gates", "more prose"]
        assert breadcrumb("docs/guide.md", lines, 4) == "docs/guide.md > Merge gates"

    def test_finds_the_enclosing_python_function(self) -> None:
        lines = ["import os", "", "def validate_token(t):", "    return t"]
        assert breadcrumb("src/auth.py", lines, 4) == "src/auth.py > validate_token"

    def test_finds_the_enclosing_shell_function(self) -> None:
        lines = ["#!/bin/bash", "check_secrets() {", "  grep -r key .", "}"]
        assert breadcrumb("hooks/h.sh", lines, 3) == "hooks/h.sh > check_secrets"

    def test_finds_the_enclosing_ts_function(self) -> None:
        lines = ["import x;", "export function deriveGates(cfg) {", "  return cfg;"]
        assert breadcrumb("src/a.ts", lines, 3) == "src/a.ts > deriveGates"

    def test_takes_the_nearest_preceding_label_not_the_first(self) -> None:
        """A chunk belongs to the region it sits in, not the file's first one."""
        lines = ["def first():", "    pass", "", "def second():", "    pass"]
        assert breadcrumb("m.py", lines, 5) == "m.py > second"

    def test_falls_back_to_the_path_alone(self) -> None:
        """Plain prose has no structure to point at — the path still locates it."""
        assert breadcrumb("notes.txt", ["just text", "more"], 2) == "notes.txt"

    def test_chunks_carry_their_breadcrumb(self) -> None:
        text = "\n".join(["# Setup", "a", "b", "## Usage", "c", "d"])
        chunks = list(chunk_text(text, size=3, overlap=1, relpath="README.md"))

        assert all(c.text.startswith("README.md") for c in chunks)
        assert any("Usage" in c.text.splitlines()[0] for c in chunks)

    def test_omitting_relpath_leaves_chunks_unprefixed(self) -> None:
        """Backwards compatible: callers that do not pass a path get the raw
        window, so the breadcrumb cannot silently corrupt other callers."""
        chunks = list(chunk_text("a\nb\nc\nd", size=2, overlap=0))
        assert not any(c.text.startswith("/") for c in chunks)
        assert chunks[0].text == "a\nb"
