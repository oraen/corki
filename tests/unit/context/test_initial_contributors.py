"""Initial-window ownership is a producer capability, not a memory key heuristic."""

import asyncio

import pytest

from corki.context import ContextBuilder
from corki.prompting import PromptContribution, PromptRole, PromptSlot
from corki.protocol.ids import new_turn_id


def test_deferred_initial_source_and_warning_refresh(tmp_path):
    async def scenario():
        calls = []

        class Source:
            initial_context_keys = ("host.initial",)

            async def async_contributions(self, **kwargs):
                calls.append("read")
                return (
                    PromptContribution(
                        "host.initial",
                        "modes/custom",
                        PromptRole.DEVELOPER,
                        PromptSlot.SESSION,
                        variables={"instructions": f"version-{len(calls)}"},
                        warnings=("initial warning",),
                    ),
                )

        snapshot = await ContextBuilder(contributors=(Source(),)).build(
            cwd=tmp_path, turn_id=new_turn_id(), defer_initial_context=True
        )
        assert not calls
        same_window = await snapshot.for_window(initial=False)
        assert not calls and "host.initial" in same_window.omitted_sections
        initial = await same_window.for_window(initial=True)
        assert calls == ["read"]
        assert initial.items[0].content == "version-1\n"
        assert "host.initial" not in initial.omitted_sections
        assert initial.section_warnings.count(("host.initial", "initial warning")) == 1
        fresh = await initial.for_window(initial=True)
        assert calls == ["read", "read"]
        assert fresh.items[0].content == "version-2\n"
        assert fresh.section_warnings.count(("host.initial", "initial warning")) == 1
        assert len([i for i in fresh.items if i.key == "host.initial"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["undeclared", "user", "input", "duplicate"])
def test_initial_source_contract_is_validated_before_publication(tmp_path, invalid):
    async def scenario():
        class Source:
            initial_context_keys = ("host.initial",)

            def contributions(self, **kwargs):
                item = PromptContribution(
                    "other" if invalid == "undeclared" else "host.initial",
                    "modes/custom",
                    PromptRole.USER if invalid == "user" else PromptRole.DEVELOPER,
                    PromptSlot.SESSION,
                    variables={"instructions": "body"},
                    input_scoped=invalid == "input",
                )
                return (item, item) if invalid == "duplicate" else (item,)

        snapshot = await ContextBuilder(contributors=(Source(),)).build(
            cwd=tmp_path, turn_id=new_turn_id(), defer_initial_context=True
        )
        with pytest.raises(ValueError):
            await snapshot.for_window(initial=True)

    asyncio.run(scenario())
