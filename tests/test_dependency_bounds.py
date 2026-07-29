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

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _declared_requirements() -> dict[str, SpecifierSet]:
    """Map every declared runtime dependency name to its version specifier."""
    data = tomllib.loads(PYPROJECT.read_text())
    reqs = {}
    for raw in data["project"]["dependencies"]:
        req = Requirement(raw)
        reqs[req.name] = req.specifier
    return reqs


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


class TestImportContractHolds:
    """The installed environment actually provides what `server.py` imports.

    Weaker than the declaration tests above — `uv.lock` makes this pass even
    when the declared bound is wrong — but it is the assertion that names the
    real contract, so it belongs next to them.
    """

    def test_server_module_imports(self) -> None:
        import forge_search.core.server  # noqa: F401
