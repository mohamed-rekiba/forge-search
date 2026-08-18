"""`--version` prints the version and exits.

It was missing, and the absence is worth a moment. A search index is stateful
and its schema is versioned: "which build is this" is the first question when a
result set looks wrong, when an MCP client and a shell are disagreeing about
what is installed, or when a bug report arrives. Until now the only answer was
to go reading site-packages.

It reports the same string the package carries, because there is only one — the
version is single-sourced in `forge_search.__version__` and read from there by
the build backend.
"""

from __future__ import annotations

import pytest

import forge_search
from forge_search.cli import main


def test_version_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert forge_search.__version__ in capsys.readouterr().out


def test_it_names_the_program_too(capsys: pytest.CaptureFixture[str]) -> None:
    """Installed under two names here, so the string has to say which."""
    with pytest.raises(SystemExit):
        main(["--version"])

    assert "forge-search" in capsys.readouterr().out


def test_it_works_without_a_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """Subcommands are required; asking the version must not require one."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
