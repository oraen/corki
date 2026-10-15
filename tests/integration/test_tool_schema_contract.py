"""Declared schema constraints reject calls before any handler side effect."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("items_schema", [None, {}, {"type": "integer"}])
@pytest.mark.parametrize("values", [[], [1], [1, 2], [1, 2, 3]])
def test_array_bounds_apply_independently_of_item_schema(tmp_path, items_schema, values):
    schema = {"type": "array", "minItems": 1, "maxItems": 2}
    if items_schema is not None:
        schema["items"] = items_schema
    _assert_contract(tmp_path, schema, values, 1 <= len(values) <= 2, "items")


@pytest.mark.parametrize(
    "allowed,value,valid",
    [
        (1, True, False),
        (False, 0, False),
        ([1], [True], False),
        ({"nested": [0]}, {"nested": [False]}, False),
        (1, 1.0, True),
        ([1], [1.0], True),
        ({"a": 1, "b": False}, {"b": False, "a": 1.0}, True),
        (True, True, True),
        (None, None, True),
        ("1", 1, False),
    ],
)
def test_enum_uses_json_value_equality(tmp_path, allowed, value, valid):
    _assert_contract(tmp_path, {"enum": [allowed]}, value, valid, "one of")


@pytest.mark.parametrize("value", [None, "text", 1, False, [], {}])
def test_nullable_string_rejects_other_types_before_execution(tmp_path, value):
    _assert_contract(
        tmp_path,
        {"type": ["string", "null"]},
        value,
        value is None or isinstance(value, str),
        "must be",
    )


@pytest.mark.parametrize(
    "schema,value,valid,error_text",
    [
        ({"type": ["integer", "null"]}, True, False, "must be"),
        ({"type": ["number", "null"]}, False, False, "must be"),
        ({"type": ["integer", "boolean"]}, True, True, ""),
        ({"type": ["number", "null"]}, 1.5, True, ""),
        ({"type": ["number", "null"], "minimum": 1}, 0, False, ">="),
        ({"type": ["array", "null"], "maxItems": 1}, [1, 2], False, "items"),
        ({"type": ["string", "null"], "enum": ["yes"]}, None, False, "one of"),
        ({"type": "array", "items": {"type": ["string", "null"]}}, [1], False, "must be"),
        ({"type": "array", "items": {"type": ["string", "null"]}}, [None, "ok"], True, ""),
    ],
)
def test_union_types_preserve_nested_and_sibling_constraints(
    tmp_path, schema, value, valid, error_text
):
    _assert_contract(tmp_path, schema, value, valid, error_text)


@pytest.mark.parametrize(
    "types", [[], ["string", "unknown"], ["string", {}], "unknown", None, False, 1, 1.5, {}]
)
def test_unsupported_type_declaration_does_not_allow_execution(tmp_path, types):
    _assert_contract(tmp_path, {"type": types}, "text", False, "unsupported type declaration")


def _assert_contract(tmp_path, schema, values, valid, error_text):
    async def scenario():
        effects, requests = [], []

        class Tool:
            spec = ToolSpec(
                "bounded",
                "Write a bounded batch",
                {"type": "object", "properties": {"values": schema}, "required": ["values"]},
            )

            async def execute(self, call, context):
                effects.append(call.arguments["values"])
                return ToolResult(call.id, call.name, "accepted")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    item = ToolCallItem(
                        ToolCall(new_tool_call_id(), "bounded", {"values": values}), turn, step
                    )
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert result.is_error is not valid
                    assert effects == ([values] if valid else [])
                    if not valid:
                        assert "arguments.values" in result.content
                        assert error_text in result.content
                    item = AssistantMessageItem("handled", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("apply the batch")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            results = [
                item
                for item in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(item, ToolResultItem)
            ]
            assert len(results) == 1
            assert results[0].is_error is not valid
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
