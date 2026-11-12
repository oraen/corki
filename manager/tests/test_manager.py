"""Test real HTTP boundaries and config mutations against disposable local data."""

import importlib.util
import json
import os
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "manager_server", Path(__file__).parents[1] / "server.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def assert_private_file(path):
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        return
    from corki.platforms.windows_acl import WindowsPrivateACL
    from corki.platforms.windows_file_handles import WindowsFiles

    files = WindowsFiles()
    handle = files.open(path, access=0x20000)
    try:
        WindowsPrivateACL().verify(handle)
    finally:
        files.close(handle)


def test_snapshot_supplies_host_interpreter_for_process_examples(store):
    import sys

    assert store.snapshot()["runtime"]["python"] == sys.executable


@pytest.fixture
def store(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text("""// keep this comment
{"default":{"provider":"local","model":"model-a"},
 "providers":{"local":{"base_url":"http://127.0.0.1:17001","api_key":"private-token-123","api_mode":"responses","models":{"model-a":{"context_window":131072,"extra":"preserved"}}}},
 "unrelated":{"enabled":true}}
""", encoding="utf-8")
    return module.Store(home, tmp_path / "work", tmp_path / "compat")


def change(store, **data):
    return store.mutate({"revision": module.revision(store.config_text()), **data})


def config(store):
    return module.document.loads(store.config_text())


def test_provider_edit_preserves_secret_models_comments_and_backup(store):
    change(
        store,
        kind="providers",
        action="save",
        id="local",
        values={"base_url": "https://example.com", "api_mode": "responses", "api_key": ""},
    )
    p = config(store)["providers"]["local"]
    assert p["api_key"] == "private-token-123"
    assert p["models"]["model-a"]["extra"] == "preserved"
    assert "// keep this comment" in store.config_text()
    assert config(store)["unrelated"]["enabled"]
    assert list((store.home / ".manager-backups").iterdir())
    assert_private_file(store.config)


def test_snapshot_does_not_expose_api_keys(store):
    result = store.snapshot()
    assert result["providers"][0]["has_key"]
    assert "private-token-123" not in json.dumps(result)
    assert result["errors"] == []


def test_concurrent_config_edit_is_rejected(store):
    rev = module.revision(store.config_text())
    store.config.write_text(store.config_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="更新"):
        store.mutate({"revision": rev, "kind": "providers", "action": "delete", "id": "other"})


@pytest.mark.parametrize(
    "kind,id,provider", [("providers", "local", None), ("models", "model-a", "local")]
)
def test_default_cannot_be_deleted(store, kind, id, provider):
    with pytest.raises(ValueError, match="默认"):
        change(store, kind=kind, action="delete", id=id, provider=provider)


def test_model_create_select_and_delete(store):
    change(
        store,
        kind="models",
        action="save",
        id="model-b",
        provider="local",
        values={"context_window": 262144, "reasoning_effort": "medium"},
    )
    change(store, kind="models", action="default", id="model-b", provider="local")
    change(store, kind="models", action="delete", id="model-a", provider="local")
    assert config(store)["default"] == {"provider": "local", "model": "model-b"}
    assert list(config(store)["providers"]["local"]["models"]) == ["model-b"]


def test_knowledge_crud_conflict_and_recycle(store):
    change(store, kind="knowledge", action="create", name="notes", content="# Notes\nhello")
    file = next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "user")
    store.mutate(
        {
            "kind": "knowledge",
            "action": "save",
            "id": file["id"],
            "revision": file["revision"],
            "content": "# Notes\nupdated",
        }
    )
    with pytest.raises(ValueError, match="更新"):
        store.mutate(
            {
                "kind": "knowledge",
                "action": "save",
                "id": file["id"],
                "revision": file["revision"],
                "content": "stale",
            }
        )
    file = store.read_file(file["id"])
    store.mutate(
        {"kind": "knowledge", "action": "delete", "id": file["id"], "revision": file["revision"]}
    )
    assert all(f["name"] == "corki_docs" for f in store.files())
    assert not Path(file["path"]).exists()
    assert not (store.home / "manager/trash").exists()
    assert not (store.home / "manager/knowledge-versions" / file["document_id"]).exists()


def test_knowledge_groups_toggle_and_delete_cleanup(store):
    change(store, kind="knowledge", action="create", name="notes", content="# Notes\nhello")
    builtin = store.home / "manager/knowledge/.system/guide.md"
    builtin.parent.mkdir(parents=True, exist_ok=True)
    builtin.write_text("# Guide\nBuilt-in reference", encoding="utf-8")
    files = {f["group"]: f for f in store.files() if f["kind"] == "knowledge"}
    assert files["user"]["writable"] and not files["user"]["enabled"]
    assert not files["builtin"]["writable"] and not files["builtin"]["enabled"]

    # Exercise a state change from an explicitly enabled document without a model call.
    store.knowledge.set_enabled(files["user"]["document_id"], True)
    store.write_knowledge_state(set())
    original_revision = store.snapshot()["knowledge_revision"]
    store.mutate({
        "kind": "knowledge", "action": "toggle", "id": files["user"]["id"],
        "knowledge_revision": original_revision, "values": {"enabled": False},
    })
    assert not next(f for f in store.files() if f["id"] == files["user"]["id"])["enabled"]
    assert (store.home / "manager/knowledge-state.json").read_text(encoding="utf-8").find("notes.md") >= 0
    with pytest.raises(ValueError, match="刷新"):
        store.mutate({
            "kind": "knowledge", "action": "toggle", "id": files["builtin"]["id"],
            "knowledge_revision": original_revision, "values": {"enabled": False},
        })
    store.mutate({
        "kind": "knowledge", "action": "toggle", "id": files["builtin"]["id"],
        "knowledge_revision": store.snapshot()["knowledge_revision"],
        "values": {"enabled": False},
    })
    assert not next(f for f in store.files() if f["id"] == files["builtin"]["id"])["enabled"]
    with pytest.raises(ValueError, match="只读"):
        store.mutate({
            "kind": "knowledge", "action": "delete", "id": files["builtin"]["id"],
            "revision": files["builtin"]["revision"],
        })
    user = next(f for f in store.files() if f["group"] == "user")
    store.mutate({
        "kind": "knowledge", "action": "delete", "id": user["id"],
        "revision": user["revision"],
    })
    assert "notes.md" not in (store.home / "manager/knowledge-state.json").read_text(encoding="utf-8")


def test_short_gateway_key_does_not_hide_user_document_or_make_it_readonly(store):
    store.config.write_text(json.dumps({
        "providers": {"local": {"base_url": "http://127.0.0.1:17001", "api_key": "note"}}
    }), encoding="utf-8")
    change(store, kind="knowledge", action="create", name="note", content="# note\nnote123")
    item = next(f for f in store.snapshot()["files"] if f["kind"] == "knowledge" and f["group"] == "user")
    assert item["name"] == "note"
    assert Path(item["path"]).name == "note.md"
    assert item["writable"]
    detail = store.read_file(item["id"])
    assert detail["content"] == "# note\nnote123"
    assert detail["writable"]


@pytest.mark.parametrize("name", ["../escape", "a/b", ".", ""])
def test_document_path_traversal_is_rejected(store, name):
    with pytest.raises(ValueError):
        change(store, kind="knowledge", action="create", name=name, content="test")


def test_shared_skill_edit_and_toggle_is_local(store):
    path = store.compatibility_home / ".agents/skills/shared/SKILL.md"
    path.parent.mkdir(parents=True)
    content = "---\nname: shared\ndescription: Shared skill\n---\nContent"
    path.write_text(content, encoding="utf-8")
    file = store.files()[0]
    assert file["group"] == "public"
    assert store.read_file(file["id"])["writable"]
    change(
        store,
        kind="skills",
        action="save",
        id=file["id"],
        revision=file["revision"],
        content=content + " changed",
    )
    change(store, kind="skills", action="toggle", id=file["id"], values={"enabled": False})
    assert not store.snapshot()["files"][0]["enabled"]
    assert path.read_text(encoding="utf-8").endswith("changed")
    assert not (store.compatibility_home / ".agents/settings.json").exists()


def test_memory_with_secret_is_redacted_and_readonly(store):
    path = store.home / "memories/MEMORY.md"
    path.parent.mkdir()
    path.write_text("token private-token-123", encoding="utf-8")
    file = store.read_file(next(f for f in store.files() if f["kind"] == "memories")["id"])
    assert file["content"] == "token [已隐藏]"
    assert not file["writable"]


def test_mcp_config_does_not_execute_and_retains_auth(store):
    change(
        store,
        kind="tools",
        action="save",
        id="reader",
        values={"transport": "http", "url": "https://example.com/mcp"},
    )
    change(store, kind="tools", action="toggle", id="reader", values={"enabled": False})
    assert not config(store)["mcp"]["servers"]["reader"]["enabled"]
    change(store, kind="tools", action="delete", id="reader")
    assert config(store)["mcp"]["servers"] == {}


def test_http_requires_same_origin_and_custom_header(store):
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.store = store
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as c:
            assert c.get("/").status_code == 200
            assert c.get("/api/snapshot").status_code == 200
            key = c.get("/api/provider-key?id=local")
            assert key.json() == {"api_key": "private-token-123"}
            assert key.headers["Cache-Control"] == "no-store"
            assert c.get("/api/provider-key?id=missing").status_code == 400
            assert (
                c.get(
                    "/api/provider-key?id=local", headers={"Origin": "https://attacker.example"}
                ).status_code
                == 403
            )

            assert c.get("/api/snapshot", headers={"Host": "attacker.example"}).status_code == 403
            assert c.post("/api/mutate", json={}).status_code == 403
            assert (
                c.post(
                    "/api/mutate",
                    json={},
                    headers={"X-Corki-Manager": "1", "Origin": "https://attacker.example"},
                ).status_code
                == 403
            )
            response = c.post(
                "/api/mutate",
                headers={"X-Corki-Manager": "1"},
                json={
                    "revision": module.revision(store.config_text()),
                    "kind": "knowledge",
                    "action": "create",
                    "name": "http-doc",
                    "content": "# HTTP document",
                },
            )
            assert response.status_code == 200
            assert any(f["name"] == "HTTP document" for f in c.get("/api/snapshot").json()["files"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_config_versions_save_rollback_and_corki_loader(store):
    from corki.config.settings import CorkiSettings

    original = store.config_text()
    first = store.config_document()
    edited = module.document.set_value(
        original, ("providers", "local", "models", "model-a", "context_window"), 262144
    )
    change(store, kind="config", action="save", content=edited)
    loaded = CorkiSettings.for_directory(store.workspace, config_file=store.config)
    assert loaded.model_context_info("model-a").context_window == 262144
    history = store.config_document()
    assert len(history["versions"]) == 2
    change(store, kind="config", action="restore", id=first["versions"][0]["id"])
    assert store.config_text() == original
    assert len(store.config_document()["versions"]) == 3
    for path in store.config_versions().glob("*.json"):
        assert_private_file(path)


@pytest.mark.parametrize(
    "content", ["{broken", "[]", '{"a":1,"a":2}', '{"x":NaN}', "", '{"providers":[]}']
)
def test_invalid_config_never_changes_active_file(store, content):
    original = store.config_text()
    with pytest.raises(ValueError):
        change(store, kind="config", action="save", content=content)
    assert store.config_text() == original


def test_history_detects_external_edits_and_conflicts(store):
    first = store.config_document()
    store.config.write_text(store.config_text() + "\n", encoding="utf-8")
    assert len(store.config_document()["versions"]) == 2
    with pytest.raises(ValueError, match="冲突"):
        store.mutate(
            dict(
                kind="config",
                action="restore",
                id=first["versions"][0]["id"],
                revision=first["revision"],
            )
        )


def test_structured_edits_are_versioned(store):
    change(
        store,
        kind="models",
        action="save",
        id="model-a",
        provider="local",
        values={"context_window": 262144, "reasoning_effort": "medium"},
    )
    assert len(store.config_document()["versions"]) == 2


def test_skills_excludes_codex_and_hidden_backups_but_lists_corki_system(store):
    roots = [
        store.compatibility_home / ".codex/skills",
        store.workspace / ".codex/skills",
        store.home / "skills/.system.previous",
        store.home / "skills/.system",
    ]
    for root in roots:
        path = root / "sample/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: sample\ndescription: Example\n---\nContent", encoding="utf-8")
    files = [f for f in store.files() if f["kind"] == "skills"]
    assert len(files) == 1
    assert files[0]["path"] == str(store.home / "skills/.system/sample/SKILL.md")
    assert files[0]["source"] == "内置"
    assert not files[0]["writable"]


def skill_upload(entries=None, **kwargs):
    import base64

    return {
        "kind": "skills",
        "action": "import",
        "files": [
            {"path": name, "data": base64.b64encode(content).decode()}
            for name, content in (
                entries
                or [
                    (
                        "example/SKILL.md",
                        b"---\nname: example\ndescription: Test import\n---\nInstructions",
                    )
                ]
            )
        ],
        **kwargs,
    }


def test_import_preview_package_and_runtime_discovery(store):
    from corki.skills.service import SkillService

    data = skill_upload()
    data["files"] += skill_upload(
        [("example/references/guide.md", b"Guide"), ("example/assets/icon.png", b"\x89PNG\x00")]
    )["files"]
    preview = store.mutate({**data, "action": "validate-import"})
    target = store.compatibility_home / ".agents/skills/example"
    assert not target.exists()
    assert len(preview["files"]) == 3
    store.mutate(data)
    assert (target / "assets/icon.png").read_bytes() == b"\x89PNG\x00"
    service = SkillService(
        home=store.home,
        project_root=store.workspace,
        compatibility_home=store.compatibility_home,
        bundled_enabled=False,
    )
    skill = next(s for s in service.snapshot(store.workspace).skills if s.name == "example")
    assert service.read(skill, "references/guide.md") == "Guide"
    with pytest.raises(ValueError, match="同名"):
        store.mutate({**data, "action": "validate-import"})
    with pytest.raises(ValueError, match="同名"):
        store.mutate(data)
    with pytest.raises(ValueError, match="自定义"):
        store.mutate({**data, "name": "example-copy"})
    assert not (target.parent / "example-copy").exists()


@pytest.mark.parametrize("group", ["corki", "public"])
def test_import_skill_to_selected_group(store, group):
    data = skill_upload(group=group)
    target = (
        store.home / "skills/example"
        if group == "corki"
        else store.compatibility_home / ".agents/skills/example"
    )
    other = (
        store.compatibility_home / ".agents/skills/example"
        if group == "corki"
        else store.home / "skills/example"
    )
    preview = store.mutate({**data, "action": "validate-import"})
    assert preview["group"] == group
    assert preview["path"] == str(target)
    assert not target.exists()
    result = store.mutate(data)
    assert result["group"] == group
    assert (target / "SKILL.md").is_file()
    assert not other.exists()
    assert next(f for f in store.files() if f["name"] == "example")["group"] == group
    with pytest.raises(ValueError, match="同名"):
        store.mutate({**data, "action": "validate-import"})


def test_import_skill_rejects_invalid_group(store):
    with pytest.raises(ValueError, match="导入位置|扩展 Skills"):
        store.mutate(skill_upload(group="builtin"))
    assert not (store.home / "skills").exists()


@pytest.mark.parametrize(
    "path", ["../outside", "/outside", "foo/../../outside", "foo\\outside"]
)
def test_import_rejects_unsafe_paths_without_partial_writes(store, path):
    data = skill_upload()
    data["files"] += skill_upload([(path, b"bad")])["files"]
    with pytest.raises(ValueError):
        store.mutate(data)
    assert not (store.compatibility_home / ".agents/skills").exists()


@pytest.mark.parametrize(
    "entries",
    [
        [("README.md", b"none")],
        [("SKILL.md", b"invalid")],
        [("a/SKILL.md", b"one"), ("b/SKILL.md", b"two")],
        [("SKILL.md", b"x"), ("skill.MD", b"x")],
        [("SKILL.md", b"x" * (128 * 1024 + 1))],
    ],
)
def test_import_rejects_invalid_packages(store, entries):
    with pytest.raises(ValueError):
        store.mutate(skill_upload(entries))


def test_zip_import_preserves_executable_and_rejects_symlinks(store):
    import base64
    import io
    import zipfile

    def archive(link=False):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("zipped/SKILL.md", "---\nname: zipped\ndescription: Zip test\n---\nRun")
            entry = zipfile.ZipInfo("zipped/scripts/run.sh")
            entry.external_attr = (0o120777 if link else 0o100755) << 16
            z.writestr(entry, b"#!/bin/sh\necho test")
        return base64.b64encode(stream.getvalue()).decode()

    with pytest.raises(ValueError, match="链接"):
        store.mutate(dict(kind="skills", action="import", archive=archive(True)))
    store.mutate(dict(kind="skills", action="import", archive=archive()))
    script = store.compatibility_home / ".agents/skills/zipped/scripts/run.sh"
    assert script.read_bytes() == b"#!/bin/sh\necho test"
    if os.name != "nt":  # NTFS has no POSIX executable bit for shell scripts.
        assert script.stat().st_mode & 0o100


def test_three_skill_groups_are_distinct(store):
    for path in (
        store.home / "skills/.system/builtin",
        store.home / "skills/private",
        store.compatibility_home / ".agents/skills/public",
    ):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\nname: {path.name}\ndescription: Test\n---\nBody", encoding="utf-8")
    files = [f for f in store.files() if f["kind"] == "skills"]
    assert {f["name"]: f["group"] for f in files} == {
        "builtin": "builtin",
        "private": "corki",
        "public": "public",
    }
    assert not next(f for f in files if f["name"] == "builtin")["writable"]


@pytest.fixture
def skill_package(store):
    root = store.compatibility_home / ".agents/skills/workbench"
    (root / "scripts").mkdir(parents=True)
    (root / "references/nested").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: workbench\ndescription: Package test\n---\n# Guide\n"
    , encoding="utf-8")
    (root / "scripts/run.py").write_bytes(b'#!/usr/bin/env python3\r\nprint("hello")\r\n')
    (root / "scripts/run.py").chmod(0o755)
    (root / "references/nested/guide.md").write_text("# Reference\n\n**Important**", encoding="utf-8")
    (root / "asset.bin").write_bytes(b"\x00\xff\x80")
    return root, store.files()[0]["id"]


def test_skill_package_read_save_conflict_export(store, skill_package):
    import io
    import zipfile

    root, identity = skill_package
    package = store.skill_package(identity)
    assert {e["path"] for e in package["entries"]} >= {
        "scripts",
        "scripts/run.py",
        "references/nested/guide.md",
    }
    doc = store.read_skill_resource(identity, "scripts/run.py")
    assert doc["writable"] and "\r\n" in doc["content"]
    assert store.read_skill_resource(identity, "asset.bin")["type"] == "binary"
    edited = doc["content"].replace("hello", "saved")
    store.mutate(
        dict(
            kind="skills",
            action="save-resource",
            id=identity,
            path="scripts/run.py",
            revision=doc["revision"],
            content=edited,
        )
    )
    assert (root / "scripts/run.py").read_bytes() == edited.encode()
    if os.name != "nt":
        assert (root / "scripts/run.py").stat().st_mode & 0o111 == 0o111
    with pytest.raises(ValueError, match="修改"):
        store.mutate(
            dict(
                kind="skills",
                action="save-resource",
                id=identity,
                path="scripts/run.py",
                revision=doc["revision"],
                content="overwrite",
            )
        )
    body, name = store.export_skill(identity)
    assert name == "workbench.zip"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.read("workbench/scripts/run.py") == edited.encode()
        assert archive.read("workbench/asset.bin") == b"\x00\xff\x80"
        if os.name != "nt":
            assert archive.getinfo("workbench/scripts/run.py").external_attr >> 16 & 0o111
        assert not any(".manager-backups" in n for n in archive.namelist())
        assert (
            archive.read("workbench/references/nested/guide.md")
            == (root / "references/nested/guide.md").read_bytes()
        )


@pytest.mark.parametrize(
    "relative",
    ["../settings.json", "/etc/passwd", "scripts/../../SKILL.md", "scripts", "missing.md"],
)
def test_skill_resources_block_paths(store, skill_package, relative):
    with pytest.raises(ValueError):
        store.read_skill_resource(skill_package[1], relative)


def test_skill_resources_block_links_and_invalid_main(store, skill_package):
    root, identity = skill_package
    (root / "linked").symlink_to(store.config)
    (root / "dirlink").symlink_to(store.home, target_is_directory=True)
    for path in ["linked", "dirlink/settings.json"]:
        with pytest.raises(ValueError):
            store.read_skill_resource(identity, path)
    doc = store.read_skill_resource(identity, "SKILL.md")
    with pytest.raises(ValueError):
        store.mutate(
            dict(
                kind="skills",
                action="save-resource",
                id=identity,
                path="SKILL.md",
                revision=doc["revision"],
                content="invalid",
            )
        )
    assert store.read_skill_resource(identity, "SKILL.md")["content"] == doc["content"]


def test_builtin_resource_readonly_even_through_alias(store):
    root = store.home / "skills/.system/native"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: native\ndescription: Native\n---\n# Guide", encoding="utf-8")
    (root / "scripts/run.sh").write_text("echo native", encoding="utf-8")
    (store.home / "skills/alias").symlink_to(root, target_is_directory=True)
    item = store.files()[0]
    assert not item["writable"] and item["group"] == "builtin"
    doc = store.read_skill_resource(item["id"], "scripts/run.sh")
    assert not doc["writable"]
    with pytest.raises(ValueError, match="只读"):
        store.mutate(
            dict(
                kind="skills",
                action="save-resource",
                id=item["id"],
                path="scripts/run.sh",
                revision=doc["revision"],
                content="changed",
            )
        )


def test_skill_http_resources_and_zip(store, skill_package):
    import io
    import zipfile

    _, identity = skill_package
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.store = store
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            assert client.get("/api/skill-package", params={"id": identity}).json()["entries"]
            resource = client.get(
                "/api/skill-resource", params={"id": identity, "path": "scripts/run.py"}
            )
            assert resource.json()["type"] == "text"
            exported = client.get("/api/skill-export", params={"id": identity})
            assert exported.headers["content-type"] == "application/zip"
            assert "attachment" in exported.headers["content-disposition"]
            assert zipfile.is_zipfile(io.BytesIO(exported.content))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_provider_rename_preserves_runtime_compatibility(store):
    from corki.config.providers import resolve_model_document

    old = config(store)["providers"]["local"]
    change(
        store,
        kind="providers",
        action="save",
        id="local",
        values={"id": "renamed", "base_url": old["base_url"], "api_mode": old["api_mode"]},
    )
    updated = config(store)
    assert "local" not in updated["providers"]
    assert updated["default"] == {"provider": "renamed", "model": "model-a"}
    profile = updated["providers"]["renamed"]
    assert profile["api_key"] == old["api_key"]
    assert profile["models"] == old["models"]
    assert profile["aliases"] == ["local"]
    for name in ("local", "renamed"):
        resolved = resolve_model_document(updated, provider=name, model="model-a")
        assert resolved["provider"]["api_key"] == old["api_key"]
        assert resolved["provider"]["name"] == "local"
    # Renaming back consumes our own alias and preserves the intervening name.
    change(
        store,
        kind="providers",
        action="save",
        id="renamed",
        values={"id": "local", "base_url": old["base_url"], "api_mode": old["api_mode"]},
    )
    assert config(store)["providers"]["local"]["aliases"] == ["renamed"]


@pytest.mark.parametrize("name", ["occupied", "reserved", "", "../bad"])
def test_provider_rename_rejects_collision_and_invalid_name(store, name):
    current = config(store)
    current["providers"]["occupied"] = {"aliases": ["reserved"]}
    store.config.write_text(json.dumps(current), encoding="utf-8")
    before = store.config_text()
    with pytest.raises(ValueError):
        change(
            store,
            kind="providers",
            action="save",
            id="local",
            values={"id": name, "base_url": "https://example.com", "api_mode": "responses"},
        )
    assert store.config_text() == before


def test_provider_rename_updates_legacy_inline_identity(store):
    current = config(store)
    current["provider"] = {"name": "local", "thinking": True}
    store.config.write_text(json.dumps(current), encoding="utf-8")
    change(
        store,
        kind="providers",
        action="save",
        id="local",
        values={"id": "renamed", "base_url": "https://example.com", "api_mode": "responses"},
    )
    assert config(store)["provider"] == {"id": "renamed", "name": "local", "thinking": True}


@pytest.mark.parametrize("shared_model", [True, False])
def test_provider_save_as_default(store, shared_model):
    from corki.config.providers import resolve_model_document

    current = config(store)
    target_model = "model-a" if shared_model else "other-model"
    current["providers"]["target"] = {
        "api_key": "target-secret",
        "models": {target_model: {"context_window": 262144}},
    }
    store.config.write_text(json.dumps(current), encoding="utf-8")
    change(
        store,
        kind="providers",
        action="save",
        id="target",
        make_default=True,
        values={"id": "renamed-target", "base_url": "https://example.com", "api_mode": "responses"},
    )
    updated = config(store)
    assert updated["default"] == {"provider": "renamed-target", "model": target_model}
    assert resolve_model_document(updated)["provider"]["api_key"] == "target-secret"


def test_empty_provider_cannot_be_default(store):
    before = store.config_text()
    with pytest.raises(ValueError, match="添加模型"):
        change(
            store,
            kind="providers",
            action="save",
            id="empty",
            make_default=True,
            values={"base_url": "https://example.com", "api_mode": "responses"},
        )
    assert store.config_text() == before


def test_model_description_save_preserve_clear_and_validation(store):
    from corki.config.providers import resolve_model_document

    values = {"context_window": 131072, "reasoning_effort": "medium"}
    change(
        store,
        kind="models",
        action="save",
        provider="local",
        id="model-a",
        values={**values, "description": "适合日常编码和问答。"},
    )
    assert (
        store.snapshot()["providers"][0]["models"]["model-a"]["description"]
        == "适合日常编码和问答。"
    )
    assert resolve_model_document(config(store))["agent"]["model"] == "model-a"
    change(store, kind="models", action="save", provider="local", id="model-a", values=values)
    assert config(store)["providers"]["local"]["models"]["model-a"]["description"]
    for invalid in (123, "x" * 501):
        before = store.config_text()
        with pytest.raises(ValueError, match="描述"):
            change(
                store,
                kind="models",
                action="save",
                provider="local",
                id="model-a",
                values={**values, "description": invalid},
            )
        assert store.config_text() == before
    change(
        store,
        kind="models",
        action="save",
        provider="local",
        id="model-a",
        values={**values, "description": ""},
    )
    assert config(store)["providers"]["local"]["models"]["model-a"]["description"] == ""


def test_config_history_returns_latest_twenty_without_removing_archives(store):
    ids = [store.archive_config(json.dumps({"version": i}), "test") for i in range(25)]
    result = store.config_document()
    assert len(result["versions"]) == 20
    assert result["versions"][0]["revision"] == module.revision(store.config_text())
    assert result["versions"][1]["id"] == ids[-1]
    assert len(list(store.config_versions().glob("*.json"))) == 26
    assert json.loads(store.config_document(ids[0])["content"]) == {"version": 0}


def skill_toggle_fixture(store, rules):
    path = store.home / "skills/.system/skill-creator/SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: skill-creator\ndescription: fixture\n---\nBody", encoding="utf-8")
    value = config(store)
    value["skills"] = {"config": rules(path)}
    store.config.write_text(json.dumps(value), encoding="utf-8")
    return next(f for f in store.files() if f["path"] == str(path)), path


def test_skill_toggle_removes_enable_rule_and_deduplicates_paths(store):
    item, path = skill_toggle_fixture(
        store,
        lambda p: [
            {"path": str(p), "enabled": True},
            {"path": "skills/.system/skill-creator/SKILL.md", "enabled": False},
            {"path": "/unrelated/SKILL.md", "enabled": False},
        ],
    )
    for enabled in (True, False, False, True):
        change(store, kind="skills", action="toggle", id=item["id"], values={"enabled": enabled})
        rules = config(store)["skills"]["config"]
        assert rules == [{"path": "/unrelated/SKILL.md", "enabled": False}] + (
            [] if enabled else [{"path": str(path), "enabled": False}]
        )
        assert (
            next(f for f in store.snapshot()["files"] if f["id"] == item["id"])["enabled"]
            == enabled
        )


def test_skill_enable_preserves_extra_fields_without_enabled(store):
    item, path = skill_toggle_fixture(
        store,
        lambda p: [
            {"path": str(p), "enabled": False, "custom": {"keep": True}},
            {"path": "/unrelated/SKILL.md", "enabled": False},
        ],
    )
    change(store, kind="skills", action="toggle", id=item["id"], values={"enabled": True})
    rules = config(store)["skills"]["config"]
    assert rules[-1] == {"path": str(path), "custom": {"keep": True}}
    from corki.config.skills import disabled_skill_paths, parse_skill_rules

    disabled = disabled_skill_paths(parse_skill_rules(rules, store.home), ())
    assert Path("/unrelated/SKILL.md").resolve() in disabled
    assert path not in disabled
    change(store, kind="skills", action="toggle", id=item["id"], values={"enabled": False})
    assert config(store)["skills"]["config"][-1] == {
        "path": str(path),
        "custom": {"keep": True},
        "enabled": False,
    }


def test_skill_enable_removes_last_rule(store):
    item, _ = skill_toggle_fixture(store, lambda p: [{"path": str(p), "enabled": False}])
    change(store, kind="skills", action="toggle", id=item["id"], values={"enabled": True})
    assert "config" not in config(store)["skills"]


def test_skill_enable_does_not_override_other_same_name_skills(store):
    item, _ = skill_toggle_fixture(store, lambda p: [{"name": "skill-creator", "enabled": False}])
    before = store.config_text()
    with pytest.raises(ValueError, match="名称规则"):
        change(store, kind="skills", action="toggle", id=item["id"], values={"enabled": True})
    assert store.config_text() == before


def test_knowledge_document_import_formats(store):
    import base64
    import io
    import zipfile

    from reportlab.pdfgen.canvas import Canvas

    pdf = io.BytesIO()
    canvas = Canvas(pdf)
    canvas.drawString(50, 750, "Knowledge PDF content")
    canvas.save()
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>知识正文</w:t></w:r></w:p></w:body></w:document>',
        )
    for filename, raw, expected in [
        ("说明.md", "# 知识正文".encode(), "# 知识正文"),
        ("说明.txt", "知识正文".encode("gb18030"), "知识正文"),
        ("说明.docx", docx.getvalue(), "知识正文"),
        ("说明.pdf", pdf.getvalue(), "Knowledge PDF content"),
    ]:
        result = store.mutate(
            {
                "kind": "knowledge",
                "action": "parse-document",
                "filename": filename,
                "data": base64.b64encode(raw).decode(),
            }
        )
        assert result == {"name": "说明", "content": expected}
    assert not list((store.home / "manager/knowledge").glob("*.md"))
    store.mutate({"kind": "knowledge", "action": "create", **result})
    assert (store.home / "manager/knowledge/说明.md").read_text(encoding="utf-8") == expected
    with pytest.raises(ValueError, match="同名"):
        store.mutate({"kind": "knowledge", "action": "create", **result})


def test_knowledge_document_invalid_and_empty(store):
    import base64

    for name, raw in [
        ("a.exe", b"binary"),
        ("a.pdf", b"broken"),
        ("a.docx", b"broken"),
        ("a.txt", b" "),
    ]:
        with pytest.raises(ValueError):
            store.mutate(
                {
                    "kind": "knowledge",
                    "action": "parse-document",
                    "filename": name,
                    "data": base64.b64encode(raw).decode(),
                }
            )


def test_knowledge_html_extracts_text_not_scripts():
    parser = module.KnowledgeHTML()
    parser.feed(
        "<html><head><title>业务说明</title><style>hidden</style></head><body>"
        "<nav>导航</nav><article><h1>标题</h1><p>你好 &amp; <b>世界</b></p>"
        "<script>bad()</script><p>第二段</p></article></body></html>"
    )
    assert parser.result() == ("业务说明", "标题\n\n你好 & 世界\n\n第二段")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "https://user:pass@example.com/",
        "ftp://example.com/",
    ],
)
def test_knowledge_web_rejects_invalid_addresses(url):
    with pytest.raises(ValueError):
        module.fetch_knowledge_page(url)


def test_knowledge_web_fetch_and_redirect_validation(monkeypatch):
    from email.message import Message

    class Response:
        status = 200
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"

        def read(self, limit):
            return "<title>网页标题</title><article><p>网页正文</p></article>".encode()

    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = None

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return response

        def close(self):
            pass

    response = Response()
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda host, port, **kw: [
            (2, 1, 6, "", ("127.0.0.1" if host == "localhost" else "93.184.216.34", port))
        ],
    )
    monkeypatch.setattr(module.socket, "create_connection", lambda *a, **k: object())
    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
    assert module.fetch_knowledge_page("http://example.com/article") == {
        "name": "网页标题",
        "content": "网页正文\n\n来源：http://example.com/article",
    }
    response.status = 302
    response.getheader = lambda key: "http://localhost/private"
    with pytest.raises(ValueError, match="公开"):
        module.fetch_knowledge_page("http://example.com/article")


def test_mcp_stdio_editor_preserves_advanced_settings(store):
    values = {
        "transport": "stdio",
        "command": "python",
        "args": ["/tmp/a b.py", "--flag"],
        "env": {"SECRET": "secret-value"},
        "cwd": "/tmp",
        "timeout_seconds": 15,
    }
    change(store, kind="tools", action="save", id="local-tool", values=values, is_new=True)
    settings = config(store)
    settings["mcp"]["servers"]["local-tool"]["disabled_tools"] = ["danger"]
    store.config.write_text(json.dumps(settings), encoding="utf-8")
    change(
        store,
        kind="tools",
        action="save",
        id="local-tool",
        values={**values, "command": "python3", "args": ["/tmp/new.py"], "cwd": None},
    )
    saved = config(store)["mcp"]["servers"]["local-tool"]
    assert saved["command"] == "python3"
    assert saved["args"] == ["/tmp/new.py"]
    assert saved["env"] == {"SECRET": "secret-value"}
    assert saved["disabled_tools"] == ["danger"]
    assert "cwd" not in saved
    module.MCPServerSettings.from_mapping("local-tool", saved)
    with pytest.raises(ValueError, match="同名"):
        change(store, kind="tools", action="save", id="local-tool", values=values, is_new=True)
    with pytest.raises(ValueError, match="启用状态"):
        change(store, kind="tools", action="toggle", id="local-tool", values={"enabled": "false"})


def test_mcp_http_editor_preserves_credentials_and_rejects_transport_change(store):
    values = {
        "transport": "http",
        "url": "https://example.com/mcp",
        "http_headers": {"Authorization": "Bearer secret"},
        "bearer_token_env_var": "MY_TOKEN",
        "timeout_seconds": 45,
    }
    change(store, kind="tools", action="save", id="remote-tool", values=values)
    change(
        store,
        kind="tools",
        action="save",
        id="remote-tool",
        values={"transport": "http", "url": "https://example.com/new"},
    )
    saved = config(store)["mcp"]["servers"]["remote-tool"]
    assert saved["http_headers"] == values["http_headers"]
    assert saved["bearer_token_env_var"] == "MY_TOKEN"
    assert saved["timeout_seconds"] == 45
    with pytest.raises(ValueError, match="传输方式"):
        change(
            store,
            kind="tools",
            action="save",
            id="remote-tool",
            values={"transport": "stdio", "command": "python"},
        )
    with pytest.raises(ValueError):
        change(
            store,
            kind="tools",
            action="save",
            id="remote-tool",
            values={**values, "http_headers": {"bad": 123}},
        )


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("draft", [True, False])
def test_mcp_connection_stdio_validates_and_closes(store, tmp_path, valid, draft):
    import sys

    store.workspace.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "mcp_test.py"
    trace = tmp_path / "calls.jsonl"
    tools = (
        [
            {
                "name": "echo",
                "description": "示例工具",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]
        if valid
        else [{"name": "broken", "inputSchema": "not-an-object"}]
    )
    script.write_text("""import json, sys
from pathlib import Path
for line in sys.stdin:
 packet=json.loads(line)
 with Path(sys.argv[1]).open('a') as log: log.write(packet['method']+'\\n')
 if 'id' not in packet: continue
 if packet['method']=='initialize':
  result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
 elif packet['method']=='tools/list': result={'tools':json.loads(sys.argv[2])}
 else: raise RuntimeError('unexpected tool execution')
 print(json.dumps({'jsonrpc':'2.0','id':packet['id'],'result':result}),flush=True)
""", encoding="utf-8")
    values = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script), str(trace), json.dumps(tools)],
    }
    if not draft:
        change(store, kind="tools", action="save", id="probe", values=values)
    before = store.config_text()
    result = change(
        store,
        kind="tools",
        action="test-connection",
        id="probe",
        **({"values": values, "is_new": True} if draft else {}),
    )
    assert result["ok"] is valid, result
    assert result["response"] == {"tools": tools}
    if valid:
        assert result["tool_count"] == 1
    else:
        assert "校验返回格式" in result["error"]
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    assert store.config_text() == before


def test_mcp_connection_http_and_empty_catalog(store):
    from http.server import BaseHTTPRequestHandler

    calls = []

    class MCPHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            packet = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(packet["method"])
            if "id" not in packet:
                self.send_response(202)
                self.end_headers()
                return
            if packet["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "http-fixture", "version": "1"},
                }
            else:
                assert packet["method"] == "tools/list"
                result = {"tools": []}
            body = json.dumps({"jsonrpc": "2.0", "id": packet["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), MCPHandler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        change(
            store,
            kind="tools",
            action="save",
            id="remote-probe",
            values={"transport": "http", "url": f"http://127.0.0.1:{server.server_port}/mcp"},
        )
        result = change(store, kind="tools", action="test-connection", id="remote-probe")
        assert result["ok"], result
        assert result["tool_count"] == 0
        assert calls == ["initialize", "notifications/initialized", "tools/list"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_mcp_connection_timeout_releases_client(store, monkeypatch):
    import asyncio

    closed = []

    class SlowClient:
        async def start(self):
            await asyncio.sleep(1)

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(module, "create_client", lambda settings: SlowClient())
    change(
        store,
        kind="tools",
        action="save",
        id="slow",
        values={"transport": "http", "url": "https://example.com/mcp", "timeout_seconds": 0.01},
    )
    result = change(store, kind="tools", action="test-connection", id="slow")
    assert not result["ok"]
    assert "超时" in result["error"]
    assert closed == [True]
    with pytest.raises(ValueError, match="配置已更新"):
        store.mutate(
            {"kind": "tools", "action": "test-connection", "id": "slow", "revision": "old"}
        )


def test_mcp_draft_preserves_saved_settings_and_redacts(store, monkeypatch):
    change(
        store,
        kind="tools",
        action="save",
        id="draft",
        values={
            "transport": "http",
            "url": "https://old.example/mcp",
            "headers": {"Authorization": "secret-old"},
        },
    )
    before = store.config_text()

    async def probe(settings, config):
        assert settings.url == "https://new.example/mcp"
        assert dict(settings.headers)["Authorization"] == "secret-new"
        return {"ok": True, "response": {"message": "secret-new"}}

    monkeypatch.setattr(store, "_test_mcp", probe)
    values = {
        "transport": "http",
        "url": "https://new.example/mcp",
        "headers": {"Authorization": "secret-new"},
    }
    result = change(store, kind="tools", action="test-connection", id="draft", values=values)
    assert result["response"]["message"] == "[已隐藏]"
    assert store.config_text() == before
    with pytest.raises(ValueError, match="同名"):
        change(
            store, kind="tools", action="test-connection", id="draft", values=values, is_new=True
        )


def test_custom_tool_crud_is_separate_and_loadable(store):
    from corki.tools.custom import register_custom_tools
    from corki.tools.registry import ToolRegistry

    before = store.config_text()
    value = {
        "name": "query",
        "description": "Query",
        "transport": "http",
        "url": "https://example.com/query",
        "headers": {"Authorization": "secret-custom"},
        "parameters": {"type": "object", "properties": {}},
    }
    store.mutate(
        {
            "kind": "custom-tools",
            "action": "save",
            "id": "query",
            "is_new": True,
            "revision": None,
            "values": value,
        }
    )
    path = store.home / "tools/custom/query.json"
    assert path.is_file()
    assert_private_file(path)
    assert "secret-custom" not in json.dumps(store.snapshot())
    registry = ToolRegistry()
    register_custom_tools(registry, store.home)
    assert registry.get("custom__query")
    with pytest.raises(ValueError, match="同名"):
        store.mutate(
            {
                "kind": "custom-tools",
                "action": "save",
                "id": "query",
                "is_new": True,
                "revision": None,
                "values": value,
            }
        )
    with pytest.raises(ValueError, match="已更新"):
        store.mutate({"kind": "custom-tools", "action": "delete", "id": "query", "revision": "old"})
    current = store.custom_tool_document("query")
    store.mutate(
        {
            "kind": "custom-tools",
            "action": "toggle",
            "id": "query",
            "enabled": False,
            "revision": current["revision"],
        }
    )
    registry = ToolRegistry()
    register_custom_tools(registry, store.home)
    assert registry.get("custom__query") is None
    current = store.custom_tool_document("query")
    store.mutate(
        {"kind": "custom-tools", "action": "delete", "id": "query", "revision": current["revision"]}
    )
    assert not path.exists()
    assert store.config_text() == before
    assert not (store.home / "manager/trash").exists()
    with pytest.raises(ValueError):
        store.custom_tool_path("../outside")


@pytest.mark.parametrize(
    "changes",
    [{"transport": "wrong"}, {"description": ""}, {"parameters": []}, {"name": "../escape"}],
)
def test_custom_tool_import_invalid_does_not_write(store, changes):
    value = {
        "name": "imported",
        "description": "Imported tool",
        "transport": "http",
        "url": "https://example.com/query",
        "parameters": {"type": "object"},
        **changes,
    }
    with pytest.raises(ValueError):
        store.mutate(
            {
                "kind": "custom-tools",
                "action": "save",
                "id": value["name"],
                "values": value,
                "is_new": True,
                "revision": None,
            }
        )
    assert not list((store.home / "tools/custom").glob("*.json"))


def test_custom_tool_import_preserves_complete_definition(store):
    import sys

    from corki.tools.custom import register_custom_tools
    from corki.tools.registry import ToolRegistry

    value = {
        "name": "imported",
        "description": "Complete process tool",
        "transport": "process",
        "command": sys.executable,
        "args": ["-c", "print('ok')"],
        "env": {"TEST": "value"},
        "cwd": str(store.workspace),
        "timeout_seconds": 45,
        "enabled": True,
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    }
    store.mutate(
        {
            "kind": "custom-tools",
            "action": "save",
            "id": value["name"],
            "values": value,
            "is_new": True,
            "revision": None,
        }
    )
    path = store.home / "tools/custom/imported.json"
    before = path.read_text(encoding="utf-8")
    assert json.loads(before) == value
    registry = ToolRegistry()
    register_custom_tools(registry, store.home)
    assert registry.get("custom__imported").spec.parameters == value["parameters"]
    with pytest.raises(ValueError, match="同名"):
        store.mutate(
            {
                "kind": "custom-tools",
                "action": "save",
                "id": value["name"],
                "values": {**value, "description": "overwrite"},
                "is_new": True,
                "revision": None,
            }
        )
    assert path.read_text(encoding="utf-8") == before


def custom_probe(store, script, **options):
    import sys

    store.workspace.mkdir(parents=True, exist_ok=True)
    values = {
        "name": "probe",
        "description": "Test",
        "transport": "process",
        "command": sys.executable,
        "args": ["-c", script],
        "parameters": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    }
    return store.mutate(
        {
            "kind": "custom-tools",
            "action": "test",
            "id": "probe",
            "revision": None,
            "values": values,
            "arguments": {"q": "hello"},
            **options,
        }
    )


@pytest.mark.parametrize(
    "script,expected,success",
    [
        ("import json,sys;print(json.dumps(json.load(sys.stdin)))", "json", True),
        ("print('plain text')", "json", False),
        ("print('plain text')", "text", True),
        ("print('')", "json", False),
        ("print('NaN')", "json", False),
        ("print('1e999')", "json", False),
        ("import sys;print('broken',file=sys.stderr);sys.exit(1)", "json", False),
    ],
)
def test_custom_tool_probe_formats_and_no_writes(store, script, expected, success):
    before = store.config_text()
    result = custom_probe(store, script, expected_format=expected)
    assert result["ok"] is success, result
    assert result["duration_ms"] >= 0
    assert isinstance(result["output"], str)
    assert store.config_text() == before
    assert not list((store.home / "tools/custom").glob("*.json"))


def test_custom_tool_probe_rejects_arguments_before_execution(store):
    marker = store.home / "must-not-exist"
    result = custom_probe(
        store,
        f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')",
        arguments={"q": 1},
    )
    assert not result["ok"]
    assert "string" in result["error"]
    assert not marker.exists()


def test_custom_tool_probe_saved_revision_and_credentials(store):
    import sys

    store.workspace.mkdir(parents=True, exist_ok=True)
    values = {
        "name": "saved",
        "description": "Saved",
        "transport": "process",
        "command": sys.executable,
        "args": ["-c", "import os,json;print(json.dumps({'secret':os.environ['MY_SECRET']}))"],
        "env": {"MY_SECRET": "test-private-token"},
        "parameters": {"type": "object"},
    }
    store.mutate(
        {
            "kind": "custom-tools",
            "action": "save",
            "id": "saved",
            "revision": None,
            "values": values,
        }
    )
    path = store.home / "tools/custom/saved.json"
    before = path.read_text(encoding="utf-8")
    result = store.mutate(
        {
            "kind": "custom-tools",
            "action": "test",
            "id": "saved",
            "revision": module.revision(before),
            "arguments": {},
        }
    )
    assert result["ok"], result
    assert "test-private-token" not in json.dumps(result)
    assert path.read_text(encoding="utf-8") == before
    with pytest.raises(ValueError, match="已更新"):
        store.mutate(
            {
                "kind": "custom-tools",
                "action": "test",
                "id": "saved",
                "revision": "old",
                "arguments": {},
            }
        )


def test_custom_tool_probe_timeout(store):
    import sys

    result = custom_probe(
        store,
        "",
        values={
            "name": "probe",
            "description": "Timeout",
            "transport": "process",
            "command": sys.executable,
            "args": ["-c", "import time;time.sleep(10)"],
            "timeout_seconds": 0.1,
            "parameters": {"type": "object"},
        },
    )
    assert not result["ok"] and "timed out" in result["error"]


def test_custom_tool_probe_http(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200 if self.path.startswith("/ok?") else 500)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        for endpoint, success in [("ok", True), ("error", False)]:
            result = custom_probe(
                store,
                "",
                values={
                    "name": "probe",
                    "description": "HTTP",
                    "transport": "http",
                    "method": "GET",
                    "url": f"http://127.0.0.1:{server.server_port}/{endpoint}",
                    "parameters": {"type": "object"},
                },
            )
            assert result["ok"] is success, result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_knowledge_enable_extract_reuse_and_edit(store, monkeypatch):
    import asyncio
    import time

    from corki.models.types import ModelCompleted, ModelTextDelta

    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            await asyncio.sleep(0.02)
            blocks = json.loads(request.items[0].content)["blocks"]
            yield ModelTextDelta(json.dumps({"segments": [{
                "start": 1, "end": len(blocks), "title": "本地说明",
                "summary": "解释文档中的设置及其用途。", "keywords": ["设置"]}]}))
            yield ModelCompleted(())

        async def aclose(self):
            pass

    monkeypatch.setattr(module, "_create_model", lambda *_: Model())
    change(store, kind="knowledge", action="create", name="guide", content="# 设置\n\n设置说明")

    def item():
        return next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "user")

    def wait_ready():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if item()["index_status"] == "ready":
                return
            time.sleep(0.01)
        pytest.fail(str(item()))

    def toggle(enabled):
        change(store, kind="knowledge", action="toggle", id=item()["id"],
               knowledge_revision=store.snapshot()["knowledge_revision"],
               values={"enabled": enabled})

    assert not item()["enabled"] and not requests
    document_id = item()["document_id"]
    toggle(True)
    wait_ready()
    assert len(requests) == 1 and requests[0].model == "model-a"
    assert store.knowledge.search("设置", document_id=document_id)["total"] == 1
    toggle(False)
    assert all(d["title"] == "corki_docs" for d in store.knowledge.search()["results"])
    toggle(True)
    assert len(requests) == 1
    current = item()
    change(store, kind="knowledge", action="save", id=current["id"],
           revision=current["revision"], content="# 设置\n\n修改后的设置")
    assert not item()["enabled"] and item()["index_status"] == "pending"
    assert len(requests) == 1
    toggle(True)
    wait_ready()
    assert len(requests) == 2 and item()["document_id"] == document_id
    segment = store.knowledge.search("设置", document_id=document_id)["results"][0]
    assert "修改后" in store.knowledge.read(segment["id"])["content"]
    current = item()
    change(store, kind="knowledge", action="delete", id=current["id"], revision=current["revision"])
    assert all(d["title"] == "corki_docs" for d in store.knowledge.search()["results"])


def test_knowledge_missing_default_rolls_back_enable(store):
    store.config.write_text('{}', encoding="utf-8")
    change(store, kind="knowledge", action="create", name="guide", content="hello")
    item = next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "user")
    with pytest.raises(ValueError, match="默认"):
        change(store, kind="knowledge", action="toggle", id=item["id"],
               knowledge_revision=store.snapshot()["knowledge_revision"], values={"enabled": True})
    assert not next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "user")["enabled"]


def test_cron_crud_versions_and_separate_storage(store, monkeypatch):
    before = store.config_text()
    values = {"name": "Daily notes", "prompt": "Summarize notes", "schedule": "every 1h",
              "timezone": "UTC", "provider": "local", "model": "model-a"}
    created = store.mutate({"kind": "cron", "action": "save", "values": values})
    job = store.cron.list()["jobs"][0]
    assert job["id"] == created["id"]
    assert not store.cron.path.is_relative_to(store.workspace)
    # An implicit default directory can be edited before its first execution creates it.
    job["prompt"] = "Updated notes"
    store.mutate({"kind": "cron", "action": "save", "id": job["id"],
                  "version": job["version"], "values": job})
    with pytest.raises(ValueError, match="更新"):
        store.mutate({"kind": "cron", "action": "pause", "id": job["id"], "version": 1})
    launched = []
    monkeypatch.setattr(store.cron_service, "launch", launched.append)
    store.mutate({"kind": "cron", "action": "run", "id": job["id"]})
    assert len(launched) == 1
    with pytest.raises(ValueError, match="已在执行"):
        store.mutate({"kind": "cron", "action": "run", "id": job["id"]})
    store.mutate({"kind": "cron", "action": "stop", "id": job["id"], "version": 2})
    assert store.cron.run(launched[0])["cancel"] == 1
    store.cron.finish(launched[0], "cancelled")
    store.mutate({"kind": "cron", "action": "delete", "id": job["id"], "version": 2})
    assert not store.cron.list()["jobs"]
    assert before == store.config_text()


def test_database_admin_http(store):
    import sqlite3

    with closing(sqlite3.connect(store.home / "fixture.db")) as db, db:
        db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.store = store
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            assert client.get("/api/databases").status_code == 200
            body = {"kind": "database", "action": "insert", "database": "fixture.db",
                    "table": "items", "values": {"name": "example"}}
            assert client.post("/api/mutate", json=body).status_code == 403
            response = client.post("/api/mutate", json=body, headers={"X-Corki-Manager": "1"})
            assert response.status_code == 200
            result = client.get("/api/databases", params={"database": "fixture.db", "table": "items"})
            assert result.json()["rows"][0]["values"]["name"] == "example"
            assert client.get("/api/databases", headers={"Origin": "https://example.com"}).status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cron_failed_detail_recovers_session_error(store):
    import sqlite3

    identity = store.cron.claim(store.cron.save({
        'name': 'error fixture', 'prompt': 'fixture', 'schedule': 'every 2h',
        'timezone': 'Asia/Shanghai',
    }))
    store.cron.start_run(identity)
    store.cron.finish(identity, 'failed', error='Agent 执行失败')
    store.database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(store.database)) as db, db:
        db.execute('CREATE TABLE turns(thread_id TEXT, error TEXT, updated_at TEXT)')
        db.execute('INSERT INTO turns VALUES(?,?,?)', (
            identity, 'HTTP 401: private-token-123', '2026-01-01 12:00',
        ))
    result = store.cron_run(identity)
    assert 'HTTP 401' in result['error_log']
    assert 'private-token-123' not in result['error_log']
    store.database.unlink()
    assert store.cron_run(identity)['error_log'] == 'Agent 执行失败'


def test_knowledge_versions_external_changes_retention_and_readonly(store):
    change(store, kind="knowledge", action="create", name="versioned", content="# First\ninitial")
    item = next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "user")
    for index in range(23):
        current = store.knowledge_document(item["id"])
        change(store, kind="knowledge", action="save", id=item["id"],
               revision=current["revision"], content=f"# Version {index}\nbody")
    current = store.knowledge_document(item["id"])
    assert len(current["versions"]) == 20
    assert current["versions"][0]["content"] == current["content"]
    assert not current["enabled"]
    Path(current["path"]).write_text("# External\nchanged", encoding="utf-8")
    external = store.knowledge_document(item["id"])
    assert external["versions"][0]["content"] == "# External\nchanged"
    assert len(external["versions"]) == 20
    with pytest.raises(ValueError, match="更新"):
        change(store, kind="knowledge", action="save", id=item["id"],
               revision=current["revision"], content="stale")
    prior = external["versions"][2]["content"]
    change(store, kind="knowledge", action="save", id=item["id"],
           revision=external["revision"], content=prior)
    assert store.knowledge_document(item["id"])["content"] == prior
    builtin = next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "builtin")
    assert store.knowledge_document(builtin["id"])["versions"] == []
    with pytest.raises(ValueError, match="只读"):
        change(store, kind="knowledge", action="save", id=builtin["id"],
               revision=builtin["revision"], content="forbidden")
    assert len([f for f in store.files() if f["kind"] == "knowledge"]) == 2


def test_knowledge_preparation_stays_disabled_and_edit_invalidates_job(store, monkeypatch):
    import time
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    async def extraction(model, settings, knowledge, doc, job, content):
        started.set()
        release.wait(5)
        try:
            knowledge.check_job(doc["id"], job)
        finally:
            finished.set()

    monkeypatch.setattr(module, "_create_model", lambda *_: object())
    monkeypatch.setattr(module, "extract_knowledge", extraction)
    change(store, kind="knowledge", action="create", name="slow", content="# Original")
    item = next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "user")
    change(store, kind="knowledge", action="toggle", id=item["id"],
           knowledge_revision=store.snapshot()["knowledge_revision"], values={"enabled": True})
    assert started.wait(2)
    processing = store.knowledge_document(item["id"])
    assert not processing["enabled"] and processing["index_status"] == "processing"
    change(store, kind="knowledge", action="save", id=item["id"],
           revision=item["revision"], content="# New source")
    release.set()
    assert finished.wait(2)
    time.sleep(.05)
    current = store.knowledge_document(item["id"])
    assert not current["enabled"] and current["index_status"] == "pending"
    assert current["content"] == "# New source"


def test_knowledge_failed_preparation_does_not_enable_or_leak_error(store, monkeypatch):
    import time

    def fail(*args):
        raise RuntimeError("private-token-123 private source")

    monkeypatch.setattr(module, "_create_model", fail)
    change(store, kind="knowledge", action="create", name="fail", content="# Failure")
    item = next(f for f in store.files() if f["kind"] == "knowledge" and f["group"] == "user")
    change(store, kind="knowledge", action="toggle", id=item["id"],
           knowledge_revision=store.snapshot()["knowledge_revision"], values={"enabled": True})
    for _ in range(100):
        current = store.knowledge_document(item["id"])
        if current["index_status"] == "failed":
            break
        time.sleep(.01)
    assert current["index_status"] == "failed" and not current["enabled"]
    assert "默认模型请求失败" in current["index_error"]
    assert "private" not in current["index_error"]


def test_monthly_usage_has_30_local_dates_and_zero_fills_gaps(store):
    import sqlite3
    from datetime import date

    assert len(store.monthly_usage(date(2026, 10, 17))) == 30
    store.database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(store.database)) as db, db:
        db.execute('CREATE TABLE usage_events(created_at TEXT,input_tokens INTEGER,output_tokens INTEGER)')
        for day, value in [('2026-09-17', 999), ('2026-09-18', 10),
                           ('2026-10-17', 20), ('2026-10-18', 999)]:
            # Convert local noon to UTC to test the same local-date contract as the API.
            db.execute("INSERT INTO usage_events VALUES(datetime(?,'utc'),?,?)",
                       (day + ' 12:00:00', value, value))
        db.execute("INSERT INTO usage_events VALUES(datetime('2026-10-17 13:00:00','utc'),5,7)")
    rows = store.monthly_usage(date(2026, 10, 17))
    assert len(rows) == 30
    assert rows[0] == dict(day='2026-09-18', input=10, output=10, steps=1)
    assert rows[-1] == dict(day='2026-10-17', input=25, output=27, steps=2)
    assert all(r['input'] == r['output'] == r['steps'] == 0 for r in rows[1:-1])

@pytest.mark.parametrize("archive", [False, True])
def test_skill_import_without_package_count_or_size_cap(store, archive):
    import base64
    import io
    import zipfile

    entries = [("example/SKILL.md", b"---\nname: example\ndescription: Large package\n---\nBody")]
    entries += [(f"example/assets/file-{i}.txt", b"data") for i in range(410)]
    entries.append(("example/assets/large.bin", b"x" * (10 * 1024 * 1024)))
    data = skill_upload(entries)
    if archive:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for name, content in entries:
                z.writestr(name, content)
        data.pop("files")
        data["archive"] = base64.b64encode(buf.getvalue()).decode()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.store = store
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False, timeout=30) as client:
            headers = {"X-Corki-Manager": "1", "X-Corki-Skill-Import": "1"}
            response = client.post("/api/mutate", json={**data, "action": "validate-import"}, headers=headers)
            assert response.status_code == 200, response.text
            assert len(response.json()["files"]) == len(entries)
            response = client.post("/api/mutate", json=data, headers=headers)
            assert response.status_code == 200, response.text
            target = store.compatibility_home / ".agents/skills/example"
            assert (target / "assets/large.bin").stat().st_size == 10 * 1024 * 1024
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

@pytest.mark.parametrize("header", ["name: legacy-name", "name: >\n  legacy-name", "description: Test import"])
def test_existing_skill_name_cannot_be_changed(store, header):
    content = "---\n" + header + ("\ndescription: Test import" if "description:" not in header else "") + "\n---\nBody\n"
    path = store.home / "skills/legacy-directory/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    item = next(f for f in store.files() if f["kind"] == "skills")
    resource = store.read_skill_resource(item["id"], "SKILL.md")
    with pytest.raises(ValueError, match="不支持修改"):
        store.mutate(dict(kind="skills", action="rename", id=item["id"], name="renamed", revision=resource["revision"]))
    for action in ("save-resource", "save"):
        with pytest.raises(ValueError, match="不支持修改"):
            store.mutate(dict(kind="skills", action=action, id=item["id"], path="SKILL.md",
                              content="---\nname: changed\ndescription: Test\n---\nBody", revision=resource["revision"]))
    store.mutate(dict(kind="skills", action="save-resource", id=item["id"], path="SKILL.md",
                      content=content + "Updated", revision=resource["revision"]))
    assert path.read_text(encoding="utf-8") == content + "Updated"


def hook_change(store, **values):
    current = store.hooks_document()
    return store.mutate(dict(kind="hooks", revision=current["revision"],
                             file_revision=current["file_revision"], **values))


def test_hooks_crud_trust_and_runtime_discovery(store):
    from corki.config.settings import CorkiSettings
    from corki.core.stop_hooks import discover

    hook_change(store, action="save", event="UserPromptSubmit", matcher="*",
                handler={"type": "command", "command": "echo ready"}, enabled=True)
    item = store.hooks_document()["entries"][0]
    assert item["enabled"] and item["trusted"]
    settings = CorkiSettings.for_directory(store.workspace, config_file=store.config)
    commands, warnings = discover(settings.configuration, event_name="UserPromptSubmit")
    assert len(commands) == 1 and commands[0].command == "echo ready"
    hook_change(store, action="toggle", id=item["id"], enabled=False)
    settings = CorkiSettings.for_directory(store.workspace, config_file=store.config)
    assert not discover(settings.configuration, event_name="UserPromptSubmit")[0]
    hook_change(store, action="save", id=item["id"], event="Stop", matcher="*",
                handler={"type": "command", "command": "echo done"}, enabled=True)
    item = store.hooks_document()["entries"][0]
    assert item["event"] == "Stop"
    settings = CorkiSettings.for_directory(store.workspace, config_file=store.config)
    assert len(discover(settings.configuration, event_name="Stop")[0]) == 1
    hook_change(store, action="delete", id=item["id"])
    assert store.hooks_document()["entries"] == []
    assert config(store)["unrelated"] == {"enabled": True}


def test_hooks_json_reindex_preserves_other_trust(store):
    from corki.config.settings import CorkiSettings
    from corki.core.stop_hooks import discover
    path = store.home / "hooks.json"
    path.write_text(json.dumps({"description": "keep", "hooks": {"Stop": [{"matcher": "*", "hooks": [
        {"type": "command", "command": "echo first"}, {"type": "command", "command": "echo second"},
        {"type": "command", "command": "echo untrusted"}]}]}}), encoding="utf-8")
    rows = store.hooks_document()["entries"]
    hook_change(store, action="toggle", id=rows[1]["id"], enabled=True)
    hook_change(store, action="delete", id=rows[0]["id"])
    rows = store.hooks_document()["entries"]
    assert rows[0]["enabled"] and not rows[1]["trusted"]
    assert json.loads(path.read_text(encoding="utf-8"))["description"] == "keep"
    settings = CorkiSettings.for_directory(store.workspace, config_file=store.config)
    commands, _ = discover(settings.configuration, event_name="Stop")
    assert [c.command for c in commands] == ["echo second"]


def test_hooks_conflicts_and_invalid_config(store):
    snapshot = store.hooks_document()
    hook_change(store, action="global", enabled=False)
    with pytest.raises(ValueError, match="已更新"):
        store.mutate(dict(kind="hooks", action="global", enabled=True,
                          revision=snapshot["revision"], file_revision=snapshot["file_revision"]))
    original = store.config_text()
    for event, handler in [("PermissionRequest", {"type":"command", "command":"echo x"}),
                           ("Stop", {"type":"command", "command":""}),
                           ("Stop", {"type":"mcp_tool", "server":"absent", "tool":"x"})]:
        with pytest.raises(ValueError):
            hook_change(store, action="save", event=event, handler=handler, enabled=True)
    assert store.config_text() == original
    (store.home / "hooks.json").write_text('{broken', encoding="utf-8")
    with pytest.raises(ValueError):
        store.hooks_document()


def test_hooks_mcp_and_disabled_preserved_after_delete(store):
    change(store, kind="tools", action="save", id="demo", values={"transport":"stdio","command":"python3"})
    hook_change(store, action="save", event="Stop", handler={"type":"command","command":"echo x"}, enabled=True)
    hook_change(store, action="save", event="PreToolUse", matcher="*", handler={"type":"mcp_tool","server":"demo","tool":"check","input":{}}, enabled=False)
    rows=store.hooks_document()["entries"]
    hook_change(store, action="delete", id=next(r["id"] for r in rows if r["event"]=="Stop"))
    row=store.hooks_document()["entries"][0]
    assert row["trusted"] and not row["enabled"] and row["handler"]["server"]=="demo"


def test_manager_saved_hook_executes_with_runtime_runner(store):
    import asyncio
    from corki.config.settings import CorkiSettings
    from corki.core.stop_hooks import discover, run_command
    from corki.shell import default_user_shell
    store.workspace.mkdir(parents=True, exist_ok=True)
    hook_change(store, action="save", event="UserPromptSubmit", matcher="*",
                handler={"type":"command","command":
                         "Write-Output hook-runtime-ok" if os.name == "nt" else "printf hook-runtime-ok",
                         "timeout":60 if os.name == "nt" else 5}, enabled=True)
    settings = CorkiSettings.for_directory(store.workspace, config_file=store.config)
    commands, _ = discover(settings.configuration, event_name="UserPromptSubmit")
    result = asyncio.run(run_command(commands[0], {"hook_event_name":"UserPromptSubmit"},
                                     shell=default_user_shell(), cwd=store.workspace,
                                     environment=dict(os.environ)))
    assert result["exit_code"] == 0 and result["stdout"].strip() == "hook-runtime-ok"


def test_private_hook_skill_upload_hidden_save_and_cleanup(store):
    upload=skill_upload([('fixture/SKILL.md',b'---\nname: private-test\ndescription: private\n---\nRead references/guide.md'),('fixture/references/guide.md',b'guide')])
    result=hook_change(store, action='upload-skill',upload=upload)
    path=Path(result['path'])
    assert path.is_file() and (path.parent/'references/guide.md').read_text(encoding="utf-8")=='guide'
    assert not any(f['name']=='private-test' for f in store.files())
    hook_change(store,action='save',event='UserPromptSubmit',matcher='*',handler={'type':'skill','path':str(path)},enabled=True)
    store.mutate({'kind':'hooks','action':'discard-upload','path':str(path)})
    assert path.exists()  # Saved package cannot be removed by stale dialog cleanup.
    item=store.hooks_document()['entries'][0]
    hook_change(store,action='delete',id=item['id'])
    assert not path.parent.exists()
    result=hook_change(store,action='upload-skill',upload=upload)
    store.mutate({'kind':'hooks','action':'discard-upload','path':result['path']})
    assert not Path(result['path']).parent.exists()


def test_private_hook_skill_zip_and_prompt(store):
    import base64,io,zipfile
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('demo/SKILL.md','---\nname: zip-private\ndescription: test\n---\nInstructions')
        z.writestr('demo/scripts/check.py','print("fixture")')
    result=hook_change(store,action='upload-skill',upload={'archive':base64.b64encode(buf.getvalue()).decode()})
    assert (Path(result['path']).parent/'scripts/check.py').exists()
    hook_change(store,action='save',event='PreToolUse',matcher='*',handler={'type':'prompt','prompt':'Check facts'},enabled=True)
    assert store.hooks_document()['entries'][0]['handler']['prompt']=='Check facts'
    with pytest.raises(ValueError):
        hook_change(store,action='upload-skill',upload=skill_upload([('../SKILL.md',b'bad')]))

@pytest.mark.parametrize("group", ["corki", "public", "project"])
def test_skill_permanent_delete_package(store, tmp_path, group):
    base = {"corki": store.home / "skills", "public": store.compatibility_home / ".agents/skills",
            "project": store.workspace / ".corki/skills"}[group]
    package = base / "delete-fixture"
    (package / "scripts").mkdir(parents=True)
    (package / "SKILL.md").write_text("---\nname: delete-fixture\ndescription: fixture\n---\nGuide", encoding="utf-8")
    (package / "scripts/run.py").write_text("print('fixture')", encoding="utf-8")
    outside = tmp_path / "untouched.txt"
    outside.write_text("keep", encoding="utf-8")
    (package / "linked.txt").symlink_to(outside)
    item = next(f for f in store.files() if f["name"] == "delete-fixture")
    with pytest.raises(ValueError, match="已更新"):
        store.mutate(dict(kind="skills", action="delete", id=item["id"], revision="stale"))
    assert package.exists()
    store.mutate(dict(kind="skills", action="delete", id=item["id"], revision=item["revision"]))
    assert not package.exists()
    assert base.is_dir()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert not any(f["id"] == item["id"] for f in store.files())


def test_skill_permanent_delete_rejects_builtin(store):
    package = store.home / "skills/.system/protected"
    package.mkdir(parents=True)
    path = package / "SKILL.md"
    path.write_text("---\nname: protected\ndescription: fixture\n---\nGuide", encoding="utf-8")
    item = next(f for f in store.files() if f["name"] == "protected")
    with pytest.raises(ValueError, match="内置"):
        store.mutate(dict(kind="skills", action="delete", id=item["id"], revision=item["revision"]))
    assert path.exists()


def test_skill_export_large_package_uses_file_stream(store, skill_package):
    import zipfile
    root, identity = skill_package
    payload = root / "large.bin"
    with payload.open("wb") as out:
        for _ in range(33):
            out.write(b"x" * (1024 * 1024))
    stream, name = store.export_skill(identity, stream=True)
    with stream:
        assert stream.fileno() >= 0
        with zipfile.ZipFile(stream) as archive:
            assert archive.getinfo("workbench/large.bin").file_size == 33 * 1024 * 1024
            assert archive.testzip() is None
    assert stream.closed


@pytest.mark.parametrize("existing_group", ["corki", "public", "builtin"])
@pytest.mark.parametrize("target_group", ["corki", "public"])
def test_skill_import_rejects_actual_name_across_groups(store, existing_group, target_group):
    root = {"corki": store.home / "skills", "public": store.compatibility_home / ".agents/skills",
            "builtin": store.home / "skills/.system"}[existing_group]
    package = root / "different-directory"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text("---\nname: Example\ndescription: fixture\n---\nGuide", encoding="utf-8")
    data = skill_upload(group=target_group)
    for action in ("validate-import", "import"):
        with pytest.raises(ValueError, match="同名"):
            store.mutate({**data, "action": action})
    assert not (store.home / "skills/new-directory").exists()
    assert not (store.compatibility_home / ".agents/skills/new-directory").exists()


def test_skill_import_rechecks_name_after_preview(store):
    data = skill_upload(group="public")
    store.mutate({**data, "action": "validate-import"})
    store.mutate(skill_upload(group="corki"))
    with pytest.raises(ValueError, match="同名"):
        store.mutate(data)


def test_private_hook_skill_import_does_not_conflict_with_catalog(store):
    store.mutate(skill_upload())
    result = store.import_skill(skill_upload(name="hook-copy"), private=True)
    assert Path(result["path"]).is_dir()


@pytest.mark.parametrize("archive", [False, True])
def test_skill_import_uses_metadata_for_root_package(store, archive):
    import base64, io, zipfile
    data = skill_upload([( "SKILL.md", b"---\nname: example\ndescription: fixture\n---\nGuide")])
    if archive:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zipped:
            for entry in data.pop("files"):
                zipped.writestr(entry["path"], base64.b64decode(entry["data"]))
        data["archive"] = base64.b64encode(buffer.getvalue()).decode()
    result = store.mutate({**data, "action": "validate-import"})
    assert result["folder"] == "example"
    assert result["name"] == "example"
    store.mutate(data)
    assert (store.compatibility_home / ".agents/skills/example/SKILL.md").is_file()


@pytest.mark.parametrize("name", [".", "..", "../outside", "a/b", "a\\b", "/outside", ".hidden", "trailing."])
def test_skill_import_rejects_unsafe_directory_names(store, name):
    with pytest.raises(ValueError, match="目录名"):
        store.mutate(skill_upload([("SKILL.md", f"---\nname: {name}\ndescription: fixture\n---\nGuide".encode())]))


@pytest.mark.parametrize("name", ["CON", "con.txt", "NUL.skill", "COM1.0", "Lpt9.1", "COM¹.zip"])
def test_skill_import_rejects_reserved_directory_names(store, name):
    with pytest.raises(ValueError, match="保留名称"):
        store.mutate(skill_upload([("SKILL.md", f"---\nname: {name}\ndescription: fixture\n---\nGuide".encode())]))



@pytest.mark.parametrize("archive", [False, True])
def test_skill_import_preserves_hidden_resources(store, archive):
    import base64, io, zipfile
    data = skill_upload()
    data["files"] += skill_upload([
        ("example/.oss-version", b"0.19.0"),
        ("example/.config/options.json", b"{}"),
        ("example/.DS_Store", b"metadata"),
        ("example/._SKILL.md", b"metadata"),
        ("example/.git/config", b"metadata"),
        ("example/.manager-backups/SKILL.md.old", b"metadata"),
        ("__MACOSX/example/._SKILL.md", b"metadata"),
    ])["files"]
    if archive:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zipped:
            for entry in data.pop("files"):
                zipped.writestr(entry["path"], base64.b64decode(entry["data"]))
        data["archive"] = base64.b64encode(buffer.getvalue()).decode()
    result = store.mutate({**data, "action": "validate-import"})
    assert {f["path"] for f in result["files"]} == {"SKILL.md", ".oss-version", ".config/options.json"}
    store.mutate(data)
    root = store.compatibility_home / ".agents/skills/example"
    assert (root / ".oss-version").read_bytes() == b"0.19.0"
    assert not (root / ".git").exists()
    assert not (root / ".DS_Store").exists()

@pytest.mark.parametrize("action", ["import", "validate-import"])
def test_new_skill_directory_must_match_name(store, action):
    data = skill_upload([("example-1.0/SKILL.md", b"---\nname: example\ndescription: fixture\n---\nGuide")])
    with pytest.raises(ValueError, match="必须与"):
        store.mutate({**data, "action": action})
    assert not (store.compatibility_home / ".agents/skills").exists()


def test_import_requires_explicit_name_and_rejects_override(store):
    with pytest.raises(ValueError):
        store.mutate(skill_upload([("SKILL.md", b"---\ndescription: fixture\n---\nGuide")]))
    with pytest.raises(ValueError, match="自定义"):
        store.mutate(skill_upload(name="override"))

@pytest.mark.parametrize("group", ["corki", "public", "builtin"])
def test_skill_text_is_not_redacted_and_respects_source_permissions(store, group):
    root = {"corki": store.home / "skills", "public": store.compatibility_home / ".agents/skills",
            "builtin": store.home / "skills/.system"}[group]
    path = root / "fixture/SKILL.md"
    path.parent.mkdir(parents=True)
    content = "---\nname: fixture\ndescription: Test\n---\n认证方式 / Bearer 选择\nsk-demo-placeholder"
    path.write_text(content, encoding="utf-8")
    item = next(f for f in store.files() if f["kind"] == "skills")
    for result in (store.read_file(item["id"]), store.read_skill_resource(item["id"], "SKILL.md")):
        assert result["content"] == content
        assert result["writable"] == (group != "builtin")
    if group != "builtin":
        store.save_skill_resource(dict(id=item["id"], path="SKILL.md", revision=module.revision(content), content=content + "\nUpdated"))
        assert path.read_text(encoding="utf-8") == content + "\nUpdated"


def test_atomic_text_is_utf8_without_platform_newline_translation(tmp_path):
    path = tmp_path / "配置.json"
    content = '{"描述":"你好"}\n'
    module.atomic(path, content)
    assert path.read_bytes() == content.encode("utf-8")


@pytest.mark.parametrize("name", ["CON", "NUL", "COM1", "lpt9"])
def test_windows_reserved_names_rejected_before_filesystem_write(store, monkeypatch, name):
    from corki.platforms.paths import archive_parts

    monkeypatch.setattr(module, "archive_parts", lambda value: archive_parts(value, windows=True))
    with pytest.raises(ValueError, match="Windows"):
        store.custom_tool_path(name)
    with pytest.raises(ValueError, match="Windows"):
        store.mutate_file({"kind": "knowledge", "action": "create", "name": name, "content": "test"})


def test_provider_model_default_does_not_change_default_provider(store):
    current = config(store)
    current['providers']['other'] = {'models': {'first': {}, 'preferred': {}}}
    store.config.write_text(json.dumps(current))
    change(store, kind='models', action='default', provider='other', id='preferred')
    assert config(store)['default'] == {'provider': 'local', 'model': 'model-a'}
    assert config(store)['providers']['other']['default_model'] == 'preferred'
    assert next(p for p in store.snapshot()['providers'] if p['id'] == 'other')['default_model'] == 'preferred'
    change(store, kind='providers', action='default', id='other')
    assert config(store)['default'] == {'provider': 'other', 'model': 'preferred'}
    change(store, kind='providers', action='default', id='local')
    change(store, kind='providers', action='default', id='other')
    assert config(store)['default']['model'] == 'preferred'
