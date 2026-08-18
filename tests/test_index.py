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
from forge_search.core.query import QueryError
from forge_search.core.roots import Root
from forge_search.core.traversal import DEFAULT_EXCLUDES


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

    def test_index_lives_in_the_tree_and_is_excluded_from_it(self, repo: Root) -> None:
        """In-tree by default, and never indexed by itself.

        This inverts an earlier decision. The index used to live in a shared
        cache specifically so it could not be indexed or committed; it moved
        in-tree because a cache keyed by a path hash orphans an index the
        moment a project is deleted or renamed — silently, and forever. The
        traversal exclusion is what makes the new default safe, so it is
        asserted here rather than left implied.
        """
        db = index_path_for(repo.label, repo.path)
        assert repo.path in db.parents
        assert ".forge_search" in DEFAULT_EXCLUDES

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
            (repo.path / "src" / "auth.py").write_text(
                "def totally_different():\n    pass\n"
            )
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
            (repo.path / "src" / "auth.py").write_text(
                "def refresh_session():\n    pass\n"
            )
            idx.reindex()

            assert idx.search("refresh_session")
            assert not [
                h for h in idx.search("validate_token") if h.path == "src/auth.py"
            ]


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
        (src / "bundle.js").write_text(
            "var a=" + ("z" * 400) + "NEEDLE" + ("z" * 400) + ";\n"
        )

        with LexicalIndex(Root(label="minified", path=src)) as idx:
            idx.reindex()
            assert idx.search("NEEDLE") == []


class TestPathMatching:
    """Filename lookup is a distinct capability from content search.

    Added after a benchmark on a real repository showed both
    `require-active-ticket.sh` and `block-unreviewed-merge.sh` unfindable by
    name: neither file contains its own filename, so a content-only index
    cannot reach them. Filename lookup was one of the dominant query shapes
    this tool targets, which made it a gap rather than a nicety.
    """

    def test_file_findable_by_name_it_never_contains(self, tmp_path: Path) -> None:
        src = tmp_path / "repo"
        (src / "hooks").mkdir(parents=True)
        # Deliberately never mentions its own name — the real case.
        (src / "hooks" / "require-active-ticket.sh").write_text(
            "#!/bin/bash\necho 'unrelated content'\n"
        )

        with LexicalIndex(Root(label="repo", path=src)) as idx:
            idx.reindex()
            hits = idx.search("require-active-ticket")

        assert any(h.path == "hooks/require-active-ticket.sh" for h in hits), (
            "a content-only index cannot find a file by a name it never "
            "contains — this is what the paths table exists for"
        )

    def test_path_match_outranks_incidental_content_mentions(
        self, tmp_path: Path
    ) -> None:
        """Someone searching a filename wants the file, not the fixtures that
        happen to reference it."""
        src = tmp_path / "repo"
        (src / "hooks").mkdir(parents=True)
        (src / "hooks" / "target-hook.sh").write_text("#!/bin/bash\necho hi\n")
        (src / "tests.md").write_text("\n".join(["mentions target-hook here"] * 20))

        with LexicalIndex(Root(label="repo", path=src)) as idx:
            idx.reindex()
            hits = idx.search("target-hook")

        assert hits[0].path == "hooks/target-hook.sh"

    def test_deleted_file_drops_out_of_path_matches(self, tmp_path: Path) -> None:
        src = tmp_path / "repo"
        src.mkdir()
        (src / "temporary-thing.py").write_text("x = 1\n")

        with LexicalIndex(Root(label="repo", path=src)) as idx:
            idx.reindex()
            assert idx.search("temporary-thing")

            (src / "temporary-thing.py").unlink()
            idx.reindex()

            assert not idx.search("temporary-thing")


class TestSchemaVersioning:
    """A schema change must not leave existing indexes silently incomplete.

    This shipped as a bug twice — the semantic arm and the paths table each
    left older indexes empty for the new feature, with searches still
    returning results and nothing reporting the gap.
    """

    def test_version_mismatch_forces_a_rebuild(self, tmp_path: Path) -> None:
        import sqlite3

        from forge_search.core.index import SCHEMA_VERSION

        src = tmp_path / "repo"
        src.mkdir()
        (src / "findme.py").write_text("def hello():\n    pass\n")
        root = Root(label="repo", path=src)

        with LexicalIndex(root) as idx:
            idx.reindex()
            db_path = idx.db_path
            assert idx.indexed_count() == 1

        # Simulate an index written by an older schema.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        conn.commit()
        conn.close()

        with LexicalIndex(root) as reopened:
            assert reopened.indexed_count() == 0, "stale index was not discarded"
            reopened.reindex()
            assert reopened.search("hello")

    def test_unstamped_index_is_rebuilt(self, tmp_path: Path) -> None:
        """An index with no version stamp predates versioning entirely — the
        exact case an earlier guard skipped, stamping the current version onto
        stale data and making the gap permanent."""
        import sqlite3

        src = tmp_path / "repo"
        src.mkdir()
        (src / "findme.py").write_text("def hello():\n    pass\n")
        root = Root(label="repo", path=src)

        with LexicalIndex(root) as idx:
            idx.reindex()
            db_path = idx.db_path

        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
        conn.commit()
        conn.close()

        with LexicalIndex(root) as reopened:
            assert reopened.indexed_count() == 0, (
                "an unstamped index must be rebuilt, not stamped as current"
            )


def test_index_dir_puts_the_db_in_the_tree(tmp_path):
    (tmp_path / "a.md").write_text("hello\n")
    with LexicalIndex(Root(label="p", path=tmp_path), index_dir=".forge_search") as ix:
        ix.reindex()
        assert ix.db_path == tmp_path / ".forge_search" / "p.db"
        assert ix.db_path.is_file()


def test_in_tree_index_is_never_indexed_by_itself(tmp_path):
    # An index that indexes itself matches on its own contents and grows every
    # pass. This exclusion is what makes in-tree storage safe at all.
    (tmp_path / "a.md").write_text("hello\n")
    root = Root(label="p", path=tmp_path)
    with LexicalIndex(root, index_dir=".forge_search") as ix:
        ix.reindex()
        first = ix.reindex().files_indexed
    with LexicalIndex(root, index_dir=".forge_search") as ix:
        assert ix.reindex().files_indexed == first
        assert not any(".forge_search" in h.path for h in ix.search("hello", limit=50))


def test_an_absolute_index_dir_opts_back_into_a_shared_directory(tmp_path):
    # The shared cache is no longer the default, but it must stay
    # reachable — it is the right choice for trees you cannot write to.
    shared = tmp_path / "cache"
    (tmp_path / "a.md").write_text("hello\n")
    with LexicalIndex(Root(label="p", path=tmp_path), index_dir=str(shared)) as ix:
        assert ix.db_path.parent == shared
        assert not (tmp_path / ".forge_search").exists()


def test_index_dir_ignores_itself(tmp_path):
    # The in-tree default writes into repos forge-search does not own, so it
    # must not become someone's accidental commit — and asking every user to
    # remember a .gitignore entry is the kind of remembering this project
    # exists to remove.
    (tmp_path / "a.md").write_text("hello\n")
    with LexicalIndex(Root(label="p", path=tmp_path)) as ix:
        ix.reindex()
    assert (tmp_path / ".forge_search" / ".gitignore").read_text() == "*\n"


def test_an_existing_gitignore_is_not_clobbered(tmp_path):
    (tmp_path / "a.md").write_text("hello\n")
    d = tmp_path / ".forge_search"
    d.mkdir()
    (d / ".gitignore").write_text("# hand-edited\n*\n")
    with LexicalIndex(Root(label="p", path=tmp_path)) as ix:
        ix.reindex()
    assert "hand-edited" in (d / ".gitignore").read_text()


class TestIdentifierTokenization:
    """Underscores are part of an identifier, not a separator.

    FTS5's stock `unicode61` tokenizer treats `_` (Unicode category Pc) as a
    separator at both index and query time, so `__hidden__` was stored and
    searched as the bare token `hidden`. Every dunder, every `snake_case`
    name, and every `_private` member therefore matched on its fragments —
    which is the opposite of the literal matching this index advertises.
    """

    @pytest.fixture
    def dunders(self, tmp_path: Path) -> Root:
        src = tmp_path / "dunders"
        src.mkdir()
        (src / "models.py").write_text("__hidden__ = frozenset()\n")
        (src / "prose.md").write_text("Lazy loading reintroduces hidden I/O.\n")
        (src / "other.py").write_text("def validate_token():\n    return 1\n")
        (src / "partial.py").write_text("token = 1\n")
        return Root(label="dunders", path=src)

    def test_a_dunder_does_not_match_the_bare_word(self, dunders: Root) -> None:
        index = LexicalIndex(dunders)
        index.reindex()

        paths = {h.path for h in index.search("__hidden__", limit=10)}

        assert paths == {"models.py"}, "the bare word 'hidden' must not match"
        index.close()

    def test_the_bare_word_does_not_match_a_dunder(self, dunders: Root) -> None:
        """The exclusion has to hold in both directions."""
        index = LexicalIndex(dunders)
        index.reindex()

        paths = {h.path for h in index.search("hidden", limit=10)}

        assert paths == {"prose.md"}
        index.close()

    def test_snake_case_identifiers_match_whole(self, dunders: Root) -> None:
        """`validate_token` is one name, not `validate` AND `token`."""
        index = LexicalIndex(dunders)
        index.reindex()

        paths = {h.path for h in index.search("validate_token", limit=10)}

        assert "partial.py" not in paths, "matched on the 'token' fragment alone"
        assert "other.py" in paths
        index.close()


class TestMatchCounts:
    """Complete enumeration, without paying for excerpts.

    A ranked page answers "show me the best hits". It cannot answer "what are
    ALL the call sites" or "does this symbol appear anywhere", because it is
    capped -- and the cap exists for a good reason, to bound token cost. The
    counting path answers those two questions instead, returning aggregates
    and paths rather than text, so completeness costs a fraction of what one
    page of excerpts costs.
    """

    @pytest.fixture
    def corpus(self, tmp_path: Path) -> Root:
        src = tmp_path / "corpus"
        (src / "pkg").mkdir(parents=True)
        # 3 files, 6 occurrences total, deliberately uneven per file.
        (src / "pkg" / "a.py").write_text("needle_token = 1\nneedle_token = 2\n")
        (src / "pkg" / "b.py").write_text("x = needle_token\n")
        (src / "notes.md").write_text(
            "About needle_token.\nAnd needle_token again.\nOnce more: needle_token.\n"
        )
        (src / "unrelated.py").write_text("nothing to see\n")
        return Root(label="corpus", path=src)

    def test_counts_every_occurrence_not_a_ranked_page(self, corpus: Root) -> None:
        index = LexicalIndex(corpus)
        index.reindex()

        counts = index.count_matches("needle_token")

        assert counts.total_chunks == 6
        assert counts.total_files == 3
        assert {f.path for f in counts.files} == {"pkg/a.py", "pkg/b.py", "notes.md"}
        assert dict((f.path, f.matches) for f in counts.files) == {
            "pkg/a.py": 2,
            "pkg/b.py": 1,
            "notes.md": 3,
        }
        assert counts.truncated is False
        index.close()

    def test_absence_is_provable(self, corpus: Root) -> None:
        """Zero on a fresh index is evidence; an empty ranked page never was."""
        index = LexicalIndex(corpus)
        index.reindex()

        counts = index.count_matches("nothinglikethis")

        assert counts.total_chunks == 0
        assert counts.total_files == 0
        assert counts.files == ()
        assert counts.truncated is False
        index.close()

    def test_an_unparseable_query_still_raises(self, corpus: Root) -> None:
        """Absence and 'did not search' must never look alike here either."""
        index = LexicalIndex(corpus)
        index.reindex()

        with pytest.raises(QueryError):
            index.count_matches("fts5:unbalanced (paren")
        index.close()

    def test_file_list_truncation_is_reported(self, corpus: Root) -> None:
        """The file roster is bounded too -- and says so when it bites."""
        index = LexicalIndex(corpus)
        index.reindex()

        counts = index.count_matches("needle_token", file_limit=2)

        assert len(counts.files) == 2
        assert counts.truncated is True
        # The aggregates stay whole even when the roster is clipped.
        assert counts.total_chunks == 6
        assert counts.total_files == 3
        index.close()
