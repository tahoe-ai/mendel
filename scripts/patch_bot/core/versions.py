from __future__ import annotations

import logging

from packaging.version import InvalidVersion, Version

log = logging.getLogger(__name__)


def max_version(versions: list[str]) -> str:
    """Return the highest version string from the list.

    Uses PEP 440 ordering (close enough to semver for the normal X.Y.Z case).
    Falls back to a lexical sort if any input can't be parsed.
    """
    candidates = [v for v in versions if v]
    if not candidates:
        raise ValueError("max_version called with no versions")
    try:
        return max(candidates, key=Version)
    except InvalidVersion:
        log.warning("unparseable version among %s — using string sort", candidates)
        return max(candidates)
