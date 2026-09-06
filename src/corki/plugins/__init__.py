"""Local plugin discovery and contribution API."""

from corki.plugins.manager import PluginManager
from corki.plugins.models import LoadedPlugin, PluginManifest

__all__ = ["LoadedPlugin", "PluginManager", "PluginManifest"]
