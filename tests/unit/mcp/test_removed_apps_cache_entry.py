"""Legacy host account cache arguments cannot reactivate the removed platform."""

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.connection import MCPConnection
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


@pytest.mark.parametrize("entry", ["runtime", "manager", "connection"])
def test_old_account_cache_input_fails_before_allocation(tmp_path, monkeypatch, entry):
    def forbidden(*args, **kwargs):
        pytest.fail("legacy cache arguments must not allocate runtime or network resources")

    monkeypatch.setattr("corki.core.runtime.SQLiteSessionRepository", forbidden)
    monkeypatch.setattr("corki.mcp.manager.create_client", forbidden)
    with pytest.raises(TypeError, match="unexpected keyword argument.*apps_cache_context"):
        if entry == "runtime":
            LangGraphRuntime.create(
                settings=CorkiSettings(tmp_path), mcp_apps_cache_context=object()
            )
        elif entry == "manager":
            MCPManager((), ToolRegistry(), apps_cache_context=object())
        else:
            MCPConnection(object(), forbidden, apps_cache_context=object())
