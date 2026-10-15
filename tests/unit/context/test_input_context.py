import asyncio
from dataclasses import replace

import pytest

from corki.context import ContextBuilder, ContextSnapshot, ContextWindowManager, active_history
from corki.context.input_context import bind_input_context
from corki.context.world_state import changed_context_items
from corki.models import ModelCompleted
from corki.prompting import PromptContribution, PromptRole, PromptSlot
from corki.protocol.ids import new_item_id, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ContextRole,
    UserMessageItem,
    item_to_payload,
    new_step_id,
)
from corki.storage import SQLiteSessionRepository


def test_input_selection_can_pause_without_hiding_input_from_world_state_contributors(tmp_path):
    async def scenario():
        calls = []

        class Contributor:
            def contributions(self, *, cwd, user_input, realtime_active):
                calls.append(("world", user_input))
                return ()

            def input_contributions(self, *, cwd, user_input):
                calls.append(("input", user_input))
                return (
                    PromptContribution(
                        key="fixture.input",
                        template_name="extensions/skills/selected",
                        role=PromptRole.USER,
                        slot=PromptSlot.EXTENSIONS,
                        input_scoped=True,
                        variables={"name": "fixture", "path": "fixture", "contents": user_input},
                    ),
                )

        builder = ContextBuilder(contributors=(Contributor(),))
        turn = new_turn_id()
        initial = await builder.build(cwd=tmp_path, turn_id=turn, user_input="initial input")
        next_step = await builder.build(
            cwd=tmp_path, turn_id=turn, user_input="steered input", include_input_context=False
        )
        assert calls == [
            ("world", "initial input"),
            ("input", "initial input"),
            ("world", "steered input"),
        ]
        assert len(initial.input_items) == 1 and "initial input" in initial.input_items[0].content
        assert next_step.input_items == ()

    asyncio.run(scenario())


def test_legacy_selected_skill_is_not_revoked_or_replaced_as_world_state():
    turn = new_turn_id()
    old = ContextItem(
        "extensions.skills.selected.fixture", ContextRole.USER, "<skill>old</skill>", turn
    )
    replacement = replace(
        old,
        id=new_item_id(),
        content="old generated replacement notice",
        snapshot_content="<skill>new</skill>",
    )
    removed = replace(
        old, id=new_item_id(), content="old generated removal notice", snapshot_content=""
    )
    history = (old, replacement, removed)
    view = active_history(history)
    assert [i.content for i in view] == ["<skill>old</skill>", "<skill>new</skill>"]
    assert changed_context_items(history, (), new_turn_id()) == ()
    assert active_history(view) == view
    assert replacement.content == "old generated replacement notice"
    assert "source_input_id" not in item_to_payload(old)


@pytest.mark.parametrize("already_recorded", [False, True])
def test_preinput_compaction_preserves_new_skill_body_and_input_atomically(
    tmp_path, already_recorded
):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread, old_turn, turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        old = AssistantMessageItem("old history " * 350, old_turn, new_step_id())
        user = UserMessageItem("use explicit skill", turn)
        fragment = ContextItem("test.input", ContextRole.USER, "CURRENT SKILL BODY", turn)
        bound = bind_input_context((), (), (fragment,), (user,))
        initial = (old, user, *bound) if already_recorded else (old,)
        await repository.append_items(thread, initial)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted((AssistantMessageItem("summary", turn, new_step_id()),))

        manager = ContextWindowManager(
            repository=repository,
            model=Model(),
            model_name="fixture",
            context_window_tokens=3000,
            auto_compact_tokens=600,
        )
        snapshot = ContextSnapshot("base", (), tmp_path, input_items=(fragment,))
        try:
            prepared = await manager.prepare(
                thread_id=thread, turn_id=turn, snapshot=snapshot, tools=(), pending_items=(user,)
            )
            assert prepared.compacted and len(requests) == 1
            assert not any(isinstance(i, ContextItem) for i in requests[0].items)
            assert not any(
                isinstance(i, UserMessageItem) and i.content == user.content
                for i in requests[0].items
            )
            assert prepared.items[-2].content == user.content
            assert prepared.items[-1].content == fragment.content
            assert prepared.items[-1].source_input_id == user.id
            # Retry an old prepare checkpoint after the file/rendered body changed.
            changed = replace(snapshot, input_items=(replace(fragment, content="DO NOT REPLACE"),))
            repeated = await manager.prepare(
                thread_id=thread, turn_id=turn, snapshot=changed, tools=(), pending_items=(user,)
            )
            assert repeated.items == prepared.items
            stored = await repository.load_items(thread)
            assert stored[: len(initial)] == initial
            assert active_history(stored) == prepared.items
        finally:
            await repository.close()

    asyncio.run(scenario())
