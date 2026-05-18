from pathlib import Path

from patch_bot.lockfile_updaters.base import select_updater


def test_selects_poetry_when_lock_present(tmp_path: Path):
    (tmp_path / "poetry.lock").write_text("")
    (tmp_path / "requirements.txt").write_text("")  # both present
    u = select_updater(tmp_path)
    assert u is not None and u.name == "poetry"


def test_selects_pnpm_over_yarn_over_npm(tmp_path: Path):
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "yarn.lock").write_text("")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    u = select_updater(tmp_path)
    assert u is not None and u.name == "pnpm"


def test_selects_pip_tools_over_bare_requirements(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("")
    (tmp_path / "requirements.txt").write_text("")
    u = select_updater(tmp_path)
    assert u is not None and u.name == "pip-tools"


def test_returns_none_when_unsupported(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text("")
    (tmp_path / "go.mod").write_text("")
    assert select_updater(tmp_path) is None
