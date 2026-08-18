"""How the CLI fails, as opposed to what it refuses.

Every message here was already written, and written well — the refusals name
the reason and the fix. What was missing is that some of them arrived as an
unhandled exception, so a careful sentence was printed underneath twenty lines
of interpreter frames pointing at site-packages. A traceback tells a reader
that the tool broke; a refusal tells them what to do. Reaching the second by
way of the first loses the message to the noise around it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.cli import main


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "auth.py").write_text("def validate_token():\n    pass\n")
    monkeypatch.chdir(p)
    return p


class TestBenchWithoutAFixture:
    def test_it_refuses_without_a_traceback(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["bench"])
        captured = capsys.readouterr()

        assert rc == 2
        assert "query fixture" in captured.err
        assert "Traceback" not in captured.err

    def test_the_refusal_names_the_flag_that_fixes_it(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The existing message explains the principle but not the remedy."""
        main(["bench"])

        assert "--fixture" in capsys.readouterr().err


class TestServeInterrupted:
    def test_ctrl_c_exits_quietly(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Ctrl-C is how a server is stopped, not a crash.

        The interpreter's default is a KeyboardInterrupt traceback through
        anyio and asyncio internals, which reads as a fault in the tool.
        """
        import forge_search.core.server as server_mod

        def _boom(self, **kwargs):  # noqa: ANN001, ANN003
            raise KeyboardInterrupt

        monkeypatch.setattr(server_mod.ForgeSearch, "run", _boom)

        rc = main(["serve", "--root", str(project)])
        captured = capsys.readouterr()

        assert rc == 0
        assert "Traceback" not in captured.err
        assert "KeyboardInterrupt" not in captured.err
