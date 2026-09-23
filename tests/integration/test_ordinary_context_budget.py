"""Archive-only metadata must not force ordinary runtime compaction."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.models.responses import _to_response_input
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.items import AssistantMessageItem, ReasoningItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("kind", [AssistantMessageItem, ReasoningItem])
@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("large_visible_body", [False, True])
@pytest.mark.parametrize("reported_usage", [None, 100])
def test_private_archive_metadata_cannot_force_summary(
    tmp_path, kind, cold, large_visible_body, reported_usage
):
    async def scenario():
        requests = []
        archived = None

        class Model:
            async def stream(self, request):
                nonlocal archived
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    archived = ModelCompleted(
                        (
                            kind(
                                "VISIBLE_BODY " * 30_000
                                if large_visible_body
                                else "small old message",
                                turn,
                                step,
                                response_item_metadata_json=json.dumps(
                                    {
                                        "id": "msg_old",
                                        "internal_chat_message_metadata_passthrough": {
                                            "turn_id": "x" * 100_000,
                                        },
                                    }
                                ),
                            ),
                        ),
                        usage=ModelUsage(total_tokens=reported_usage),
                    )
                    yield archived
                else:
                    yield ModelCompleted((AssistantMessageItem("final", turn, step),))

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    context_window_tokens=80_000,
                    auto_compact_tokens=20_000,
                ),
                database_path=tmp_path / "sessions.db",
                thread_id=thread,
                registry=ToolRegistry(),
                model=Model(),
            )

        runtime = await create()
        try:
            events = [event async for event in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted)
            thread = runtime.thread_id
            if cold:
                await runtime.aclose()
                runtime = await create(thread)
            events = [event async for event in runtime.stream("followup")]
            assert isinstance(events[-1], TurnCompleted)
            assert (
                any(isinstance(event, ContextCompacted) for event in events) is large_visible_body
            )
            assert len(requests) == (3 if large_visible_body else 2)
            old = next(item for item in requests[1].items if item.id == archived.items[0].id)
            # Media preparation may classify a model-visible copy; it must not
            # change ordinary wire content or the durable model-step below.
            assert _to_response_input(old) == _to_response_input(archived.items[0])
            assert "internal_chat_message_metadata_passthrough" not in _to_response_input(old)
            assert await runtime._repository.load_model_step(thread, old.turn_id, 0) == archived
            if large_visible_body:
                assert old.id not in {item.id for item in requests[-1].items}
                assert requests[1].tools == ()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
