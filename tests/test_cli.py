"""CLI argument handling — the first surface every user touches.

Untested until an unexpanded `${workspaceFolder}` reached a real MCP client
and printed a message that pointed at the wrong problem.
"""

from __future__ import annotations

import pytest

from forge_search.cli import main


def test_unexpanded_variable_is_named_as_the_cause():
    # The bare "not a directory: /cwd/${someVar}" this used to print sent
    # people hunting for a missing folder. The real cause is a client that
    # never substituted the variable — say that, since which variables a
    # client expands differs between clients.
    #
    # ${workspaceFolder} is the one exception: it has its own cwd fallback.
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--root", "${someOtherVar}"])
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


def test_workspace_folder_falls_back_to_cwd(tmp_path, monkeypatch):
    # Several MCP clients pass ${workspaceFolder} through unexpanded. Rather
    # than fail, fall back to cwd — which for a stdio MCP server is the
    # project root in practice, though its docs do not guarantee it.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.md").write_text("hello\n")
    assert main(["index", "--root", "${workspaceFolder}"]) == 0


def test_workspace_folder_with_a_subpath(tmp_path, monkeypatch):
    # Matching only the bare string would leave this failing for a reason
    # nobody would guess from the error.
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.md").write_text("hello\n")
    monkeypatch.chdir(tmp_path)
    assert main(["index", "--root", "${workspaceFolder}/sub"]) == 0


def test_other_unexpanded_variables_are_still_rejected(tmp_path, monkeypatch):
    # The shim is for one known-broken variable, not a licence to silently
    # resolve any unsubstituted config to the working directory.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["index", "--root", "${projectRoot}"])
    assert "unexpanded variable" in str(exc.value)
