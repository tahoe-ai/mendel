from pathlib import Path

from patch_bot.analysis.dep_tree import find_parents

# express → path-to-regexp; sanitize-html → postcss → nanoid
_YARN_LOCK = """\
sanitize-html@^2.7.0:
  version "2.11.0"
  dependencies:
    postcss "^8.3.11"
    htmlparser2 "^8.0.0"

postcss@^8.3.11:
  version "8.4.31"
  dependencies:
    nanoid "^3.3.6"

express@^4.0.0:
  version "4.18.2"
  dependencies:
    path-to-regexp "0.1.7"

path-to-regexp@0.1.7:
  version "0.1.7"

htmlparser2@^8.0.0:
  version "8.0.2"

nanoid@^3.3.6:
  version "3.3.7"
"""


def test_finds_immediate_direct_parent(tmp_path: Path):
    (tmp_path / "yarn.lock").write_text(_YARN_LOCK)
    parents = find_parents(tmp_path, "npm", "postcss", {"sanitize-html", "express"})
    assert parents == ["sanitize-html"]


def test_finds_parent_through_deeper_nesting(tmp_path: Path):
    # nanoid sits two levels down: sanitize-html → postcss → nanoid
    (tmp_path / "yarn.lock").write_text(_YARN_LOCK)
    parents = find_parents(tmp_path, "npm", "nanoid", {"sanitize-html", "express"})
    assert parents == ["sanitize-html"]


def test_no_candidate_when_parent_not_a_direct_dep(tmp_path: Path):
    (tmp_path / "yarn.lock").write_text(_YARN_LOCK)
    # postcss's only parent (sanitize-html) is not in the direct-dep set
    assert find_parents(tmp_path, "npm", "postcss", {"express"}) == []


def test_pip_returns_empty(tmp_path: Path):
    assert find_parents(tmp_path, "pip", "anything", {"x"}) == []


def test_no_lockfile_returns_empty(tmp_path: Path):
    assert find_parents(tmp_path, "npm", "postcss", {"sanitize-html"}) == []
