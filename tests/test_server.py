"""Server-layer behaviour.

The emphasis is on the boundaries where a wrong answer would look like a right
one: a bad project name, an unparseable query, a stale index, a partially
unreadable set of roots. Each of those has a tempting silent-degradation
implementation, and each is tested here for the loud one instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from forge_search.core.query import QueryError
from forge_search.core.roots import Root
from forge_search.core.server import MAX_TOP_K, ForgeSearch


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def search(tmp_path: Path):
    """Two roots, so multi-root behaviour is exercised by default."""
    alpha = tmp_path / "alpha"
    (alpha / "src").mkdir(parents=True)
    (alpha / "src" / "auth.py").write_text("def validate_token():\n    pass\n")
    (alpha / "guide.md").write_text("How to validate_token in prose.\n")

    beta = tmp_path / "beta"
    beta.mkdir()
    (beta / "other.py").write_text("def validate_token():\n    return 1\n")

    fs = ForgeSearch(
        roots=[
            Root(label="alpha", path=alpha),
            Root(label="beta", path=beta),
        ]
    )
    yield fs
    fs.close()


class TestToolRegistration:
    def test_tools_are_registered_under_the_configured_name(self, tmp_path: Path) -> None:
        """The server name is configuration — a standalone tool must not
        assume another product's identity."""
        fs = ForgeSearch(
            roots=[Root(label="r", path=tmp_path)], server_name="custom-name"
        )
        mcp = fs.build()

        names = {t.name for t in asyncio.run(mcp.list_tools())}

        assert mcp.name == "custom-name"
        assert names == {"search_code", "search_docs", "reindex"}
        fs.close()

    def test_query_is_the_only_required_argument(self, tmp_path: Path) -> None:
        fs = ForgeSearch(roots=[Root(label="r", path=tmp_path)])
        tools = {t.name: t for t in asyncio.run(fs.build().list_tools())}

        # mcp 2.x renamed the model fields to snake_case (`inputSchema` →
        # `input_schema`); the JSON wire keys are unchanged.
        assert tools["search_code"].input_schema["required"] == ["query"]
        assert (
            "required" not in tools["reindex"].input_schema
            or not tools["reindex"].input_schema["required"]
        )
        fs.close()


class TestSearching:
    def test_searches_all_roots_by_default(self, search: ForgeSearch) -> None:
        result = search._search("validate_token", project=None, top_k=10, doc_only=False)
        assert {r["project"] for r in result["results"]} == {"alpha", "beta"}

    def test_project_filter_restricts(self, search: ForgeSearch) -> None:
        result = search._search(
            "validate_token", project="beta", top_k=10, doc_only=False
        )
        assert {r["project"] for r in result["results"]} == {"beta"}

    def test_unknown_project_raises_rather_than_searching_everything(
        self, search: ForgeSearch
    ) -> None:
        """Silently widening the search answers a question nobody asked."""
        with pytest.raises(ValueError, match="unknown project"):
            search._search("validate_token", project="nope", top_k=10, doc_only=False)

    def test_error_names_the_known_roots(self, search: ForgeSearch) -> None:
        """An error the caller can act on beats one they have to investigate."""
        with pytest.raises(ValueError, match="alpha, beta"):
            search._search("x", project="nope", top_k=10, doc_only=False)

    def test_docs_filter_excludes_code(self, search: ForgeSearch) -> None:
        result = search._search("validate_token", project=None, top_k=10, doc_only=True)
        paths = {r["path"] for r in result["results"]}

        assert paths == {"guide.md"}, f"non-prose leaked into search_docs: {paths}"

    def test_results_are_ranked_descending(self, search: ForgeSearch) -> None:
        result = search._search("validate_token", project=None, top_k=10, doc_only=False)
        scores = [r["score"] for r in result["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_paths_returned_are_relative(self, search: ForgeSearch) -> None:
        result = search._search("validate_token", project=None, top_k=10, doc_only=False)
        assert all(not r["path"].startswith("/") for r in result["results"])


class TestBounds:
    def test_top_k_is_capped(self, tmp_path: Path) -> None:
        """Unbounded output is the cost this tool exists to avoid."""
        src = tmp_path / "big"
        src.mkdir()
        (src / "f.py").write_text("\n".join(f"needle {i}" for i in range(500)))

        fs = ForgeSearch(roots=[Root(label="big", path=src)])
        result = fs._search("needle", project=None, top_k=10_000, doc_only=False)

        assert len(result["results"]) <= MAX_TOP_K
        fs.close()

    def test_top_k_below_one_is_clamped_not_crashed(self, search: ForgeSearch) -> None:
        result = search._search("validate_token", project=None, top_k=0, doc_only=False)
        assert len(result["results"]) == 1


class TestErrorsStayLoud:
    def test_bad_query_raises_rather_than_returning_empty(
        self, search: ForgeSearch
    ) -> None:
        """The bug this project's own smoke test found: an unparseable query
        must never be indistinguishable from an honest miss."""
        with pytest.raises(QueryError):
            search._search(
                "fts5:unbalanced (paren", project=None, top_k=10, doc_only=False
            )

    def test_genuine_miss_returns_empty_results(self, search: ForgeSearch) -> None:
        result = search._search("nothinglikethis", project=None, top_k=10, doc_only=False)
        assert result["results"] == []
        assert result["degraded"] is None


class TestFreshness:
    def test_edits_are_visible_without_an_explicit_reindex(
        self, search: ForgeSearch
    ) -> None:
        """Freshness is owned by the read path. Requiring callers to remember
        a reindex is a convention, and conventions are what produced every
        defect this project fixes."""
        search._search("validate_token", project=None, top_k=10, doc_only=False)

        beta = next(r for r in search.roots if r.label == "beta")
        (beta.path / "other.py").write_text("def brand_new_symbol():\n    pass\n")

        result = search._search(
            "brand_new_symbol", project=None, top_k=10, doc_only=False
        )

        assert result["results"], "an edit was invisible until an explicit reindex"
        assert result["reindexed"] == ["beta"], "the auto-reindex should be reported"

    def test_no_reindex_reported_when_nothing_changed(self, search: ForgeSearch) -> None:
        search._search("validate_token", project=None, top_k=10, doc_only=False)
        result = search._search("validate_token", project=None, top_k=10, doc_only=False)
        assert result["reindexed"] is None


class TestReindexTool:
    def test_reindex_reports_per_root(self, search: ForgeSearch) -> None:
        mcp = search.build()
        result = asyncio.run(mcp.call_tool("reindex", {}))
        # mcp 2.x returns a `CallToolResult` rather than a
        # (content, structured) tuple; the dict a tool returns arrives on
        # `.structured_content`.
        payload = result.structured_content

        assert payload is not None
        assert set(payload["roots"]) == {"alpha", "beta"}

    def test_reindex_unknown_project_raises(self, search: ForgeSearch) -> None:
        """A wrong project name must surface as a tool error naming the known
        roots — not a bare failure, and not a silent reindex of everything.

        Asserting on `Exception` here would pass on almost any failure,
        including the wrong one. The message is the part that makes the error
        actionable, so that is what gets checked.
        """
        from mcp.server.mcpserver.exceptions import ToolError

        mcp = search.build()
        with pytest.raises(ToolError, match="unknown project"):
            asyncio.run(mcp.call_tool("reindex", {"project": "nope"}))


class TestDynamicRootDiscovery:
    """A project registered after the server started must be reachable without
    a restart. The root set is not fixed for the life of the server: an adapter
    over a portfolio re-reads its registry, so a new clone should be found the
    first time it is named. Requiring a restart would be exactly the "rely on a
    human to remember" convention that freshness-on-read exists to remove.
    """

    def _make(self, tmp_path: Path, roots: list[Root], *, resolver=None) -> ForgeSearch:
        return ForgeSearch(roots=list(roots), root_resolver=resolver)

    def test_a_project_added_after_startup_is_found_on_first_reference(
        self, tmp_path: Path
    ) -> None:
        alpha = tmp_path / "alpha"
        alpha.mkdir()
        (alpha / "a.py").write_text("def alpha_symbol():\n    pass\n")
        beta = tmp_path / "beta"
        beta.mkdir()
        (beta / "b.py").write_text("def beta_symbol():\n    pass\n")

        # The server starts knowing only alpha; beta is "registered" later by
        # the resolver returning it on the next call.
        current = [Root(label="alpha", path=alpha)]

        def resolver() -> list[Root]:
            return [Root(label="alpha", path=alpha), Root(label="beta", path=beta)]

        fs = self._make(tmp_path, current, resolver=resolver)
        try:
            result = fs._search("beta_symbol", project="beta", top_k=10, doc_only=False)
            assert {r["project"] for r in result["results"]} == {"beta"}
        finally:
            fs.close()

    def test_discovered_root_persists_for_later_search_all(self, tmp_path: Path) -> None:
        """Once discovered by name, the new root joins the set — a subsequent
        search-all includes it, without another re-resolution."""
        alpha = tmp_path / "alpha"
        alpha.mkdir()
        (alpha / "a.py").write_text("def shared_symbol():\n    pass\n")
        beta = tmp_path / "beta"
        beta.mkdir()
        (beta / "b.py").write_text("def shared_symbol():\n    pass\n")

        calls = {"n": 0}

        def resolver() -> list[Root]:
            calls["n"] += 1
            return [Root(label="alpha", path=alpha), Root(label="beta", path=beta)]

        fs = self._make(tmp_path, [Root(label="alpha", path=alpha)], resolver=resolver)
        try:
            fs._search("shared_symbol", project="beta", top_k=10, doc_only=False)
            result = fs._search("shared_symbol", project=None, top_k=10, doc_only=False)
            assert {r["project"] for r in result["results"]} == {"alpha", "beta"}
            assert calls["n"] == 1, "root set should re-resolve once, not per call"
        finally:
            fs.close()

    def test_genuinely_unknown_project_still_raises_after_reresolution(
        self, tmp_path: Path
    ) -> None:
        """Re-resolution is a chance to discover, not a licence to widen: a name
        no resolver knows must still fail loudly, naming the known roots."""
        alpha = tmp_path / "alpha"
        alpha.mkdir()
        (alpha / "a.py").write_text("x = 1\n")

        def resolver() -> list[Root]:
            return [Root(label="alpha", path=alpha)]

        fs = self._make(tmp_path, [Root(label="alpha", path=alpha)], resolver=resolver)
        try:
            with pytest.raises(ValueError, match="unknown project 'ghost'"):
                fs._search("x", project="ghost", top_k=10, doc_only=False)
        finally:
            fs.close()

    def test_no_resolver_means_no_reresolution(self, tmp_path: Path) -> None:
        """A standalone server with an explicit --root has a fixed set. Absent a
        resolver, an unknown project fails immediately — no discovery attempt."""
        alpha = tmp_path / "alpha"
        alpha.mkdir()
        (alpha / "a.py").write_text("x = 1\n")

        fs = ForgeSearch(roots=[Root(label="alpha", path=alpha)])
        try:
            with pytest.raises(ValueError, match="unknown project"):
                fs._search("x", project="beta", top_k=10, doc_only=False)
        finally:
            fs.close()
