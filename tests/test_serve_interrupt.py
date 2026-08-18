"""Stopping the server takes one Ctrl-C.

It used to take three, and the third printed a traceback. The cause is not in
the signal handling: the stdio transport reads stdin by iterating a file, which
anyio runs as a blocking `readline()` on a worker thread. Against a terminal
that thread sits in `read()`, nothing is ever going to write to it, and
cancellation cannot reach a thread blocked in a syscall. asyncio's own SIGINT
handling makes the first press cancel the main task rather than raise, so it
appears to do nothing; the second raises, and interpreter shutdown then joins
those non-daemon worker threads and hangs until a third press interrupts the
join and prints `KeyboardInterrupt` out of `threading._shutdown`.

So `serve` handles SIGINT itself and leaves without unwinding.

These tests drive the installed handler directly rather than spawning a real
server on a pty. That was the first version, and it was measurably worse: it
had to fork a pytest process with the semantic arm's native libraries already
loaded, which aborted the whole run in the C++ runtime — intermittently, one
run in two. A test that can flakily abort the suite buys less than it costs,
whatever it proves. The end-to-end behaviour was verified by hand against a
real terminal, and the three properties that make it work are pinned here.
"""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from forge_search.cli import main


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "a.py").write_text("def needle():\n    pass\n")
    return p


def _run_serve_capturing_handler(project: Path, monkeypatch: pytest.MonkeyPatch):
    """Start `serve` with the transport stubbed, and return its SIGINT handler."""
    import forge_search.core.server as server_mod

    captured: dict[str, object] = {}

    def _fake_run(self, **_kwargs):  # noqa: ANN001, ANN003
        # Whatever is installed while the server is running is the handler a
        # Ctrl-C would reach.
        captured["handler"] = signal.getsignal(signal.SIGINT)

    monkeypatch.setattr(server_mod.ForgeSearch, "run", _fake_run)
    assert main(["serve", "--root", str(project), "--quiet"]) == 0
    return captured["handler"]


def test_serve_installs_its_own_sigint_handler(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Left to the default, the first press cancels a task that cannot finish."""
    handler = _run_serve_capturing_handler(project, monkeypatch)

    assert callable(handler)
    assert handler not in (signal.SIG_DFL, signal.SIG_IGN)


def test_the_handler_leaves_immediately_rather_than_unwinding(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It must not return: returning is what hangs on the blocked reader."""
    handler = _run_serve_capturing_handler(project, monkeypatch)

    exited: list[int] = []
    monkeypatch.setattr("os._exit", exited.append)

    handler(signal.SIGINT, None)  # type: ignore[operator]

    assert exited == [0], "the handler returned instead of exiting"
    assert "interrupted" in capsys.readouterr().err


def test_the_previous_handler_is_restored_afterwards(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serve` is one subcommand, not the whole process's signal policy."""
    before = signal.getsignal(signal.SIGINT)

    _run_serve_capturing_handler(project, monkeypatch)

    assert signal.getsignal(signal.SIGINT) is before
