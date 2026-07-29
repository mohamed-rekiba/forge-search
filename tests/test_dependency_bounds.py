"""Regression tests for dependency bounds that must not silently widen.

These exist because of a real shipped failure, and they are written to fail on
the *declaration* rather than on the installed environment — which is the whole
point. `uv.lock` pins what a developer and CI actually install, so a test that
merely imports the package passes happily while the published metadata resolves
into something broken for everyone doing a fresh install.

The failure, 2026-07-29: `pyproject.toml` declared `mcp>=1.28.1` with no upper
bound. `mcp` 2.0.0 removed the `mcp.server.fastmcp` module that
`core/server.py` imports, so every fresh install resolved into a tool that
installed cleanly and then died at import time with

    ModuleNotFoundError: No module named 'mcp.server.fastmcp'

Silent in the way that matters: existing installs kept working off their lock,
and the error surfaced in an MCP client's stderr as a client-looking problem
rather than as a dependency problem.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
SRC = Path(__file__).resolve().parent.parent / "src"

#: Declared distributions whose Python API this project imports directly, mapped
#: to the module name as it appears in `src/`. These are the ones whose major
#: must be bounded: a major bump is exactly where an import path may vanish,
#: which is the defect that broke every fresh install.
#:
#: `fastapi` and `uvicorn` are deliberately absent. They are declared to satisfy
#: the SDK's optional HTTP transport, and no `forge_search` module imports them
#: — so there is no import path of ours for their major to break. The mapping is
#: kept honest by `test_every_listed_distribution_is_actually_imported`.
IMPORTED_DISTRIBUTIONS = {
    "mcp": "mcp",
    "onnxruntime": "onnxruntime",
    "sqlite-vec": "sqlite_vec",
    "tokenizers": "tokenizers",
}


def _declared_requirements() -> dict[str, SpecifierSet]:
    """Map every declared dependency name to its version specifier.

    Covers `project.dependencies` **and** every `project.optional-dependencies`
    group. Reading only the former was the original oversight: the lesson of the
    `mcp` breakage got applied to one declared dependency while three others,
    all imported in `src/`, stayed unbounded in the `semantic` extra.
    """
    data = tomllib.loads(PYPROJECT.read_text())
    groups = [data["project"].get("dependencies", [])]
    groups += list(data["project"].get("optional-dependencies", {}).values())

    reqs = {}
    for group in groups:
        for raw in group:
            req = Requirement(raw)
            reqs[req.name] = req.specifier
    return reqs


def _next_major(spec: SpecifierSet) -> str:
    """The lowest version that a correctly-bounded specifier must exclude.

    Derived from the specifier's own floor rather than hardcoded, so raising a
    floor never silently invalidates the assertion.
    """
    floors = [s.version for s in spec if s.operator in (">=", "==", "~=")]
    assert floors, f"no floor found in {spec!r} — cannot derive the next major"
    major = int(Version(min(floors, key=Version)).major)
    return f"{major + 1}.0.0"


class TestMcpMajorIsBounded:
    """`mcp` must not float past the major whose API we actually import.

    `core/server.py` reaches into `mcp.server.mcpserver` directly. A major bump
    is precisely where such a path is allowed to disappear — which is what
    happened when 2.0 removed the `mcp.server.fastmcp` this project imported
    before the migration. So the constraint has to admit the major we migrated
    to, and exclude the next one until someone migrates again.
    """

    def test_mcp_is_declared(self) -> None:
        assert "mcp" in _declared_requirements(), (
            "mcp is the one non-optional runtime dependency; if it stopped being "
            "declared, the bound tests below would pass vacuously."
        )

    def test_mcp_admits_the_migrated_major(self) -> None:
        """The floor must not have been left behind on an older major."""
        spec = _declared_requirements()["mcp"]
        assert "2.0.0" in spec, (
            f"pyproject declares mcp{spec}, which excludes 2.0.0 — but the code "
            "imports mcp.server.mcpserver, which only exists in 2.x. The "
            "declaration and the import sites have drifted apart."
        )

    @pytest.mark.parametrize("version", ["3.0.0", "3.1.0", "4.0.0"])
    def test_mcp_specifier_excludes_unmigrated_majors(self, version: str) -> None:
        """No major we have not migrated to may satisfy the constraint."""
        spec = _declared_requirements()["mcp"]
        assert version not in spec, (
            f"mcp{spec} admits {version}. Majors must be adopted deliberately, "
            "with the import sites migrated, not picked up by a resolver. That "
            "is exactly how the 1.x→2.0 breakage reached users."
        )


class TestEveryImportedDependencyIsBounded:
    """The rule applies to all of them, not just the one that broke.

    The `mcp` incident produced a policy — bound the major of anything whose
    import path you reach into — and then applied it to `mcp` alone. Three
    further distributions in the `semantic` extra are imported throughout
    `src/` and were left unbounded, so the identical failure was still
    available: `onnxruntime` 2.0 or `tokenizers` 1.0 removing something we
    import, resolved automatically into a fresh install.
    """

    @pytest.mark.parametrize("dist", sorted(IMPORTED_DISTRIBUTIONS))
    def test_declared(self, dist: str) -> None:
        assert dist in _declared_requirements(), (
            f"{dist} is imported in src/ but not declared in pyproject.toml — "
            "so nothing pins the version the code is written against."
        )

    @pytest.mark.parametrize("dist", sorted(IMPORTED_DISTRIBUTIONS))
    def test_major_is_bounded(self, dist: str) -> None:
        spec = _declared_requirements()[dist]
        nxt = _next_major(spec)
        assert nxt not in spec, (
            f"{dist}{spec} admits {nxt}. {dist} is imported directly in src/, "
            "so a major bump may remove the API this code uses — exactly how "
            "the mcp 1.x→2.0 breakage reached every fresh install. Bound it."
        )

    @pytest.mark.parametrize("dist,module", sorted(IMPORTED_DISTRIBUTIONS.items()))
    def test_every_listed_distribution_is_actually_imported(
        self, dist: str, module: str
    ) -> None:
        """Keeps the mapping honest.

        If a dependency stops being imported it should leave this list, so the
        bound requirement tracks real coupling rather than becoming folklore.
        """
        hits = [
            p
            for p in SRC.rglob("*.py")
            if f"import {module}" in p.read_text() or f"from {module}" in p.read_text()
        ]
        assert hits, (
            f"{dist} is listed as imported (module {module!r}) but no file under "
            "src/ imports it. Either the mapping is stale or the dependency is "
            "no longer needed."
        )


class TestImportContractHolds:
    """The installed environment actually provides what `server.py` imports.

    Weaker than the declaration tests above — `uv.lock` makes this pass even
    when the declared bound is wrong — but it is the assertion that names the
    real contract, so it belongs next to them.
    """

    def test_server_module_imports(self) -> None:
        import forge_search.core.server  # noqa: F401
