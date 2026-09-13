"""Storage failure cannot manufacture model-item or Turn completion events."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import AssistantMessageCompleted, TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("after_commit", [False, True])
def test_item_completion_waits_for_successful_commit_ack(
    tmp_path, monkeypatch, streamed, after_commit
):
    async def scenario():
        samples = []

        class Model:
            async def stream(self, request):
                item = AssistantMessageItem("answer", request.items[-1].turn_id, new_step_id())
                samples.append(item)
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
        )
        repository = runtime._repository
        method = "append_partial_item" if streamed else "commit_model_step"
        original = getattr(repository, method)

        async def fail(*args, **kwargs):
            if after_commit:
                await original(*args, **kwargs)
            raise OSError("model commit acknowledgement failed")

        monkeypatch.setattr(repository, method, fail)
        try:
            events = [event async for event in runtime.stream("answer once")]
            assert isinstance(events[-1], TurnFailed)
            assert "model commit acknowledgement failed" in events[-1].error
            assert not any(
                isinstance(e, (AssistantMessageCompleted, TurnCompleted)) for e in events
            )
            assert len(samples) == 1
            history = await repository.load_items(runtime.thread_id)
            assert (samples[0] in history) is after_commit
            saved = await repository.load_model_step(runtime.thread_id, events[-1].turn_id, 0)
            assert (saved is not None) is (after_commit and not streamed)
            partial = await repository.load_partial_step(runtime.thread_id, events[-1].turn_id, 0)
            assert partial == ((samples[0],) if after_commit and streamed else ())
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
