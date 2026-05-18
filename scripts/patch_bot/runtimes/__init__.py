from __future__ import annotations

from pathlib import Path

from .base import CompatVerdict, RuntimePlugin
from .gae import GaePlugin
from .generic import GenericPlugin


def select_plugin(repo_root: Path) -> RuntimePlugin:
    """First plugin whose detect() returns True; falls back to generic."""
    for plugin in (GaePlugin(), GenericPlugin()):
        if plugin.detect(repo_root):
            return plugin
    return GenericPlugin()


__all__ = ["CompatVerdict", "RuntimePlugin", "GaePlugin", "GenericPlugin", "select_plugin"]
