"""Index behaviour, with emphasis on staleness.

The staleness tests carry the most weight here. A stale index is the same
failure shape as the traversal defects — plausible results, no error, nothing
that announces the problem — so "does it notice its own staleness" is the
property most worth pinning down.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from forge_search.core.cache import index_path_for
from forge_search.core.index import LexicalIndex
from forge_search.core.roots import Root


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real user cache from tests."""
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def repo(tmp_path: Path) -> Root:
    src = tmp_path / "repo"
    (src / "src").mkdir(parents=True)
    (src / "src" / "auth.py").write_text(
        "def validate_token(token):\n"
        "    # verify the signature\n"
        "    return decode(token)\n"
    )
    (src / "README.md").write_text("# Demo\n\nUses validate_token for auth.\n")
    return Root(label="repo", path=src)


class TestIndexing:
    def test_indexes_and_finds(self, repo: Root) -> None:
        with LexicalIndex(repo) as idx:
            stats = idx.reindex()
            assert stats.files_indexed == 2

            hits = idx.search("validate_token")

        assert {h.path for h in hits} == {"src/auth.py", "README.md"}
        assert all(h.project == "repo" for h in hits)

    def test_paths_are_relative_never_absolute(self, repo: Root) -> None:
        """Absolute paths leak machine layout and break when the tree moves."""
        with LexicalIndex(repo) as idx:
            idx.reindex()
            hits = idx.search("validate_token")

        assert hits
        for hit in hits:
            assert not os.path.isabs(hit.path), f"absolute path leaked: {hit.path}"
            assert str(repo.path) not in hit.path

    def test_second_reindex_is_a_noop(self, repo: Root) -> None:
        with LexicalIndex(repo) as idx:
            idx.reindex()
            second = idx.reindex()

        assert second.up_to_date
        assert second.files_indexed == 0

    def test_index_lives_outside_the_searched_tree(self, repo: Root) -> None:
        """An index inside the tree would be indexed by the next run."""
        db = index_path_for(repo.label, repo.path)
        assert repo.path not in db.parents

    def test_roots_with_same_label_do_not_collide(self, tmp_path: Path) -> None:
        a = Root(label="docs", path=tmp_path / "a")
        b = Root(label="docs", path=tmp_path / "b")
        assert index_path_for(a.label, a.path) != index_path_for(b.label, b.path)


class TestStaleness:
    """The index must notice when it disagrees with disk."""

    def test_fresh_index_is_not_stale(self, repo: Root) -> None:
        with LexicalIndex(repo) as idx:
            idx.reindex()
            assert not idx.is_stale()

    def test_modified_file_marks_stale(self, repo: Root) -> None:
        with LexicalIndex(repo) as idx:
            idx.reindex()
            (repo.path / "src" / "auth.py").write_text("def totally_different():\n    pass\n")
            assert idx.is_stale(), "a changed file must be detected, or searches lie"

    def test_new_file_marks_stale(self, repo: Root) -> None:
        with LexicalIndex(repo) as idx:
            idx.reindex()
            (repo.path / "src" / "new.py").write_text("x = 1\n")
            assert idx.is_stale()

    def test_deleted_file_marks_stale(self, repo: Root) -> None:
        with LexicalIndex(repo) as idx:
            idx.reindex()
            (repo.path / "README.md").unlink()
            assert idx.is_stale()

    def test_reindex_drops_deleted_files_from_results(self, repo: Root) -> None:
        """Deleting a file must remove its hits — a result pointing at a file
        that no longer exists is worse than no result at all."""
        with LexicalIndex(repo) as idx:
            idx.reindex()
            (repo.path / "README.md").unlink()
            stats = idx.reindex()

            assert stats.files_removed == 1
            assert {h.path for h in idx.search("validate_token")} == {"src/auth.py"}

    def test_reindex_picks_up_edits(self, repo: Root) -> None:
        with LexicalIndex(repo) as idx:
            idx.reindex()
            (repo.path / "src" / "auth.py").write_text("def refresh_session():\n    pass\n")
            idx.reindex()

            assert idx.search("refresh_session")
            assert not [h for h in idx.search("validate_token") if h.path == "src/auth.py"]


class TestQuerying:
    def test_malformed_query_returns_empty_not_raises(self, repo: Root) -> None:
        """An unparseable FTS expression must not crash the server."""
        with LexicalIndex(repo) as idx:
            idx.reindex()
            assert idx.search('unbalanced "quote') == []

    def test_limit_is_respected(self, tmp_path: Path) -> None:
        src = tmp_path / "many"
        src.mkdir()
        (src / "f.py").write_text("\n".join(f"needle line {i}" for i in range(50)))

        with LexicalIndex(Root(label="many", path=src)) as idx:
            idx.reindex()
            assert len(idx.search("needle", limit=5)) == 5

    def test_higher_score_is_better(self, repo: Root) -> None:
        """bm25() is lower-is-better; we invert it, so results must be
        descending. A caller sorting by score should not get them backwards."""
        with LexicalIndex(repo) as idx:
            idx.reindex()
            scores = [h.score for h in idx.search("validate_token")]

        assert scores == sorted(scores, reverse=True)

    def test_excerpt_is_bounded_on_a_long_line(self, tmp_path: Path) -> None:
        """A very long line must still yield a bounded excerpt containing the
        match — the truncation defect, exercised end-to-end through the index
        rather than against `excerpt()` directly."""
        src = tmp_path / "long"
        src.mkdir()
        filler = " ".join("word" for _ in range(200))
        (src / "f.py").write_text(f"x = '{filler} NEEDLE {filler}'\n")

        with LexicalIndex(Root(label="long", path=src)) as idx:
            idx.reindex()
            hits = idx.search("NEEDLE")

        assert hits
        assert "NEEDLE" in hits[0].excerpt, "excerpt dropped the match"
        assert len(hits[0].excerpt) <= 152

    def test_substring_of_an_unbroken_token_is_not_found(self, tmp_path: Path) -> None:
        """Documents a real limitation rather than pretending it away.

        FTS5's `unicode61` tokenizer splits on non-alphanumerics, so a long
        unbroken run of characters — minified JS, a base64 blob, a very long
        identifier — is ONE token. Searching for a substring inside it matches
        nothing, because the index stores tokens, not character positions.

        This is correct token-based behaviour, not a bug, and it is the right
        trade for the observed workload (identifier and filename lookups).
        Substring search over such content would need a trigram index, which
        is a separate table and a real disk cost. If minified assets or
        embedded blobs ever need to be searchable, that is the change — and
        this test should start failing when it lands.
        """
        src = tmp_path / "minified"
        src.mkdir()
        (src / "bundle.js").write_text("var a=" + ("z" * 400) + "NEEDLE" + ("z" * 400) + ";\n")

        with LexicalIndex(Root(label="minified", path=src)) as idx:
            idx.reindex()
            assert idx.search("NEEDLE") == []
