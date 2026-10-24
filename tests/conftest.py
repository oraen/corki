import sys
import tempfile
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from corki.mcp.tool_catalog_cache import MCPToolCatalogCache


def pytest_ignore_collect(collection_path, config):
    """The pexpect PTY suite uses Unix forkpty; Windows uses ConPTY tests."""
    return (
        sys.platform == "win32"
        and collection_path.parent.name == "e2e"
        and collection_path.name.startswith("test_")
        and collection_path.name.endswith("_pty.py")
    )


@pytest.fixture
def headless_terminal():
    """Explicit terminal adapter for display tests rendering to StringIO.

    These tests exercise Corki rendering, not a system console. Real keyboard
    and console behavior is covered separately by PTY/ConPTY integration tests.
    """
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        yield


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
