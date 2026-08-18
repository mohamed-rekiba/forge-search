"""No `--root` falls back to the working directory — audibly.

apexyard's session-start hook runs `doctor` and `reindex --incremental
--if-stale=N` with no `--root` at all, so a hard failure there means the hook
degrades to "search is not reachable" on every session.

The recorded objection to inferring a root is that a wrong guess is *silent*:
the wrong tree gets indexed and every later search returns plausible results
from somewhere else. That objection is about silence, not about the fallback,
so the fallback announces the absolute path it resolved. A stated default is
checkable; that is the whole difference.
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


def test_doctor_without_root_probes_the_working_directory(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hook's probe. Exit 0 is what tells it search is reachable."""
    rc = main(["doctor"])
    captured = capsys.readouterr()

    assert rc == 0
    assert str(project) in captured.err


def test_reindex_without_root_uses_the_working_directory(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hook's refresh, with the exact flags it passes."""
    rc = main(["reindex", "--incremental", "--if-stale=86400"])
    captured = capsys.readouterr()

    assert rc == 0
    assert str(project) in captured.err


def test_the_default_names_the_absolute_path_it_chose(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A guess you can read is not the silent guess the design rejects."""
    main(["search", "validate_token"])
    captured = capsys.readouterr()

    assert "no --root given" in captured.err
    assert str(project) in captured.err
    # Absolute, so it can be checked against the tree the caller meant.
    assert str(project) != "." and Path(str(project)).is_absolute()


def test_an_explicit_root_is_never_overridden(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "z.py").write_text("def validate_token():\n    pass\n")

    main(["search", "validate_token", "--root", str(other)])
    captured = capsys.readouterr()

    assert "no --root given" not in captured.err
    assert "other" in captured.out


def test_serve_also_falls_back_and_announces(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`serve` takes the fallback too, on the same terms as everything else.

    It was briefly excluded, on the reasoning that a server's root is written
    once into a config and a wrong one is wrong for a whole session rather than
    one command. That risk is real, but it is the *silence* that makes it
    unrecoverable, and the notice removes the silence here exactly as it does
    elsewhere. One rule across every subcommand also means a caller never has
    to remember which half of the CLI guesses.
    """
    import forge_search.core.server as server_mod

    monkeypatch.setattr(server_mod.ForgeSearch, "run", lambda self, **kw: None)

    assert main(["serve"]) == 0

    err = capsys.readouterr().err
    assert "no --root given" in err
    assert str(project) in err
