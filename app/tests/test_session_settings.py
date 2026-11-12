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
    ({"skills":False}, "skills_enabled", True),
    ({"skills":True}, "skills_enabled", True),

    ({"fast":False}, "service_tier", "default"),
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
            assert created[0].settings.skills_enabled is True
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
            assert (await service.validate_session({"threadId":"busy","sessionSettings":{}}))['model']=='plain'
            task=service.tasks.pop('busy');task.cancel();await asyncio.gather(task,return_exceptions=True)
        finally:
            await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stop", [False, True])
def test_running_turn_keeps_settings_and_steers_until_next_turn(service, stop):
    from test_followups import ControlledRuntime, wait_until

    async def scenario():
        created = []

        async def factory(**kw):
            runtime = ControlledRuntime(**kw)
            created.append(runtime)
            return runtime

        service.factory = factory
        original = (service.home / "settings.json").read_bytes()
        thread = str(uuid.uuid4())

        def request(content, options):
            return {"requestId": str(uuid.uuid4()), "threadId": thread,
                    "directory": str(service.home), "content": content,
                    "sessionSettings": options}

        options = {"provider": "beta", "model": "remote", "reasoning": "high",
                   "contextWindow": 32768, "skills": False, "memories": False}
        try:
            await service.submit(request("first", {}))
            runtime = created[0]
            await runtime.started.wait()
            admitted = service.active_settings[thread]
            # Saving pending defaults must not close an executing/background host.
            runtime.background = ("owned-terminal",)
            effective = await service.validate_session({"threadId": thread, "sessionSettings": options})
            assert effective["model"] == "remote"
            assert service.active_settings[thread] is admitted
            assert runtime.settings.model == "small" and not runtime.closed
            await service.write_state({"key": "draft:" + thread, "value": {"sessionSettings": options}})
            follow = request("supplement current task", options)
            from unittest.mock import patch
            # Even a provider deleted after admission cannot invalidate a text
            # steer; it still belongs to the already-running model/connection.
            with patch.object(service, "settings", side_effect=AssertionError("steer reread config")):
                a, b = await asyncio.gather(service.submit(follow), service.submit(follow))
            assert a == b and a["state"] == "accepted"
            assert runtime.steers == 1 and len(created) == 1
            assert service.active_models[thread][:2] == ("alpha", "small")
            assert runtime.settings.skills_enabled and runtime.settings.memories_use == admitted.memories_use
            runtime.background = ()
            if stop:
                await service.stop({"threadId": thread})
            else:
                runtime.release.set()
            await wait_until(lambda: thread not in service.tasks)
            assert thread not in service.active_settings
            assert (await service.read_state({"key": "draft:" + thread}))["sessionSettings"] == options
            await service.submit(request("next turn", options))
            await created[-1].started.wait()
            assert len(created) == 2 and runtime.closed
            assert created[-1].settings.model == "remote"
            assert created[-1].settings.provider_id == "beta"
            assert created[-1].settings.reasoning_effort == "high"
            assert created[-1].settings.main_context_limits.raw_tokens == 32768
            assert created[-1].settings.skills_enabled
            created[-1].release.set()
            await wait_until(lambda: not service.tasks)
            assert (service.home / "settings.json").read_bytes() == original
            # Pending choices survive host restart without becoming global defaults.
            restored = Service(service.home, service.send)
            assert (await restored.read_state({"key": "draft:" + thread}))["sessionSettings"] == options
            await restored.close()
        finally:
            for runtime in created:
                runtime.release.set()
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("active_images", [False, True])
def test_steered_images_use_the_admitted_provider_capabilities(service, active_images):
    import base64
    import io
    from PIL import Image
    from test_followups import ControlledRuntime

    async def scenario():
        config = configuration()
        config["providers"]["alpha"]["supports_image_input"] = active_images
        config["providers"]["beta"]["supports_image_input"] = not active_images
        (service.home / "settings.json").write_text(json.dumps(config))
        service.factory = lambda **kw: asyncio.sleep(0, result=ControlledRuntime(**kw))
        thread = str(uuid.uuid4())
        p = {"requestId": str(uuid.uuid4()), "threadId": thread, "content": "first", "sessionSettings": {}}
        try:
            await service.submit(p)
            runtime = service.runtimes[thread]
            await runtime.started.wait()
            image = io.BytesIO()
            Image.new("RGB", (1, 1)).save(image, format="PNG")
            p = {**p, "requestId": str(uuid.uuid4()), "content": "image follow-up",
                 "sessionSettings": {"provider": "beta", "model": "remote"},
                 "attachments": [{"name": "fixture.png", "type": "image",
                                  "data": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()}]}
            if active_images:
                assert (await service.submit(p))["state"] == "accepted"
            else:
                with pytest.raises(ValueError, match="图片输入"):
                    await service.submit(p)
            assert runtime.steers == int(active_images)
            assert runtime.settings.model == "small"
        finally:
            if service.runtimes.get(thread):
                service.runtimes[thread].release.set()
            await service.close()

    asyncio.run(scenario())


def test_manual_compaction_reuses_runtime_and_rejects_running_tasks(service):
    from corki.protocol.events import TurnStarted, TurnCompleted, ContextCompacted
    from corki.protocol.ids import TurnId
    from corki.sessions.models import TurnRecord, TurnStatus

    class CompactRuntime(FakeRuntime):
        compact_calls = 0
        async def compact(self):
            self.compact_calls += 1
            turn = TurnId(str(uuid.uuid4()))
            await self.repository.save_turn(TurnRecord(turn, self.thread, TurnStatus.RUNNING, ''))
            yield TurnStarted(self.thread, turn)
            await asyncio.sleep(.05)
            yield ContextCompacted(self.thread, turn, 100)
            await self.repository.save_turn(TurnRecord(turn, self.thread, TurnStatus.COMPLETED, ''))
            yield TurnCompleted(self.thread, turn, '')

    async def scenario():
        async def factory(**kw): return CompactRuntime(**kw)
        service.factory = factory
        thread = str(uuid.uuid4())
        initial = (service.home / 'settings.json').read_bytes()
        try:
            await service.submit({'threadId':thread,'requestId':str(uuid.uuid4()),'content':'fixture question','sessionSettings':{}})
            await asyncio.gather(*list(service.tasks.values()))
            runtime = service.runtimes[thread]
            p = {'threadId':thread,'requestId':str(uuid.uuid4())}
            a,b = await asyncio.gather(service.compact(p),service.compact(p))
            assert a['id'] == b['id']
            with pytest.raises(ValueError,match='当前任务'):
                await service.compact({**p,'requestId':str(uuid.uuid4())})
            await asyncio.gather(*list(service.tasks.values()))
            assert runtime.compact_calls == 1 and not runtime.closed
            assert (await service.compact(p))['state'] == 'completed'
            assert runtime.compact_calls == 1
            assert (service.home / 'settings.json').read_bytes() == initial
            with pytest.raises(ValueError):
                await service.compact({'threadId':str(uuid.uuid4()),'requestId':str(uuid.uuid4())})
        finally:
            await service.close()
    asyncio.run(scenario())


def test_fast_changes_runtime_signature_and_filters_unsupported_model(service):
    from session_settings import session_signature
    base = service.settings(service.home)
    with pytest.raises(ValueError, match="fast"):
        session_settings(base, {"fast": True})
    enabled = base
    disabled = session_settings(base, {"fast": False})
    assert session_signature(enabled) != session_signature(disabled)
    # Unknown models must not receive unsupported service tiers.
    assert enabled.session_service_tier is None
    document = configuration()
    document["providers"]["alpha"]["models"]["small"]["service_tiers"] = [{"id": "priority"}]
    (service.home / "settings.json").write_text(json.dumps(document))
    base = service.settings(service.home, refresh=True)
    assert session_settings(base, {"fast": True}).session_service_tier == "priority"
    assert session_settings(base, {"fast": False}).session_service_tier is None
    for invalid in (1, "true", None):
        with pytest.raises(ValueError, match="fast"):
            session_settings(base, {"fast": invalid})
