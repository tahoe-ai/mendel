import json
from pathlib import Path

from patch_bot.analysis.manifest import read_direct_deps


def test_npm_direct_deps_collects_all_sections(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"sanitize-html": "^2.7.0", "express": "^4.0.0"},
                "devDependencies": {"jest": "^29.0.0"},
            }
        )
    )
    assert read_direct_deps(tmp_path, "npm") == {"sanitize-html", "express", "jest"}


def test_npm_missing_package_json(tmp_path: Path):
    assert read_direct_deps(tmp_path, "npm") is None


def test_npm_malformed_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text("{not valid json")
    assert read_direct_deps(tmp_path, "npm") is None


def test_pip_is_unsupported(tmp_path: Path):
    assert read_direct_deps(tmp_path, "pip") is None
