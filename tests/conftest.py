import tempfile
from pathlib import Path

import pytest

from corki.mcp.tool_catalog_cache import MCPToolCatalogCache


@pytest.fixture(autouse=True)
def isolated_mcp_catalog_host(monkeypatch):
    """Each test is a host; its Runtimes still share one process catalog."""
    monkeypatch.setattr("corki.mcp.manager.SHARED_TOOL_CATALOG_CACHE", MCPToolCatalogCache())


@pytest.fixture
def memory_worker_state_dirs(monkeypatch):
    """Track disposable worker state separately from its persistent memory cwd."""
    directories = []
    original = tempfile.mkdtemp

    def allocate(*args, **kwargs):
        value = original(*args, **kwargs)
        if kwargs.get("prefix") == "corki-memory-agent-":
            directories.append(Path(value))
        return value

    monkeypatch.setattr(tempfile, "mkdtemp", allocate)
    return directories
