"""CLI argument handling — the first surface every user touches.

Untested until an unexpanded `${workspaceFolder}` reached a real MCP client
and printed a message that pointed at the wrong problem.
"""

from __future__ import annotations

import pytest

from forge_search.cli import main


def test_unexpanded_variable_is_named_as_the_cause():
    # The bare "not a directory: /cwd/${workspaceFolder}" this used to print
    # sent people hunting for a missing folder. The real cause is a client
    # that never substituted the variable — say that, since which variables
    # a client expands differs between clients.
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--root", "${workspaceFolder}"])
    assert "unexpanded variable" in str(exc.value)
    assert "absolute path" in str(exc.value)


def test_bare_dollar_variable_is_caught_too():
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--root", "$PROJECT_ROOT/sub"])
    assert "unexpanded variable" in str(exc.value)


def test_a_genuinely_missing_directory_still_says_so(tmp_path):
    # The diagnostic must not swallow the ordinary case.
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--root", str(tmp_path / "nope")])
    assert "not a directory" in str(exc.value)
    assert "unexpanded" not in str(exc.value)


def test_missing_root_lists_the_correct_forms():
    # Nothing is discovered, so the failure has to teach the fix.
    with pytest.raises(SystemExit) as exc:
        main(["serve"])
    assert "--root" in str(exc.value)


def test_a_real_directory_is_accepted(tmp_path):
    (tmp_path / "f.md").write_text("hello\n")
    assert main(["index", "--root", str(tmp_path)]) == 0
