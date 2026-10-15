"""Async IO capabilities do not move synchronous host contributors off their loop."""

import asyncio

import pytest

from corki.context import ContextBuilder
from corki.context.extensions import InputContextContributions
from corki.context.message_groups import freeze_context_messages
from corki.prompting import PromptContribution, PromptPhase, PromptRole, PromptSlot
from corki.protocol import InputMention
from corki.protocol.ids import new_turn_id


@pytest.mark.parametrize("include_input", [False, True])
def test_async_contributor_order_and_captured_input(tmp_path, include_input):
    async def scenario():
        loop = asyncio.get_running_loop()
        calls = []
        mentions = (InputMention("guide", "/fixture/SKILL.md", "skill"),)
        tool_snapshot = object()

        class Sync:
            def contributions(self, **kwargs):
                assert asyncio.get_running_loop() is loop
                calls.append("sync-world")
                return ()

            def input_contributions(self, **kwargs):
                assert asyncio.get_running_loop() is loop
                calls.append("sync-input")
                return ()

        class Async(Sync):
            async def async_contributions(self, **kwargs):
                assert kwargs == dict(cwd=tmp_path, user_input="input", realtime_active=False)
                await asyncio.sleep(0)
                calls.append("async-world")
                return ()

            async def async_input_contributions(self, **kwargs):
                assert kwargs == dict(
                    cwd=tmp_path, user_input="input", mentions=mentions, tool_snapshot=tool_snapshot
                )
                await asyncio.sleep(0)
                calls.append("async-input")
                return InputContextContributions(warnings=("read warning",))

        builder = ContextBuilder(contributors=(Sync(), Async(), Sync()))
        snapshot = await builder.build(
            cwd=tmp_path,
            turn_id=new_turn_id(),
            user_input="input",
            input_mentions=mentions,
            tool_snapshot=tool_snapshot,
            include_input_context=include_input,
        )
        assert calls == (
            ["sync-world", "sync-input", "async-world", "async-input", "sync-world", "sync-input"]
            if include_input
            else ["sync-world", "async-world", "sync-world"]
        )
        assert ("read warning" in snapshot.warnings) is include_input

    asyncio.run(scenario())


@pytest.mark.parametrize("full_permissions", [False, True])
def test_explicit_phase_overrides_legacy_slot_without_reordering_deltas(tmp_path, full_permissions):
    from corki.config import CorkiSettings

    async def scenario():
        def fragment(key, slot, phase):
            return PromptContribution(
                key,
                "modes/custom",
                PromptRole.DEVELOPER,
                slot,
                phase=phase,
                variables={"instructions": key},
            )

        class Host:
            def contributions(self, **kwargs):
                return (
                    fragment("thread", PromptSlot.SESSION, PromptPhase.EXTENSION),
                    fragment("world", PromptSlot.EXTENSIONS, PromptPhase.WORLD_STATE),
                    fragment("skills", PromptSlot.HOST_SKILLS, PromptPhase.WORLD_STATE),
                )

        class Tools:
            def step_contributions(self, **kwargs):
                return (fragment("tools", PromptSlot.EXTENSIONS, PromptPhase.WORLD_STATE),)

        snapshot = (
            await ContextBuilder(contributors=(Host(),), step_contributors=(Tools(),))
            .with_instruction_settings(
                CorkiSettings(
                    working_directory=tmp_path,
                    include_permissions_instructions=full_permissions,
                    execution_permissions=None,
                )
            )
            .build(cwd=tmp_path, turn_id=new_turn_id(), user_input="input")
        )
        assert snapshot.initial_extension_keys == frozenset({"thread"})
        keys = [item.key for item in snapshot.items]
        if full_permissions:
            assert keys.index("skills") < keys.index("permissions") < keys.index("mode.default")
        else:
            assert keys.index("mode.default") < keys.index("tools") < keys.index("skills")
        initial = freeze_context_messages(
            snapshot.items,
            initial=True,
            initial_extension_keys=snapshot.initial_extension_keys,
        )
        assert initial[0].key == "thread"
        delta = freeze_context_messages(
            snapshot.items,
            initial=False,
            initial_extension_keys=snapshot.initial_extension_keys,
        )
        assert [i.key for i in delta if not i.is_snapshot_only] == [
            i.key for i in snapshot.items if not i.is_snapshot_only
        ]

    asyncio.run(scenario())


def test_initial_extension_order_does_not_reclassify_world_input_or_tool_catalog(tmp_path):
    async def scenario():
        def contribution(key, slot=PromptSlot.EXTENSIONS, **kwargs):
            return PromptContribution(
                key,
                "modes/custom",
                kwargs.pop("role", PromptRole.DEVELOPER),
                slot,
                variables={"instructions": key},
                **kwargs,
            )

        class Host:
            def contributions(self, **kwargs):
                return (
                    contribution("memory", PromptSlot.SESSION),
                    contribution("extension"),
                    contribution("turn", PromptSlot.TURN),
                    contribution("user", role=PromptRole.USER),
                    contribution("standalone", separate_message=True),
                )

            def input_contributions(self, **kwargs):
                return (contribution("input", input_scoped=True),)

        class Catalog:
            def step_contributions(self, **kwargs):
                return (contribution("catalog"),)

        snapshot = await ContextBuilder(
            contributors=(Host(),), step_contributors=(Catalog(),)
        ).build(cwd=tmp_path, turn_id=new_turn_id(), user_input="input")
        assert snapshot.initial_extension_keys == frozenset({"extension", "turn"})
        initial = freeze_context_messages(
            snapshot.items, initial=True, initial_extension_keys=snapshot.initial_extension_keys
        )
        keys = [item.key for item in initial]
        assert keys[:2] == ["extension", "turn"]
        assert keys.index("memory") < keys.index("mode.default") < keys.index("catalog")
        delta = freeze_context_messages(
            snapshot.items, initial=False, initial_extension_keys=snapshot.initial_extension_keys
        )
        assert [item.key for item in delta] == [item.key for item in snapshot.items]

    asyncio.run(scenario())
