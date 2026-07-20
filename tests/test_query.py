"""Query translation, and the regression that motivated it.

The first test class pins the actual bug: a hyphenated filename — the most
common query shape in the observed workload — parsed as FTS5 column syntax,
errored, and got swallowed into an empty result. Valid query, real matches
present, zero results, no error.

That is the same silent-wrong-answer shape this project exists to fix, so
these tests matter more than their size suggests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.core.index import LexicalIndex
from forge_search.core.query import QueryError, to_fts5
from forge_search.core.roots import Root


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


class TestHyphenRegression:
    """The bug, end to end."""

    def test_hyphenated_identifier_is_found(self, tmp_path: Path) -> None:
        src = tmp_path / "repo"
        src.mkdir()
        (src / "notes.md").write_text("See require-active-ticket.sh for the gate.\n")

        with LexicalIndex(Root(label="repo", path=src)) as idx:
            idx.reindex()
            hits = idx.search("require-active-ticket")

        assert hits, (
            "hyphenated identifiers must be searchable — FTS5 read the hyphens "
            "as column syntax and the error was swallowed into an empty result"
        )

    @pytest.mark.parametrize(
        "query",
        [
            "require-active-ticket",   # hyphens — the original bug
            "core/index.py",           # a path
            "forge_search.core",       # dotted module
            "Root.label",              # attribute access
            "search_code(query)",      # a call, parens are FTS5 syntax
            "a:b",                     # colon — column separator in FTS5
        ],
    )
    def test_punctuation_heavy_queries_do_not_raise(self, query: str, tmp_path: Path) -> None:
        """Every one of these is a plausible search that FTS5 would choke on raw."""
        src = tmp_path / "repo"
        src.mkdir()
        (src / "f.md").write_text("nothing relevant here\n")

        with LexicalIndex(Root(label="repo", path=src)) as idx:
            idx.reindex()
            assert idx.search(query) == []  # no match, but crucially no crash


class TestErrorsAreNotEmptyResults:
    """"Found nothing" and "could not search" must never look alike."""

    def test_empty_query_raises(self) -> None:
        with pytest.raises(QueryError):
            to_fts5("   ")

    def test_malformed_raw_query_raises_not_returns_empty(self, tmp_path: Path) -> None:
        src = tmp_path / "repo"
        src.mkdir()
        (src / "f.md").write_text("content\n")

        with LexicalIndex(Root(label="repo", path=src)) as idx:
            idx.reindex()
            with pytest.raises(QueryError):
                idx.search("fts5:unbalanced (paren")

    def test_genuine_miss_returns_empty(self, tmp_path: Path) -> None:
        """The other side of the same coin — a real miss is still empty."""
        src = tmp_path / "repo"
        src.mkdir()
        (src / "f.md").write_text("content\n")

        with LexicalIndex(Root(label="repo", path=src)) as idx:
            idx.reindex()
            assert idx.search("definitelynotpresent") == []


class TestTranslation:
    def test_bare_word_passes_through_unquoted(self) -> None:
        """Bare words are already safe — quoting them would only add noise."""
        assert to_fts5("token") == "token"

    def test_punctuation_is_phrase_quoted(self) -> None:
        assert to_fts5("require-active-ticket") == '"require-active-ticket"'

    def test_multiple_bare_terms_are_anded(self) -> None:
        """Whitespace-separated terms AND together, as a search box implies."""
        assert to_fts5("validate token") == "validate token"

    def test_mixed_terms_quote_only_what_needs_it(self) -> None:
        assert to_fts5("validate require-active-ticket") == 'validate "require-active-ticket"'

    def test_embedded_quote_is_escaped(self) -> None:
        """An unescaped quote would terminate the phrase and change the query."""
        assert to_fts5('say"hi') == '"say""hi"'

    def test_raw_prefix_passes_expression_through(self) -> None:
        assert to_fts5("fts5:foo OR bar") == "foo OR bar"

    def test_raw_prefix_with_empty_body_raises(self) -> None:
        with pytest.raises(QueryError):
            to_fts5("fts5:   ")
