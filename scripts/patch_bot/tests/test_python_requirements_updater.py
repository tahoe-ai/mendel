from pathlib import Path

import pytest
from patch_bot.lockfile_updaters.python_requirements import RequirementsUpdater


@pytest.mark.asyncio
async def test_bumps_pinned_version(tmp_path: Path):
    f = tmp_path / "requirements.txt"
    f.write_text("requests==2.19.0\nflask==2.0.0\n")
    res = await RequirementsUpdater().update(tmp_path, "requests", "2.32.0")
    assert res.success
    assert "requirements.txt" in res.changed_files
    assert "requests==2.32.0" in f.read_text()
    assert "flask==2.0.0" in f.read_text()  # unrelated lines untouched


@pytest.mark.asyncio
async def test_bumps_unpinned_version(tmp_path: Path):
    f = tmp_path / "requirements.txt"
    f.write_text("requests\n")
    res = await RequirementsUpdater().update(tmp_path, "requests", "2.32.0")
    assert res.success
    assert "requests==2.32.0" in f.read_text()


@pytest.mark.asyncio
async def test_no_match_reports_failure(tmp_path: Path):
    f = tmp_path / "requirements.txt"
    f.write_text("flask==2.0.0\n")
    res = await RequirementsUpdater().update(tmp_path, "requests", "2.32.0")
    assert not res.success
    assert "not found" in res.message


@pytest.mark.asyncio
async def test_preserves_comments_and_extras(tmp_path: Path):
    f = tmp_path / "requirements.txt"
    f.write_text("# pinned for legacy\nrequests==2.19.0  # CVE-2023-xxxx\n")
    res = await RequirementsUpdater().update(tmp_path, "requests", "2.32.0")
    assert res.success
    text = f.read_text()
    assert text.startswith("# pinned for legacy\n")
    assert "requests==2.32.0" in text
    assert "# CVE-2023-xxxx" in text


def test_detects_only_when_requirements_present(tmp_path: Path):
    assert RequirementsUpdater().detect(tmp_path) is None
    (tmp_path / "requirements.txt").write_text("")
    assert RequirementsUpdater().detect(tmp_path) == tmp_path
