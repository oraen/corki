import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from corki.storage.admin import DatabaseAdmin, split_statements
from corki.storage.query_workspace import QueryWorkspace


@pytest.fixture
def workspace(tmp_path):
    with sqlite3.connect(tmp_path / "test.db") as db:
        db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT UNIQUE)")
        db.execute("INSERT INTO items VALUES(1,'initial')")
    return QueryWorkspace(tmp_path)


def create(workspace):
    return workspace.mutate({"action": "create", "database": "test.db"})


def run(workspace, doc, sql, confirmed=False, identity=None):
    return workspace.mutate(
        {
            "action": "run",
            "id": doc["id"],
            "sql": sql,
            "confirmed": confirmed,
            "result": identity or uuid.uuid4().hex,
        }
    )


def test_save_reopen_and_delete(workspace):
    doc = create(workspace)
    saved = workspace.mutate(
        {**doc, "action": "save", "name": "统计查询", "sql": "SELECT * FROM items"}
    )
    assert QueryWorkspace(workspace.home).listing()["queries"][0]["name"] == "统计查询"
    with pytest.raises(ValueError, match="其他页面"):
        workspace.mutate({**doc, "action": "save"})
    result = run(workspace, saved, saved["sql"])["result"]
    assert workspace.result(doc["id"], result["id"])["results"][0]["rows"] == [[1, "initial"]]
    path = workspace.path(doc["id"]) / result["id"]
    assert (path / "data.json").exists()
    workspace.mutate({"action": "close-result", "id": doc["id"], "result": result["id"]})
    assert not path.exists()
    run(workspace, saved, "SELECT 2")
    workspace.mutate({**saved, "action": "close"})
    assert not workspace.path(doc["id"]).exists()
    assert workspace.listing() == {"queries": []}
    with pytest.raises(ValueError):
        workspace.mutate({**saved, "action": "save"})


def test_batch_atomic_confirmation_and_idempotence(workspace):
    doc = create(workspace)
    statement = "UPDATE items SET value=value||';changed'; SELECT value FROM items;"
    assert run(workspace, doc, statement) == {"requires_confirmation": True}
    assert not workspace.listing()["queries"][0]["results"]
    with sqlite3.connect(workspace.home / "test.db") as db:
        assert db.execute("SELECT value FROM items").fetchone()[0] == "initial"
    identity = uuid.uuid4().hex
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(
            pool.map(lambda _: run(workspace, doc, statement, True, identity), range(2))
        )
    assert outcomes[0] == outcomes[1]
    assert len(workspace.listing()["queries"][0]["results"]) == 1
    result = workspace.result(doc["id"], identity)
    assert result["results"][1]["rows"] == [["initial;changed"]]
    failure = run(
        workspace, doc, "UPDATE items SET value='broken'; SELECT missing FROM items;", True
    )
    assert failure["result"]["status"] == "failed"
    assert "回滚" in workspace.result(doc["id"], failure["result"]["id"])["error"]
    with sqlite3.connect(workspace.home / "test.db") as db:
        assert db.execute("SELECT value FROM items").fetchone()[0] == "initial;changed"


def test_sql_split_quotes_comments_and_trigger(workspace):
    sql = "SELECT ';' AS x; -- semicolon ;\n SELECT 'a;b' AS y; /* trailing ; */"
    result = DatabaseAdmin(workspace.home).query({"database": "test.db", "sql": sql})
    assert [r["rows"] for r in result["results"]] == [[[";"]], [["a;b"]]]
    trigger = (
        "CREATE TRIGGER change_log AFTER INSERT ON items BEGIN "
        "UPDATE items SET value=value||';' WHERE id=new.id; SELECT 1; END; SELECT 2;"
    )
    assert len(split_statements(trigger)) == 2
    DatabaseAdmin(workspace.home).query({"database": "test.db", "sql": trigger, "confirmed": True})
    for text in ("-- only comment", "SELECT 'unfinished", "SELECT 1;" * 51):
        with pytest.raises(ValueError):
            split_statements(text)


def test_failed_result_and_recovery_marker(workspace):
    doc = create(workspace)
    outcome = run(workspace, doc, "SELECT absent FROM items")["result"]
    assert outcome["status"] == "failed"
    assert QueryWorkspace(workspace.home).result(doc["id"], outcome["id"])["error"]
    identity = uuid.uuid4().hex
    path = workspace.path(doc["id"]) / identity
    workspace.write(path / "meta.json", {"id": identity, "created": 0, "status": "unknown"})
    workspace.write(path / "data.json", {"status": "unknown", "error": "interrupted"})
    assert run(workspace, doc, "DELETE FROM items", True, identity)["result"]["status"] == "unknown"
    with pytest.raises(ValueError):
        workspace.result(doc["id"], "../escape")


def test_result_write_failure_does_not_repeat_committed_sql(workspace, monkeypatch):
    doc = create(workspace)
    identity = uuid.uuid4().hex
    original = workspace.write

    def fail_completed(path, data):
        if path.name == "data.json" and data.get("status") == "completed":
            raise OSError("simulated storage failure")
        original(path, data)

    monkeypatch.setattr(workspace, "write", fail_completed)
    with pytest.raises(OSError):
        run(workspace, doc, "UPDATE items SET value=value||'x'", True, identity)
    monkeypatch.setattr(workspace, "write", original)
    recovered = run(workspace, doc, "UPDATE items SET value=value||'x'", True, identity)
    assert recovered["result"]["status"] == "unknown"
    with sqlite3.connect(workspace.home / "test.db") as db:
        assert db.execute("SELECT value FROM items").fetchone()[0] == "initialx"
