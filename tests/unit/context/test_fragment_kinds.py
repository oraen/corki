"""Producer declarations, typed durable fields, and pre-field checkpoint compatibility."""

import asyncio
import base64
from dataclasses import replace

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.context.world_state import changed_context_items, render_context_history
from corki.core.checkpoint import checkpoint_serializer
from corki.models.responses import _to_response_input
from corki.prompting import PromptAssembler, PromptContribution, PromptRole, PromptSlot, PromptStore
from corki.prompting.assembly import RenderedPrompt
from corki.protocol.items import ContextItem, ContextRole, item_from_payload, item_to_payload
from corki.storage import SQLiteSessionRepository

META = "internal_chat_message_metadata_passthrough"
# Produced by immutable141's installed serializer, before content_kind existed.
OLD_CONTEXT = (
    "x+ICk7Rjb3JraS5wcm90b2NvbC5pdGVtc6tDb250ZXh0SXRlbYmja2V5rnByb2plY3QuYWdlbnRz"
    "pHJvbGXHJwCTtGNvcmtpLnByb3RvY29sLml0ZW1zq0NvbnRleHRSb2xlpHVzZXKnY29udGVudKZs"
    "ZWdhY3mndHVybl9pZKR0dXJuomlkpWxvY2FsqmNyZWF0ZWRfYXS5MjAyNi0wOS0wOFQwMDowMDow"
    "MCswMDowMLBzbmFwc2hvdF9jb250ZW50wK9zb3VyY2VfaW5wdXRfaWTArnNuYXBzaG90X3N0YXRlwA=="
)


@pytest.mark.parametrize("kind", [None, "", "fixture.instructions"])
def test_kind_round_trip_budget_and_request_projection(kind):
    item = ContextItem("fixture", ContextRole.USER, "body", "turn", content_kind=kind)
    payload = item_to_payload(item)
    assert ("content_kind" in payload) == (kind is not None)
    assert item_from_payload("context", payload) == item
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(item)) == item
    wire = _to_response_input(item, audio_enabled=True)
    assert wire["content"] == [{"type": "input_text", "text": "body"}]
    assert META not in wire
    large = replace(item, content_kind="x" * 10000)
    assert estimate_item_tokens(large) == estimate_item_tokens(item)
    assert estimate_item_tokens(replace(large, content="", snapshot_state="hidden")) == 0


@pytest.mark.parametrize("bad", [1, False, [], {}, ("fixture",)])
@pytest.mark.parametrize("stage", ["contribution", "rendered", "item"])
def test_invalid_kinds_are_rejected_at_contract_boundaries(stage, bad):
    with pytest.raises(TypeError, match="content_kind must be a string or None"):
        if stage == "contribution":
            PromptContribution(
                "fixture",
                "memory/consolidation_inputs",
                PromptRole.USER,
                PromptSlot.EXTENSIONS,
                content_kind=bad,
            )
        elif stage == "rendered":
            RenderedPrompt(
                "fixture",
                PromptRole.USER,
                "body",
                PromptSlot.EXTENSIONS,
                0,
                False,
                content_kind=bad,
            )
        else:
            ContextItem("fixture", ContextRole.USER, "body", "turn", content_kind=bad)


def test_assembler_preserves_explicit_kind_without_key_or_tag_inference():
    contribution = PromptContribution(
        "project.agents",
        "memory/consolidation_inputs",
        PromptRole.USER,
        PromptSlot.EXTENSIONS,
        variables={"inputs": "<environment_context>body</environment_context>"},
        content_kind="fixture.declared",
    )
    assembler = PromptAssembler(PromptStore())
    fragment = assembler.assemble(
        base_template="agent/base", contributions=(contribution,)
    ).fragments[0]
    assert fragment.content_kind == "fixture.declared"
    fragment = assembler.assemble(
        base_template="agent/base", contributions=(replace(contribution, content_kind=None),)
    ).fragments[0]
    assert fragment.content_kind is None


def test_classification_only_change_and_role_revocation_keep_owned_kinds():
    old = ContextItem("fixture", ContextRole.DEVELOPER, "body", "old", content_kind="old.kind")
    new = replace(old, id="new", turn_id="new", content_kind="new.kind")
    updates = changed_context_items((old,), (new,), "new")
    assert len(updates) == 1 and updates[0].content_kind == "new.kind"
    assert changed_context_items((old, *updates), (new,), "again") == ()
    removal, addition = changed_context_items((old,), (replace(new, role=ContextRole.USER),), "new")
    assert (removal.role, removal.content_kind, removal.snapshot_content) == (
        ContextRole.DEVELOPER,
        "old.kind",
        "",
    )
    assert (addition.role, addition.content_kind) == (ContextRole.USER, "new.kind")
    assert render_context_history((old, removal, addition)) == (old, removal, addition)


def test_old_context_checkpoint_sqlite_and_legacy_projection_are_nonmutating(tmp_path):
    async def scenario():
        item = checkpoint_serializer().loads_typed(("msgpack", base64.b64decode(OLD_CONTEXT)))
        assert item == ContextItem(
            "project.agents",
            ContextRole.USER,
            "legacy",
            "turn",
            id="local",
            created_at="2026-09-08T00:00:00+00:00",
        )
        payload = item_to_payload(item)
        assert "content_kind" not in payload
        assert item.content_kind is None
        # Even a recognized key must not fabricate historical producer metadata.
        assert META not in _to_response_input(item, audio_enabled=True)
        repository = SQLiteSessionRepository(tmp_path / "s.db")
        try:
            await repository.create_thread("thread", tmp_path)
            await repository.append_items("thread", (item,))
            await repository.append_items("thread", (item_from_payload("context", payload),))
            assert await repository.load_items("thread") == (item,)
            removal = replace(item, id="removed", content="")
            projected = render_context_history((item, removal))
            assert projected[-1].content and projected[-1].content_kind is None
            assert item_to_payload(item) == payload and removal.content == ""
        finally:
            await repository.close()

    asyncio.run(scenario())
