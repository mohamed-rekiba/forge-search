"""The version lives in one place, and two tools read it from there.

`pyproject.toml` declares the version dynamic; hatchling reads it from the
package, and release-please writes it there. That removes the release-time
re-lock — uv records no version for a project whose version is dynamic, so a
bump no longer touches `uv.lock` — but it moves the version onto a line whose
*shape* two tools depend on.

Both dependencies are silent when broken. hatchling fails the build loudly, so
it looks after itself. release-please does not: its updater keys on the
`x-release-please-version` marker comment, and without it the bump is simply
skipped — releases keep happening, the package keeps reporting the old version,
and nothing anywhere says so. That is the failure this file exists to catch.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import forge_search

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "forge_search" / "__init__.py"


def test_the_package_carries_a_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", forge_search.__version__), (
        f"{forge_search.__version__!r} is not a release version"
    )


def test_pyproject_declares_it_dynamic_and_points_at_the_package() -> None:
    """A static version here would reintroduce the lockfile churn."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert "version" in data["project"].get("dynamic", []), (
        "a static [project] version puts the version back into uv.lock"
    )
    assert "version" not in data["project"]
    assert data["tool"]["hatch"]["version"]["path"] == "src/forge_search/__init__.py"


def test_the_release_marker_is_on_the_version_line() -> None:
    """release-please skips the bump silently without this comment.

    Nothing fails when it goes missing: the release completes, the tag is
    right, and the package reports the previous version indefinitely.
    """
    line = next(
        line for line in INIT.read_text().splitlines() if line.startswith("__version__")
    )

    assert "x-release-please-version" in line, (
        "release-please keys on this marker; without it the bump is skipped "
        "with no error anywhere"
    )


def test_release_please_is_told_to_update_that_file() -> None:
    """The marker is inert unless the file is listed as an extra file."""
    import json

    cfg = json.loads((ROOT / ".github" / "release-please-config.json").read_text())
    extra = cfg["packages"]["."].get("extra-files", [])

    assert "src/forge_search/__init__.py" in extra
