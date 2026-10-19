"""Quick setup replaces configuration; launch passes only quoted paths to the OS."""

import asyncio
import importlib.util
import json
import shlex
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("quick_manager_server", Path(__file__).parents[1] / "server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def store(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text('{"old_setting":true,"providers":{"old":{"models":{}}}}')
    return module.Store(home, tmp_path)


def payload(store):
    return {"kind": "quick-tools", "action": "configure", "provider": "qwen",
            "model": "qwen3.8-flash", "api_key": "fixture-qwen",
            "revision": module.revision(store.config_text())}


def add_payload(store, provider="qwen", models=None, key="fixture-qwen"):
    return {"kind": "quick-tools", "action": "add", "provider": provider,
            "models": models if models is not None else ["qwen3.8-flash"], "api_key": key,
            "revision": module.revision(store.config_text())}


def configured_launch(store):
    store.config.write_text(json.dumps({
        "default": {"provider": "fixture", "model": "demo"},
        "providers": {"fixture": {"base_url": "https://example.test/v1", "api_key": "fixture-key",
                                   "api_mode": "chat_completions", "models": {"demo": {}}}},
    }))


def test_template_replaces_config_and_selects_default(store):
    old = store.config_text()
    data = payload(store)
    store.mutate(data)
    config = json.loads(store.config_text())
    assert "old_setting" not in config and list(config["providers"]) == ["qwen"]
    assert config["default"] == {"provider": "qwen", "model": "qwen3.8-flash"}
    assert list(config["providers"]["qwen"]["models"]) == ["qwen3.8-flash"]
    assert store.config.stat().st_mode & 0o777 == 0o600
    assert any(json.loads(p.read_text())["content"] == old for p in store.config_versions().glob("*.json"))
    assert "fixture-" not in json.dumps(store.snapshot())


def test_quick_add_preserves_existing_config_and_default(store):
    configured_launch(store)
    original = '// keep this comment\n' + store.config_text()
    store.config.write_text(original)
    store.mutate(add_payload(store, models=["qwen3.8-flash", "qwen3.8-max"]))
    config = module.document.loads(store.config_text())
    assert config["default"] == {"provider": "fixture", "model": "demo"}
    assert config["providers"]["fixture"]["models"] == {"demo": {}}
    assert set(config["providers"]["qwen"]["models"]) == {"qwen3.8-flash", "qwen3.8-max"}
    assert config["providers"]["qwen"]["models"]["qwen3.8-flash"]["context_window"] == 262144
    assert "// keep this comment" in store.config_text()
    assert any(json.loads(p.read_text())["content"] == original for p in store.config_versions().glob("*.json"))
    saved_key = config["providers"]["qwen"]["api_key"]
    store.mutate(add_payload(store, models=["qwen3.7-plus", "qwen3.7-max"], key=""))
    config = module.document.loads(store.config_text())
    assert config["providers"]["qwen"]["api_key"] == saved_key
    assert set(config["providers"]["qwen"]["models"]) == {"qwen3.8-flash", "qwen3.8-max", "qwen3.7-plus", "qwen3.7-max"}
    assert config["default"] == {"provider": "fixture", "model": "demo"}


def test_quick_add_first_provider_sets_default(store):
    store.config.write_text("{}")
    store.mutate(add_payload(store))
    config = module.document.loads(store.config_text())
    assert config["default"] == {"provider": "qwen", "model": "qwen3.8-flash"}


def test_quick_add_rejects_duplicate_and_stale_revision(store):
    configured_launch(store)
    store.mutate(add_payload(store))
    original = store.config_text()
    with pytest.raises(ValueError, match="已存在"):
        store.mutate(add_payload(store))
    with pytest.raises(ValueError, match="密钥"):
        store.mutate(add_payload(store, models=["qwen3.7-plus"], key="replacement"))
    stale = add_payload(store, models=["qwen3.7-plus"], key="")
    store.config.write_text(store.config_text() + "\n")
    with pytest.raises(ValueError, match="更新"):
        store.mutate(stale)
    assert module.document.loads(store.config_text()) == module.document.loads(original)


def test_quick_add_rejects_any_duplicate_without_partial_write(store):
    configured_launch(store)
    store.mutate(add_payload(store))
    original = store.config_text()
    with pytest.raises(ValueError, match="已存在"):
        store.mutate(add_payload(store, models=["qwen3.8-flash", "qwen3.7-plus"], key=""))
    assert store.config_text() == original


@pytest.mark.parametrize("models", [[], ["qwen3.8-flash", "qwen3.8-flash"],
                                     ["unknown"], ["qwen3.8-flash", "unknown"]])
def test_quick_add_rejects_invalid_model_selection(store, models):
    configured_launch(store)
    original = store.config_text()
    with pytest.raises(ValueError, match="有效模型"):
        store.mutate(add_payload(store, models=models))
    assert store.config_text() == original


def test_every_template_loads_with_correct_runtime_transport(store):
    from corki.models import OpenAICompatibleModel, ModelRequest
    from corki.models.responses import OpenAIResponsesModel
    from corki.protocol.ids import new_turn_id
    from corki.protocol.items import UserMessageItem

    async def check():
        for provider in store.quick_templates()["providers"]:
            for model, metadata in provider["models"].items():
                store.quick_setup({"provider": provider["id"], "model": model, "api_key": "fixture-key",
                                   "revision": module.revision(store.config_text())})
                settings = module.CorkiSettings.for_directory(store.home, config_file=store.config)
                assert settings.model == model
                assert settings.reasoning_effort == metadata["reasoning_effort"]
                info = settings.model_context_info(model)
                assert info.context_window == 262144
                assert info.supported_reasoning_levels == tuple(p["effort"] for p in metadata["supported_reasoning_levels"])
                assert settings.provider_name == provider["id"]
                assert settings.api_key == "fixture-key"
                assert settings.api_base == provider["base_url"]
                adapter = module._create_model(settings, module._model_capabilities(settings))
                try:
                    assert isinstance(adapter, OpenAIResponsesModel if provider["id"] == "openai" else OpenAICompatibleModel)
                    request = ModelRequest(model=model, instructions="test", context_items=(),
                                           items=(UserMessageItem("hello", new_turn_id()),), tools=(), model_info=info,
                                           reasoning_effort=settings.reasoning_effort)
                    assert adapter._build_payload(request)["model"] == model
                    saved = json.loads(store.config_text())["providers"][provider["id"]]["models"][model]
                    assert "generation" not in saved and "available_before" not in saved
                    assert saved["description"] == metadata["description"]
                    assert saved["reasoning_effort"] == metadata["reasoning_effort"]
                    wire = adapter._build_payload(request)
                    expected = settings.reasoning_effort or info.default_reasoning_level
                    if provider["id"] == "openai":
                        assert wire["reasoning"]["effort"] == "medium"
                    elif expected is not None:
                        assert wire["reasoning_effort"] == expected
                    else:
                        assert "reasoning_effort" not in wire
                    for effort in info.supported_reasoning_levels:
                        wire = adapter._build_payload(replace(request, reasoning_effort=effort))
                        expected_effort = info.reasoning_effort_for_request(effort)
                        if provider["id"] == "openai":
                            assert wire["reasoning"]["effort"] == expected_effort
                        else:
                            assert wire["reasoning_effort"] == expected_effort
                finally:
                    await adapter.aclose()
    asyncio.run(check())


def test_catalog_has_ten_providers_and_three_generations_where_available(store):
    catalog = store.quick_templates(today="2026-10-14")
    assert len(catalog["providers"]) == 10
    for p in catalog["providers"]:
        assert len({m["generation"] for m in p["models"].values()}) == (2 if p["id"] == "deepseek" else 3)
        assert p["sources"]
        assert p["api_mode"] == ("responses" if p["id"] == "openai" else "chat_completions")
    future = store.quick_templates(today="2026-10-19")
    assert list(next(p for p in future["providers"] if p["id"] == "minimax")["models"]) == ["MiniMax-M3"]
    public = json.dumps(store.quick_catalog())
    assert "base_url" not in public and "api_mode" not in public and "api_key" not in public


def test_client_cannot_override_template_connection_or_default(store):
    data = payload(store)
    data.update(base_url="https://invalid.test", api_mode="responses", default={"provider": "other", "model": "other"})
    store.mutate(data)
    saved = json.loads(store.config_text())
    assert saved["providers"]["qwen"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert saved["providers"]["qwen"]["api_mode"] == "chat_completions"
    assert saved["default"] == {"provider": "qwen", "model": "qwen3.8-flash"}


def test_stale_setup_does_not_overwrite_external_edit(store):
    data = payload(store)
    store.config.write_text('{"external":true}')
    with pytest.raises(ValueError, match="冲突"):
        store.mutate(data)
    assert store.config_text() == '{"external":true}'


def test_model_save_rejects_unsupported_effort_without_changing_config(store):
    store.quick_setup({"provider": "kimi", "model": "kimi-k3", "api_key": "fixture-key",
                       "revision": module.revision(store.config_text())})
    old = store.config_text()
    with pytest.raises(ValueError, match="不支持所选推理等级"):
        store.mutate({"kind": "models", "action": "save", "provider": "kimi", "id": "kimi-k3",
                      "revision": module.revision(old),
                      "values": {"context_window": 262144, "reasoning_effort": "medium"}})
    assert store.config_text() == old


@pytest.mark.parametrize("key,value", [("api_key", ""), ("api_key", "invalid\nkey"),
    ("provider", "unknown"), ("provider", ["qwen", "kimi"]),
    ("model", ["qwen3.8-flash"]), ("model", "kimi-k3"), ("model", "unknown")])
def test_invalid_setup_does_not_change_file(store, key, value):
    data = payload(store)
    data[key] = value
    old = store.config_text()
    with pytest.raises(ValueError): store.mutate(data)
    assert store.config_text() == old


@pytest.mark.parametrize("iterm", [True, False])
def test_launch_quotes_directory_and_prefers_iterm(store, monkeypatch, tmp_path, iterm):
    configured_launch(store)
    folder = tmp_path / "project ' $(touch should-not-exist)"
    folder.mkdir()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/fixture/bin/corki")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0 if iterm else 1, "", ""))
    calls = []
    monkeypatch.setattr(store, "desktop_script", lambda script, args: calls.append((script, args)))
    result = store.quick_launch({"directory": str(folder)})
    script, args = calls[0]
    assert str(folder) not in script
    assert shlex.split(args[0]) == ["cd", "--", str(folder), "&&", "env", f"CORKI_HOME={store.home}", "/fixture/bin/corki"]
    assert result["terminal"] == ("iTerm" if iterm else "Terminal")
    assert ("com.googlecode.iterm2" if iterm else "com.apple.Terminal") in script


def test_default_directory_created_and_missing_custom_directory_rejected(store, monkeypatch):
    configured_launch(store)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/fixture/bin/corki")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    monkeypatch.setattr(store, "desktop_script", lambda *a, **k: "")
    assert store.quick_launch({})["directory"] == str(store.home / "main/default")
    assert (store.home / "main/default").is_dir()
    with pytest.raises(ValueError, match="不存在"):
        store.quick_launch({"directory": str(store.home / "missing")})
    assert not (store.home / "missing").exists()


def test_directory_picker_cancel_is_not_an_error(store, monkeypatch):
    monkeypatch.setattr(store, "desktop_script", lambda *a, **k: None)
    assert store.choose_launch_directory() == {"path": None, "cancelled": True}


@pytest.mark.parametrize("content", [
    "{not valid json", "{}",
    json.dumps({"providers": {"fixture": {"models": {"demo": {}}}}}),
    json.dumps({"default": {"provider": "missing", "model": "demo"}, "providers": {}}),
    json.dumps({"default": {"provider": "fixture", "model": "missing"},
                "providers": {"fixture": {"models": {"demo": {}}}}}),
])
def test_launch_rejects_invalid_or_incomplete_config_before_side_effects(store, monkeypatch, content):
    store.config.write_text(content)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/fixture/bin/corki")
    monkeypatch.setattr(store, "desktop_script", lambda *a, **k: pytest.fail("Opened terminal"))
    with pytest.raises(ValueError, match="配置无效，建议使用快速配置配置厂商和模型"):
        store.quick_launch({})
    assert not (store.home / "main/default").exists()


def test_desktop_launch_passes_directory_and_home_without_shell(store, monkeypatch, tmp_path):
    configured_launch(store)
    folder = tmp_path / "中文 项目 $(not-a-command)"
    folder.mkdir()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(store, "desktop_application", lambda: Path("/fixture/Corki.app/Contents/MacOS/Corki"))
    calls = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    result = store.quick_launch({"mode": "desktop", "directory": str(folder)})
    args, options = calls[0]
    assert args == ["/fixture/Corki.app/Contents/MacOS/Corki", "--corki-directory", str(folder)]
    assert options["env"]["CORKI_HOME"] == str(store.home)
    assert "ELECTRON_RUN_AS_NODE" not in options["env"]
    assert options["cwd"] == str(folder)
    assert options["start_new_session"]
    assert result["mode"] == "desktop"


def test_invalid_launch_mode_rejected(store):
    with pytest.raises(ValueError, match="启动方式"):
        store.quick_launch({"mode": "arbitrary-command"})
