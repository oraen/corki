"""Summary replacement must physically contain retained legacy pending input rows."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context import active_history
from corki.context.input_context import bind_input_context
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.context_messages import context_message_groups
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.parametrize("already_recorded", [False, True])
def test_token_budget_summary_pending_input_checkpoint_and_cold_resume(tmp_path, already_recorded):
    async def scenario():
        requests = []
        skill = tmp_path / ".corki/skills/fixture/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: fixture\ndescription: fixture\n---\nORIGINAL SKILL BODY")
        settings = CorkiSettings(
            working_directory=tmp_path,
            token_budget_enabled=True,
            context_window_tokens=40000,
            auto_compact_tokens=15000,
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "old " * 20000 if len(requests) == 1 else "done",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                model=Model(),
                database_path=tmp_path / "s.db",
                home_path=tmp_path / ".corki",
                thread_id=thread,
            )

        runtime = create()
        try:
            assert isinstance(
                [e async for e in runtime.stream("seed old window")][-1], TurnCompleted
            )
            thread, turn = runtime.thread_id, new_turn_id()
            user = UserMessageItem("use $fixture", turn)
            snapshot = await runtime._graph._context_builder.build(
                cwd=tmp_path,
                turn_id=turn,
                user_input=user.content,
            )
            bound = bind_input_context((), (), snapshot.input_items, (user,))
            assert len(bound) == 1 and "ORIGINAL SKILL BODY" in bound[0].content
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            if already_recorded:
                # Older checkpoint boundary: accepted input is already in the journal,
                # but pending_input_items still asks prepare to install it for sampling.
                await runtime._repository.append_items(thread, (user, *bound))
            before = await runtime._repository.load_items(thread)
            config = runtime._graph_config(turn)
            await runtime._compiled.ainvoke(
                _initial_state(thread, turn, settings, user),
                config=config,
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            checkpoint = await runtime._compiled.aget_state(config)
            assert checkpoint.next == ("call_model",)
            prepared = await runtime._repository.load_items(thread)
            visible = active_history(prepared)
            assert prepared[: len(before)] == before
            list(context_message_groups(visible))  # Membership remains complete after reload.
            users = [
                i for i in visible if isinstance(i, UserMessageItem) and i.content == user.content
            ]
            bodies = [
                i for i in visible if isinstance(i, ContextItem) and i.source_input_id == user.id
            ]
            assert len(users) == len(bodies) == 1
            assert users[0].content == user.content
            assert users[0].retained_from_id == (user.id if already_recorded else None)
            assert (users[0].id != user.id) == already_recorded
            assert "ORIGINAL SKILL BODY" in bodies[0].content
            assert bodies[0].message_group_id == bodies[0].id
            assert len(requests) == 2 and not requests[-1].tools
            assert "use $fixture" not in str(requests[-1].items)
            skill.write_text("---\nname: fixture\ndescription: fixture\n---\nCHANGED SKILL BODY")
            await runtime.aclose()
            runtime = create(thread)
            assert isinstance([e async for e in runtime.resume_pending()][-1], TurnCompleted)
            assert len(requests) == 3
            assert any(
                isinstance(i, UserMessageItem) and i.content == user.content
                for i in requests[-1].items
            )
            assert any(
                isinstance(i, ContextItem) and "ORIGINAL SKILL BODY" in i.content
                for i in requests[-1].items
            )
            assert not any(
                isinstance(i, ContextItem) and "CHANGED SKILL BODY" in i.content
                for i in requests[-1].items
            )
            assert (await runtime._repository.load_items(thread))[: len(prepared)] == prepared
            assert isinstance(
                [e async for e in runtime.stream("following turn")][-1], TurnCompleted
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
