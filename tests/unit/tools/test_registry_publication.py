import weakref

import pytest

from corki.protocol.tools import ToolSpec
from corki.tools import ToolRegistry
from corki.tools.registry import DuplicateToolError


class Tool:
    def __init__(self, name, description):
        self.spec = ToolSpec(name, description, {"type": "object", "properties": {}})


def test_sealed_owner_replacement_publishes_handler_and_isolated_schema_together():
    registry = ToolRegistry()
    owner = registry.create_owner()
    old, new = Tool("candidate", "amber"), Tool("candidate", "cobalt")
    registry.replace_owned(owner, (old,))
    registry.seal()
    before = registry.specs()
    registry.replace_owned(owner, (new,))
    new.spec.parameters["properties"]["mutated"] = {"type": "string"}
    assert registry.get("candidate") is new
    assert registry.spec("candidate").description == "cobalt"
    assert registry.spec("candidate").parameters == {"type": "object", "properties": {}}
    assert before == (old.spec,)
    with pytest.raises(RuntimeError, match="sealed"):
        registry.register(Tool("foreign", ""))
    with pytest.raises(RuntimeError, match="sealed"):
        registry.create_owner()


def test_owner_cannot_overwrite_other_owners_or_ordinary_tools():
    registry = ToolRegistry()
    first, second = registry.create_owner(), registry.create_owner()
    ordinary, owned = Tool("ordinary", ""), Tool("owned", "")
    registry.register(ordinary)
    registry.replace_owned(first, (owned,))
    registry.seal()
    before = registry.specs()
    for name in ("ordinary", "owned"):
        with pytest.raises(DuplicateToolError):
            registry.replace_owned(second, (Tool("new", ""), Tool(name, "bad")))
        assert registry.specs() == before and registry.get("new") is None
        assert registry.owned_names(second) == frozenset()
    registry.replace_owned(first, ())
    assert registry.specs() == (ordinary.spec,)


def test_failed_schema_capture_does_not_publish_half_a_generation():
    registry = ToolRegistry()
    owner = registry.create_owner()
    old = Tool("old", "")
    registry.replace_owned(owner, (old,))
    registry.seal()

    class Broken:
        @property
        def spec(self):
            raise ValueError("fixture schema failure")

    with pytest.raises(ValueError, match="schema failure"):
        registry.replace_owned(owner, (Tool("new", ""), Broken()))
    assert registry.get("old") is old and registry.specs() == (old.spec,)
    assert registry.owned_names(owner) == frozenset({"old"})


def test_replacement_does_not_retain_old_handler_in_composition_map():
    registry = ToolRegistry()
    owner = registry.create_owner()
    tool = Tool("candidate", "")
    reference = weakref.ref(tool)
    registry.replace_owned(owner, (tool,))
    registry.seal()
    del tool
    registry.replace_owned(owner, ())
    assert reference() is None
