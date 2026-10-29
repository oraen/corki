"""Fast local secret reads use disposable data and never start a subprocess."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "channel_gateway_client_test", Path(__file__).parents[1] / "channel_gateway.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_secret_read_is_local_current_and_field_scoped(tmp_path, monkeypatch):
    client = module.ChannelGateway(tmp_path)
    path = tmp_path / "gateway" / "gateway.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE connections(id TEXT PRIMARY KEY,type_id TEXT,config TEXT);"
            "CREATE TABLE channels(id TEXT PRIMARY KEY,manifest TEXT);"
        )
        manifest = {"fields": [{"key": "secret", "secret": True}, {"key": "name"}]}
        db.execute("INSERT INTO channels VALUES(?,?)", ("test", json.dumps(manifest)))
        db.execute("INSERT INTO connections VALUES(?,?,?)", (
            "account", "test", json.dumps({"secret": "test-value", "name": "demo"})
        ))
    def no_process(*args, **kwargs):
        pytest.fail("secret reads must not spawn processes")
    monkeypatch.setattr(module.subprocess, "run", no_process)
    request = {"action": "reveal-secret", "id": "account", "field": "secret"}
    assert client.request(request) == {"value": "test-value"}
    with sqlite3.connect(path) as db:
        db.execute("UPDATE connections SET config=?", (json.dumps({"secret": "updated"}),))
    assert client.request(request) == {"value": "updated"}
    for change in ({"field": "name"}, {"id": "unknown"}, {"id": "' OR 1=1 --"}, {"field": []}):
        with pytest.raises(ValueError):
            client.request({**request, **change})


def test_missing_database_is_not_created(tmp_path):
    client = module.ChannelGateway(tmp_path)
    with pytest.raises(ValueError, match="无法读取本地密钥"):
        client.request({"action": "reveal-secret", "id": "account", "field": "secret"})
    assert not (tmp_path / "gateway").exists()
