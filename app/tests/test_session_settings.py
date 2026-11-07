"""Conversation overrides reach runtime settings without changing global configuration."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from service import Service
from session_settings import session_settings
from test_bridge import FakeRuntime


def configuration():
    def model(window, reasoning=True):
        return {"context_window": window, "max_context_window": window * 2,
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}] if reasoning else []}
    return {"default": {"provider": "alpha", "model": "small"},
            "providers": {name: {"base_url": "http://127.0.0.1:1", "api_key": "fixture",
                "models": models} for name, models in {
                    "alpha": {"small": model(131072), "large": model(262144), "plain": model(65536, False)},
                    "beta": {"remote": model(65536)}}.items()}}


@pytest.fixture
def service(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps(configuration()), encoding="utf-8")
    async def send(value): pass
    return Service(tmp_path, send)


@pytest.mark.parametrize("options,field,expected", [
    ({"provider":"beta"}, "provider_id", "beta"),
    ({"provider":"beta"}, "model", "remote"),
    ({"model":"large"}, "model", "large"),
    ({"reasoning":"high"}, "reasoning_effort", "high"),
    ({"contextWindow":65536}, "model_context_window_override", 65536),
    ({"mode":"plan"}, "collaboration_mode", "plan"),
    ({"mode":"default"}, "collaboration_mode", "default"),
    ({"memories":True}, "memories_enabled", True),
    ({"memories":False}, "memories_use", False),
    ({"skills":False}, "skills_enabled", False),
    ({"skills":True}, "skills_enabled", True),
])
def test_each_override_is_effective_and_global_file_is_unchanged(service, options, field, expected):
    path=service.home/'settings.json'; before=path.read_bytes()
    result=session_settings(service.settings(service.home), options)
    assert getattr(result, field)==expected
    if "contextWindow" in options:
        assert result.main_context_limits.raw_tokens==options['contextWindow']
    assert path.read_bytes()==before


@pytest.mark.parametrize("permission,profile,approval", [
    ("read-only", ":read-only", "on-request"), ("workspace", ":workspace", "on-request"),
    ("full", ":danger-full-access", "never"),
])
def test_each_permission_keeps_host_constraints(service, permission, profile, approval):
    base=service.settings(service.home)
    settings=session_settings(base, {"permissions":permission})
    assert json.loads(settings.execution_permissions.profile_json)['default_permissions']==profile
    assert json.loads(settings.execution_permissions.approval_policy_json)==approval
    assert settings.execution_permissions.requirements==base.execution_permissions.requirements
    assert settings.execution_permissions.compiler==base.execution_permissions.compiler


@pytest.mark.parametrize("value", [None, [], {"notifications":False}, {"soundEnabled":False},
    {"contextWindow":True}, {"contextWindow":0}, {"contextWindow":9999999},
    {"reasoning":"invalid"}, {"model":"plain","reasoning":"high"},
    {"skills":"false"}, {"memories":1}, {"permissions":"invalid"}, {"mode":"invalid"},
    {"provider":"missing"}, {"model":"missing"}])
def test_invalid_or_global_only_fields_are_rejected(service, value):
    with pytest.raises(ValueError): session_settings(service.settings(service.home), value)


def test_save_preview_global_precedence_and_runtime_rebuild_are_isolated(service):
    async def scenario():
        created=[]
        async def factory(**kw):
            runtime=FakeRuntime(**kw);created.append(runtime);return runtime
        service.factory=factory
        initial=(service.home/'settings.json').read_bytes()
        options={"provider":"alpha","model":"large","reasoning":"high","contextWindow":65536,
                 "mode":"plan","permissions":"full","memories":True,"skills":False}
        preview=await service.validate_session({"directory":str(service.home),"sessionSettings":options})
        assert preview=={"provider":"alpha","model":"large","reasoning":"high","contextWindow":65536}
        async def submit(thread, overrides):
            receipt=await service.submit({"requestId":str(uuid.uuid4()),"threadId":thread,
                "directory":str(service.home),"content":"hello","sessionSettings":overrides})
            await asyncio.gather(*list(service.tasks.values()))
            return receipt
        try:
            await submit('first',options)
            assert created[0].settings.main_context_limits.raw_tokens==65536
            assert created[0].settings.collaboration_mode=='plan'
            assert created[0].settings.skills_enabled is False
            await submit('second',{})
            assert created[-1].settings.model=='small' and created[-1].settings.skills_enabled
            # Same options reuse the current host, changed options recreate only that host.
            count=len(created);await submit('first',options);assert len(created)==count
            await submit('first',{})
            assert created[0].closed and created[-1].settings.main_context_limits.raw_tokens==131072
            assert (service.home/'settings.json').read_bytes()==initial
            await service.write_state({"key":"draft:first","value":{"sessionSettings":options}})
            assert (await service.read_state({"key":"draft:first"}))['sessionSettings']==options
            # A global model change is used by inheriting sessions, while explicit choices win.
            config=configuration();config['default']['model']='plain'
            (service.home/'settings.json').write_text(json.dumps(config))
            assert (await service.validate_session({"sessionSettings":{}}))['model']=='plain'
            assert (await service.validate_session({"sessionSettings":options}))['model']=='large'
            service.tasks['busy']=asyncio.create_task(asyncio.sleep(10))
            with pytest.raises(ValueError,match='停止'):
                await service.validate_session({"threadId":"busy","sessionSettings":{}})
            task=service.tasks.pop('busy');task.cancel();await asyncio.gather(task,return_exceptions=True)
        finally:
            await service.close()
    asyncio.run(scenario())
