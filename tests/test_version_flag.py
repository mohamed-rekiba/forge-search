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


class TestHelpSurface:
    """What a bare `forge-search` does, and what its help says.

    Two small things that decide whether a first run teaches anything. Running
    the command with no arguments is someone asking what it is; argparse's
    answer was `error: the following arguments are required: command` and exit
    2, which tells them they did something wrong instead of what they can do.

    And the version belongs in the help, not only behind a flag you have to
    know about. `--version` answers "which build" once you suspect a version
    problem; the help answers it while you are already reading.
    """

    def test_no_arguments_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            main([])

        out = capsys.readouterr().out
        assert exc.value.code == 0
        assert "usage: forge-search" in out
        # The subcommands are the point of showing it at all.
        for command in ("serve", "search", "count", "index", "doctor"):
            assert command in out

    def test_no_arguments_is_not_reported_as_an_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """It goes to stdout, so it can be piped, and stderr stays clean."""
        with pytest.raises(SystemExit):
            main([])

        captured = capsys.readouterr()
        assert captured.err == ""
        assert "error:" not in captured.out

    def test_a_malformed_invocation_still_fails(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Only the empty case is a question; a wrong command is still wrong."""
        with pytest.raises(SystemExit) as exc:
            main(["nosuchcommand"])

        assert exc.value.code == 2

    def test_the_help_carries_the_version(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit):
            main(["--help"])

        assert forge_search.__version__ in capsys.readouterr().out

    def test_the_bare_help_carries_it_too(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit):
            main([])

        assert forge_search.__version__ in capsys.readouterr().out
