"""Source-only installation and migration; all index generation uses C&E."""

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.knowledge.builtin import bundled_document, install_builtin_knowledge
from corki.knowledge.extraction import extract
from corki.knowledge.store import KnowledgeStore
from corki.knowledge.tools import register_knowledge_tools
from corki.models.types import ModelCompleted, ModelTextDelta
from corki.tools.registry import ToolRegistry


def test_source_only_and_stable_install(tmp_path):
    content = bundled_document()
    assert len(content.encode()) < 256 * 1024
    doc = install_builtin_knowledge(tmp_path)
    store = KnowledgeStore(tmp_path)
    assert doc["enabled"] == 0 and doc["status"] == "pending"
    assert store.source(doc["path"]).read_text(encoding="utf-8") == content
    assert install_builtin_knowledge(tmp_path) == doc
    assert store.search()["results"] == []
    assert not (Path(__file__).parents[2] / "scripts/build_builtin_knowledge.py").exists()


def test_ce_index_survives_install_and_disable_reenable(tmp_path):
    doc = install_builtin_knowledge(tmp_path)
    store = KnowledgeStore(tmp_path)
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            assert "相邻文本块可以合并" in request.instructions
            blocks = json.loads(request.items[0].content)["blocks"]
            yield ModelTextDelta(json.dumps({"segments": [{
                "start": 1, "end": len(blocks), "title": "Corki 使用说明",
                "summary": "配置、工具和使用方法", "keywords": ["Corki"]}]}))
            yield ModelCompleted(())

        async def aclose(self):
            pass

    settings = CorkiSettings(working_directory=tmp_path)
    job = store.claim(doc["id"], "fixture/model", prepare=True)
    processing = install_builtin_knowledge(tmp_path)
    assert processing["job"] == job and processing["status"] == "processing"
    asyncio.run(extract(Model(), settings, store, doc, job, bundled_document()))
    ready = install_builtin_knowledge(tmp_path)
    assert ready["status"] == "ready" and ready["enabled"]
    with store.connect() as db:
        before = [tuple(row) for row in db.execute(
            "SELECT * FROM segments WHERE document_id=? ORDER BY ordinal", (doc["id"],))]
    assert "".join(row[6] for row in before) == bundled_document()
    count = len(requests)
    store.set_enabled(doc["id"], False)
    assert not install_builtin_knowledge(tmp_path)["enabled"]
    store.set_enabled(doc["id"], True)
    assert install_builtin_knowledge(tmp_path)["status"] == "ready"
    assert len(requests) == count
    with store.connect() as db:
        after = [tuple(row) for row in db.execute(
            "SELECT * FROM segments WHERE document_id=? ORDER BY ordinal", (doc["id"],))]
    assert before == after


def test_legacy_index_migration_and_source_update(tmp_path, monkeypatch):
    import corki.knowledge.builtin as builtin

    doc = install_builtin_knowledge(tmp_path)
    store = KnowledgeStore(tmp_path)
    user = store.root / "user.md"
    user.write_text("# User\nPrivate local document")
    user_doc = store.sync(user, "User", user.read_text())
    with store.connect() as db:
        db.execute("UPDATE documents SET status='ready',enabled=1,model='builtin-reviewed:old' "
                   "WHERE id=?", (doc["id"],))
        db.execute("INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("old", doc["id"], 0, "old", "old", "[]", "old", 0, 3, 1, 1))
        db.execute("INSERT INTO batches VALUES(?,?,?)", (doc["id"], 0, "[]"))
    migrated = install_builtin_knowledge(tmp_path)
    assert migrated["id"] == doc["id"]
    assert not migrated["enabled"] and migrated["status"] == "pending"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM batches").fetchone()[0] == 0
    assert install_builtin_knowledge(tmp_path) == migrated
    monkeypatch.setattr(builtin, "bundled_document", lambda: "# Updated manual")
    updated = install_builtin_knowledge(tmp_path)
    assert updated["id"] == doc["id"] and updated["status"] == "pending"
    assert not updated["enabled"]
    assert store.source(doc["path"]).read_text() == "# Updated manual"
    assert store.sync(user, "User", user.read_text()) == user_doc


def test_concurrent_install_is_one_document(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        docs = list(pool.map(lambda _: install_builtin_knowledge(tmp_path), range(8)))
    assert len({d["id"] for d in docs}) == 1
    with KnowledgeStore(tmp_path).connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_symlink_destination_rejected(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    home = tmp_path / "home"
    (home / "manager").mkdir(parents=True)
    (home / "manager/knowledge").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="inside Corki home"):
        install_builtin_knowledge(home)
    assert list(external.iterdir()) == []


def test_runtime_registration_and_manager_classification(tmp_path):
    import importlib.util

    register_knowledge_tools(ToolRegistry(), tmp_path)
    path = Path(__file__).resolve().parents[2] / "manager/server.py"
    spec = importlib.util.spec_from_file_location("builtin_manual_manager", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manager = module.Store(tmp_path, tmp_path, compatibility_home=tmp_path)
    item = next(f for f in manager.files() if f["name"] == "corki_docs")
    assert item["group"] == "builtin" and not item["writable"]
    assert not item["enabled"] and item["index_status"] == "pending"


def test_manual_json_examples_load_without_network(tmp_path):
    from corki.config import CorkiSettings
    from corki.tools.custom import validate_custom_tool

    examples = re.findall(r"```json\n(.*?)\n```", bundled_document(), re.S)
    assert len(examples) >= 3
    for raw in examples:
        data = json.loads(raw)
        if data.get("transport") == "process":
            assert validate_custom_tool(data)["name"] == "echo_text"
        else:
            config = tmp_path / "settings.json"
            config.write_text(raw, encoding="utf-8")
            CorkiSettings.for_directory(tmp_path, config_file=config)


def test_ephemeral_registration_does_not_create_storage(tmp_path):
    home = tmp_path / "home"
    register_knowledge_tools(ToolRegistry(), home, install_builtin=False)
    assert not home.exists()


def test_sync_preserves_installed_builtin_identity(tmp_path):
    original = install_builtin_knowledge(tmp_path)
    store = KnowledgeStore(tmp_path)
    synced = store.sync(store.source(original["path"]), original["title"], bundled_document())
    assert synced["id"] == original["id"]
    assert synced["path"] == ".system/corki_docs.md"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_legacy_windows_path_keeps_identity_and_disabled_state(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    import corki.knowledge.store as module

    original = install_builtin_knowledge(tmp_path)
    store = KnowledgeStore(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE documents SET path=? WHERE id=?",
                   (".system\\corki_docs.md", original["id"]))
    # Exercise only the path compatibility branch, not Windows filesystem APIs.
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    restored = install_builtin_knowledge(tmp_path)
    assert restored["id"] == original["id"]
    assert restored["path"] == ".system/corki_docs.md"
    assert restored["enabled"] == 0
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
