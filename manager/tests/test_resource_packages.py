"""Resource bundles use isolated homes; never read real user resources or credentials."""

import base64
import importlib.util
import io
import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest

from corki.config import document

spec = importlib.util.spec_from_file_location(
    "resource_package_server", Path(__file__).parents[1] / "server.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def store(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    config = {
        "default": {"provider": "fixture", "model": "fixture-model"},
        "providers": {
            "fixture": {
                "base_url": "https://example.invalid/v1",
                "api_key": "fixture-only",
                "models": {"fixture-model": {}},
            }
        },
        "mcp": {
            "servers": {
                "fixture-mcp": {
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-V"],
                    "enabled": True,
                }
            }
        },
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo fixture",
                            "statusMessage": "Fixture hook",
                        }
                    ],
                }
            ]
        },
    }
    (home / "settings.json").write_text(json.dumps(config))
    skill = home / "skills/fixture-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fixture-skill\ndescription: Isolated resource fixture\n---\n\n"
        "Use the local script.\n"
    )
    (skill / "scripts").mkdir()
    script = skill / "scripts/fixture.py"
    script.write_text('print("fixture")\n')
    script.chmod(0o700)
    return module.Store(home, workspace, tmp_path / "shared-user")


def make(store, name="Fixture bundle", **extra):
    inventory = store.resource_packages.inventory()
    return store.mutate(
        {
            "kind": "resource-packages",
            "action": "create",
            "name": name,
            "selection": {
                kind: [row["id"] for row in inventory[kind]] for kind in ("skills", "mcp", "hooks")
            },
            **extra,
        }
    )["id"]


def remove_live(store):
    shutil.rmtree(store.home / "skills/fixture-skill")
    config = document.loads(store.config_text())
    config.pop("mcp")
    config.pop("hooks")
    store.config.write_text(json.dumps(config))


def test_backup_snapshot_export_and_delete_are_independent(store):
    original = store.config_text()
    identity = make(store)
    assert store.config_text() == original
    assert not (store.home / "plugins").exists()
    raw, filename = store.resource_packages.export([identity])
    assert filename == "Fixture bundle.zip"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("plugin.json"))
        assert manifest["format"] == "corki-resource-package"
        assert "enabled" not in manifest["mcp"][0]["config"]
        assert "_upload" not in manifest["skills"][0]
        assert (
            archive.getinfo("skills/fixture-skill/scripts/fixture.py").external_attr >> 16 & 0o111
        )
    (store.home / "skills/fixture-skill/SKILL.md").write_text("changed source")
    assert store.resource_packages.export([identity])[0] == raw
    store.mutate({"kind": "resource-packages", "action": "delete", "id": identity})
    assert (store.home / "skills/fixture-skill/SKILL.md").read_text() == "changed source"
    assert store.config_text() == original
    assert not store.resource_packages.listing()["packages"]


@pytest.mark.parametrize("enabled", [False, True])
def test_import_creates_real_components_and_repeat_is_noop(store, enabled):
    identity = make(store)
    remove_live(store)
    result = store.mutate(
        {
            "kind": "resource-packages",
            "action": "import",
            "id": identity,
            "enable_after_import": enabled,
        }
    )
    assert len(result["available"]) == 3 and not result["skipped"]
    config = document.loads(store.config_text())
    assert config["mcp"]["servers"]["fixture-mcp"]["enabled"] is enabled
    skill = store.home / "skills/fixture-skill"
    assert (skill / "SKILL.md").exists()
    assert (skill / "scripts/fixture.py").stat().st_mode & 0o111
    rules = config.get("skills", {}).get("config", [])
    assert bool(rules) is not enabled
    hook = store.hooks_document()["entries"][0]
    assert hook["trusted"] and hook["enabled"] is enabled
    before = store.config_text()
    with pytest.raises(ValueError, match="无法重复导入"):
        store.mutate({"kind": "resource-packages", "action": "import", "id": identity})
    assert store.config_text() == before
    store.mutate({"kind": "resource-packages", "action": "delete", "id": identity})
    assert skill.exists() and store.hooks_document()["entries"][0]["trusted"]


def test_partial_duplicate_keeps_existing_config_and_reports_skip(store):
    identity = make(store)
    shutil.rmtree(store.home / "skills/fixture-skill")
    before = document.loads(store.config_text())
    result = store.mutate({"kind": "resource-packages", "action": "import", "id": identity})
    assert len(result["available"]) == 1 and {row["kind"] for row in result["skipped"]} == {
        "mcp",
        "hooks",
    }
    after = document.loads(store.config_text())
    assert after["mcp"] == before["mcp"] and after["hooks"] == before["hooks"]
    assert after.get("plugins") == before.get("plugins")


def test_export_archive_can_be_imported_in_another_home(store, tmp_path):
    identity = make(store)
    raw, _ = store.resource_packages.export([identity])
    new_home = tmp_path / "destination"
    new_home.mkdir()
    config = document.loads(store.config_text())
    config.pop("hooks")
    config.pop("mcp")
    (new_home / "settings.json").write_text(json.dumps(config))
    other = module.Store(new_home, store.workspace, tmp_path / "other-shared")
    payload = {"kind": "resource-packages", "archive": base64.b64encode(raw).decode()}
    plan = other.mutate({**payload, "action": "preview-import"})
    assert plan["can_import"] and len(plan["available"]) == 3
    other.mutate({**payload, "action": "import"})
    assert (new_home / "skills/fixture-skill/SKILL.md").is_file()


def test_failed_config_publish_rolls_back_new_files_and_backup(store, monkeypatch):
    identity = make(store)
    remove_live(store)
    before = store.config_text()

    def fail(*args):
        raise ValueError("fixture conflict")

    monkeypatch.setattr(store, "write_config", fail)
    with pytest.raises(ValueError, match="fixture conflict"):
        store.mutate({"kind": "resource-packages", "action": "import", "id": identity})
    assert store.config_text() == before
    assert not (store.home / "skills/fixture-skill").exists()
    assert not store.resource_packages.assets.exists() or not any(
        store.resource_packages.assets.iterdir()
    )
    assert len(store.resource_packages.listing()["packages"]) == 1


@pytest.mark.parametrize("filename", ["../escape", "/absolute", "C:/escape", "x/../../escape"])
def test_archive_paths_rejected_before_changes(store, filename):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(filename, "fixture")
    with pytest.raises(ValueError):
        store.mutate(
            {
                "kind": "resource-packages",
                "action": "preview-import",
                "archive": base64.b64encode(out.getvalue()).decode(),
            }
        )
    assert not store.resource_packages.listing()["packages"]


def test_zip_symlink_is_rejected(store):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/not-selected")
    with pytest.raises(ValueError, match="符号链接"):
        store.resource_packages.uploaded({"archive": base64.b64encode(out.getvalue()).decode()})


def test_hook_skill_is_bundled_and_survives_source_and_backup_deletion(store):
    main = store.home / "skills/fixture-skill/SKILL.md"
    config = document.loads(store.config_text())
    config["hooks"] = {
        "UserPromptSubmit": [
            {
                "matcher": "*",
                "hooks": [
                    {"type": "skill", "path": str(main), "statusMessage": "Skill hook fixture"}
                ],
            }
        ]
    }
    store.config.write_text(json.dumps(config))
    identity = make(store)
    remove_live(store)
    store.mutate(
        {
            "kind": "resource-packages",
            "action": "import",
            "id": identity,
            "enable_after_import": True,
        }
    )
    hook = store.hooks_document()["entries"][0]
    imported_main = Path(hook["handler"]["path"])
    assert imported_main != main and imported_main.is_file()
    assert (imported_main.parent / "scripts/fixture.py").is_file()
    assert hook["trusted"] and hook["enabled"]
    store.mutate({"kind": "resource-packages", "action": "delete", "id": identity})
    assert imported_main.is_file()


def test_local_files_create_components_and_preserve_plain_assets(store):
    files = {
        "local/local-skill/SKILL.md": (
            "---\nname: local-skill\ndescription: local fixture\n---\n\nFixture"
        ),
        "local/mcp.json": json.dumps(
            {
                "mcpServers": {
                    "local-mcp": {"command": "python", "args": ["-V"], "env": {"_FIXTURE": "keep"}}
                }
            }
        ),
        "local/hooks.json": json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo local",
                                    "statusMessage": "Local hook",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        "local/notes.txt": "local resource",
    }
    result = store.mutate(
        {
            "kind": "resource-packages",
            "action": "create",
            "name": "Local files",
            "files": [
                {"path": path, "data": base64.b64encode(content.encode()).decode()}
                for path, content in files.items()
            ],
        }
    )
    identity = result["id"]
    store.mutate({"kind": "resource-packages", "action": "import", "id": identity})
    config = document.loads(store.config_text())
    assert config["mcp"]["servers"]["local-mcp"]["env"] == {"_FIXTURE": "keep"}
    assert (store.home / "skills/local-skill/SKILL.md").is_file()
    assert any(p.name == "notes.txt" for p in store.resource_packages.assets.rglob("*"))


def test_batch_export_contains_independent_plugin_zips(store):
    first = make(store)
    second = make(store, name="Second bundle")
    raw, filename = store.resource_packages.export([first, second])
    assert filename == "corki-plugins.zip"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert len(archive.namelist()) == 2
        for name in archive.namelist():
            with zipfile.ZipFile(io.BytesIO(archive.read(name))) as plugin:
                assert json.loads(plugin.read("plugin.json"))["format"] == "corki-resource-package"


def test_post_commit_history_error_does_not_delete_live_resources(store, monkeypatch):
    identity = make(store)
    remove_live(store)
    original = store.write_config

    def after_commit(*args):
        original(*args)
        raise OSError("fixture history failure")

    monkeypatch.setattr(store, "write_config", after_commit)
    with pytest.raises(ValueError, match="已保留导入资源"):
        store.mutate({"kind": "resource-packages", "action": "import", "id": identity})
    assert (store.home / "skills/fixture-skill/SKILL.md").exists()
    assert store.hooks_document()["entries"][0]["trusted"]


def test_import_resource_names_are_checked_again_after_preview(store):
    identity = make(store)
    remove_live(store)
    request = {"kind": "resource-packages", "id": identity}
    assert len(store.mutate({**request, "action": "preview-import"})["available"]) == 3
    config = document.loads(store.config_text())
    config["mcp"] = {
        "servers": {
            "fixture-mcp": {
                "transport": "stdio",
                "command": "python",
                "args": ["-V"],
                "enabled": False,
            }
        }
    }
    store.config.write_text(json.dumps(config))
    result = store.mutate({**request, "action": "import", "enable_after_import": True})
    assert result["skipped"] == [{"kind": "mcp", "name": "fixture-mcp"}]
    assert document.loads(store.config_text())["mcp"]["servers"]["fixture-mcp"]["enabled"] is False


def test_assets_only_bundle_can_be_imported_once_without_config_changes(store, tmp_path):
    created = store.mutate(
        {
            "kind": "resource-packages",
            "action": "create",
            "name": "Assets only",
            "files": [{"path": "guide.txt", "data": base64.b64encode(b"fixture").decode()}],
        }
    )
    raw, _ = store.resource_packages.export([created["id"]])
    home = tmp_path / "assets-destination"
    home.mkdir()
    (home / "settings.json").write_text(store.config_text())
    other = module.Store(home, store.workspace, tmp_path / "other-shared-assets")
    before = other.config_text()
    payload = {"kind": "resource-packages", "archive": base64.b64encode(raw).decode()}
    assert other.mutate({**payload, "action": "preview-import"})["can_import"]
    other.mutate({**payload, "action": "import"})
    assert other.config_text() == before
    assert not other.mutate({**payload, "action": "preview-import"})["can_import"]


def test_http_create_large_resource_and_export(store):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.daemon_threads = True
    server.store = store
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = {
            "kind": "resource-packages",
            "action": "create",
            "name": "HTTP fixture",
            "files": [{"path": "large.txt", "data": base64.b64encode(b"x" * 600000).decode()}],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/mutate",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Corki-Manager": "1",
                "X-Corki-Skill-Import": "1",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/resource-package-export?id=" + result["id"]
        ) as response:
            assert response.headers["Content-Type"] == "application/zip"
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                assert archive.read("resources/local/large.txt") == b"x" * 600000
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_portable_local_resource_with_spaces_is_copied_and_resolved(store):
    files = {
        "mcp.json": json.dumps(
            {
                "mcpServers": {
                    "portable-mcp": {
                        "command": "python",
                        "args": ["${PACKAGE_ROOT}/resources/local/中文 文件.py"],
                    }
                }
            }
        ),
        "中文 文件.py": 'print("fixture")',
    }
    created = store.mutate(
        {
            "kind": "resource-packages",
            "action": "create",
            "name": "Portable fixture",
            "files": [
                {"path": path, "data": base64.b64encode(value.encode()).decode()}
                for path, value in files.items()
            ],
        }
    )
    store.mutate({"kind": "resource-packages", "action": "import", "id": created["id"]})
    mcp = document.loads(store.config_text())["mcp"]["servers"]["portable-mcp"]
    assert Path(mcp["args"][0]).read_text() == 'print("fixture")'
    store.mutate({"kind": "resource-packages", "action": "delete", "id": created["id"]})
    assert Path(mcp["args"][0]).is_file()


def test_invalid_existing_skill_is_still_skipped_by_name(store):
    identity = make(store)
    (store.home / "skills/fixture-skill/SKILL.md").write_text("invalid frontmatter")
    plan = store.mutate({"kind": "resource-packages", "action": "preview-import", "id": identity})
    assert not plan["can_import"]
    assert {"kind": "skills", "name": "fixture-skill"} in plan["skipped"]


def test_create_skips_local_virtual_environment_without_losing_skill_sources(store):
    skill = store.home / 'skills/fixture-skill'
    environment = skill / '.venv/bin'
    environment.mkdir(parents=True)
    executable = store.home / 'fixture-python'
    executable.write_text('local runtime, not skill source')
    try:
        (environment / 'python').symlink_to(executable)
    except OSError:
        pytest.skip('symbolic links unavailable')
    (environment / 'runtime-data').write_text('must not be packaged')
    identity = make(store)
    raw, _ = store.resource_packages.export([identity])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert 'skills/fixture-skill/SKILL.md' in archive.namelist()
        assert 'skills/fixture-skill/scripts/fixture.py' in archive.namelist()
        assert not any('.venv' in Path(name).parts for name in archive.namelist())
    assert (environment / 'python').is_symlink()


def test_create_rejects_actual_resource_symlink_and_identifies_relative_path(store):
    skill = store.home / 'skills/fixture-skill'
    target = store.home / 'outside-resource'
    target.write_text('not selected')
    try:
        (skill / 'scripts/linked.py').symlink_to(target)
    except OSError:
        pytest.skip('symbolic links unavailable')
    with pytest.raises(ValueError, match=r'符号链接：scripts/linked.py'):
        make(store)
    assert not store.resource_packages.listing()['packages']


def test_inventory_groups_external_skills_and_blocks_builtin_selection(store):
    builtin = store.home / 'skills/.system/fixture-builtin'
    public = store.compatibility_home / '.agents/skills/fixture-public'
    for path, name in ((builtin, 'fixture-builtin'), (public, 'fixture-public')):
        path.mkdir(parents=True, exist_ok=True)
        (path/'SKILL.md').write_text(f'---\nname: {name}\ndescription: Fixture\n---\nBody\n')
    inventory=store.resource_packages.inventory()['skills']
    assert any(row['name']=='fixture-skill' and row['group']=='corki' for row in inventory)
    assert any(row['name']=='fixture-public' and row['group']=='public' for row in inventory)
    assert all(row['group']!='builtin' for row in inventory)
    identity=next(row['id'] for row in store.files() if row['name']=='fixture-builtin')
    with pytest.raises(ValueError,match='内置 Skills 不能添加到插件'):
        store.resource_packages.create({'name':'Forbidden fixture','selection':{'skills':[identity]}})
    assert not store.resource_packages.listing()['packages']
