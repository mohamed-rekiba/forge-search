"""Roots from the environment, including the apexyard aliases.

A root can now be stated three ways, and the order between them is the whole
design: `--root` beats `FORGE_SEARCH_ROOTS`, which beats the apexyard aliases,
which beat the working-directory fallback. Every one of them is the caller
saying which tree they mean; only the last is a guess, and it is the only one
that has to be announced as such.

The apexyard pair is read rather than merely tolerated because apexyard's own
config validator requires both keys to be present and to point at real
directories. Requiring them in the config while ignoring them at runtime would
leave a reader with two plausible beliefs about which tree is searched and no
way to tell which is true.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_search.cli import main


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_SEARCH_CACHE_DIR", str(tmp_path / "cache"))
    for var in ("FORGE_SEARCH_ROOTS", "APEXYARD_OPS_ROOT", "APEXYARD_PORTFOLIO_ROOT"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def trees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    made = {}
    for name in ("ops", "portfolio", "elsewhere", "cwd"):
        d = tmp_path / name
        d.mkdir()
        (d / f"{name}.py").write_text(f"# marker {name}\ndef needle(): pass\n")
        made[name] = d
    monkeypatch.chdir(made["cwd"])
    return made


def _roots_line(err: str) -> str:
    return next(line for line in err.splitlines() if "roots:" in line)


class TestApexyardAliases:
    def test_both_apexyard_roots_are_searched(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("APEXYARD_OPS_ROOT", str(trees["ops"]))
        monkeypatch.setenv("APEXYARD_PORTFOLIO_ROOT", str(trees["portfolio"]))

        assert main(["index"]) == 0

        line = _roots_line(capsys.readouterr().err)
        assert "ops=" in line and "portfolio=" in line

    def test_they_are_labelled_by_what_the_variable_calls_them(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`--project ops` should mean the ops tree, not a directory basename."""
        monkeypatch.setenv("APEXYARD_OPS_ROOT", str(trees["ops"]))

        main(["index"])

        assert f"ops={trees['ops']}" in _roots_line(capsys.readouterr().err)

    def test_either_one_alone_is_enough(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("APEXYARD_PORTFOLIO_ROOT", str(trees["portfolio"]))

        assert main(["index"]) == 0

        line = _roots_line(capsys.readouterr().err)
        assert "portfolio=" in line
        assert str(trees["cwd"]) not in line, "the guess must not also apply"

    def test_a_stale_path_is_skipped_with_a_warning(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """These variables are optional, so a stale one must not be fatal.

        They belong to another product. forge-search reads them as a courtesy,
        and refusing to start because a variable it does not own points
        somewhere stale would make a courtesy into a dependency. Skipped, not
        silently: the warning names the variable, so a config that has drifted
        is still visible to whoever has to fix it.
        """
        monkeypatch.setenv("APEXYARD_OPS_ROOT", str(trees["ops"] / "gone"))
        monkeypatch.setenv("APEXYARD_PORTFOLIO_ROOT", str(trees["portfolio"]))

        assert main(["index"]) == 0

        err = capsys.readouterr().err
        assert "APEXYARD_OPS_ROOT" in err
        assert "portfolio=" in _roots_line(err)

    def test_every_variable_stale_falls_through_to_the_guess(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Skipping the last usable source leaves the ordinary fallback."""
        monkeypatch.setenv("APEXYARD_OPS_ROOT", str(trees["ops"] / "gone"))

        assert main(["index"]) == 0

        err = capsys.readouterr().err
        assert "APEXYARD_OPS_ROOT" in err
        assert "no --root given" in err
        assert str(trees["cwd"]) in err


class TestPrecedence:
    def test_an_explicit_root_beats_every_variable(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("APEXYARD_OPS_ROOT", str(trees["ops"]))
        monkeypatch.setenv("FORGE_SEARCH_ROOTS", str(trees["portfolio"]))

        main(["index", "--root", str(trees["elsewhere"])])

        line = _roots_line(capsys.readouterr().err)
        assert "elsewhere=" in line
        assert "ops=" not in line and "portfolio=" not in line

    def test_the_native_variable_beats_the_aliases(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One source at a time, so a reader never has to guess the union."""
        monkeypatch.setenv("APEXYARD_OPS_ROOT", str(trees["ops"]))
        monkeypatch.setenv("FORGE_SEARCH_ROOTS", f"mine={trees['elsewhere']}")

        main(["index"])

        line = _roots_line(capsys.readouterr().err)
        assert "mine=" in line
        assert "ops=" not in line

    def test_variables_beat_the_working_directory_guess(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("APEXYARD_OPS_ROOT", str(trees["ops"]))

        main(["index"])

        err = capsys.readouterr().err
        assert "no --root given" not in err, "a stated root is not a guess"

    def test_no_source_at_all_still_falls_back_and_says_so(
        self, trees, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["index"])

        err = capsys.readouterr().err
        assert "no --root given" in err
        assert str(trees["cwd"]) in err


class TestNativeVariable:
    def test_several_roots_separated_like_PATH(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import os

        monkeypatch.setenv(
            "FORGE_SEARCH_ROOTS",
            os.pathsep.join([f"a={trees['ops']}", str(trees["portfolio"])]),
        )

        assert main(["index"]) == 0

        line = _roots_line(capsys.readouterr().err)
        assert "a=" in line and "portfolio=" in line

    def test_the_source_is_named_in_the_notice(
        self, trees, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Which variable supplied the root is the first thing to check."""
        monkeypatch.setenv("FORGE_SEARCH_ROOTS", str(trees["ops"]))

        main(["index"])

        assert "FORGE_SEARCH_ROOTS" in capsys.readouterr().err
