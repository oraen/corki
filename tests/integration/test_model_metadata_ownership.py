"""An admitted exact-model view must not become a prefix-matched catalog entry."""

import asyncio
from dataclasses import replace

import ormsgpack
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.checkpoint import checkpoint_serializer
from corki.core.graph import GraphRunContext
from corki.core.model_settings import bind_model_settings, capture_model_settings
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.settings import ModelSettingsSnapshot
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


def configured(tmp_path, model="large", **changes):
    return CorkiSettings(
        tmp_path,
        model=model,
        skills_enabled=False,
        include_environment_context=False,
        model_contexts=(ModelContextInfo("large", 200000), ModelContextInfo("small", 20000)),
        **changes,
    )


@pytest.mark.parametrize("other", ["large-preview", "vendor/large", "vendor/large-preview"])
def test_rebinding_exact_snapshot_does_not_shadow_current_catalog(tmp_path, other):
    old = configured(tmp_path)
    saved = capture_model_settings(old)
    current = replace(old, model="small", model_contexts=(ModelContextInfo("large", 3000),))
    bound = bind_model_settings(current, saved)
    assert bound.model_context_info("large").context_window == 200000
    assert bound.model_context_info(other) == current.model_context_info(other)
    assert bound.model_contexts == current.model_contexts
    assert capture_model_settings(bound) == saved


def test_saved_unknown_model_does_not_create_a_catalog_family(tmp_path):
    old = replace(configured(tmp_path, "unlisted"), context_window_tokens=20000)
    bound = bind_model_settings(configured(tmp_path, "small"), capture_model_settings(old))
    assert bound.model_context_info("unlisted").context_window == 20000
    assert bound.model_context_info("unlisted-preview").context_window == 272000
    assert bound.model_context_info("unlisted").used_fallback_model_metadata is True
    assert bound.model_context_info("unlisted-preview").used_fallback_model_metadata is True


def test_exact_snapshot_does_not_clear_current_overrides_for_other_models(tmp_path):
    saved = capture_model_settings(configured(tmp_path))
    current = replace(
        configured(tmp_path, "small"),
        model_context_window_override=3000,
        tool_output_token_limit=123,
    )
    bound = bind_model_settings(current, saved)
    assert bound.model_context_info("large") == saved.model_info
    assert bound.model_context_info("large-preview") == current.model_context_info("large-preview")


@pytest.mark.parametrize(
    "name,fallback",
    [
        ("large", False),
        ("large-preview", False),
        ("vendor/large", False),
        ("a/b/large", True),
        ("missing", True),
    ],
)
def test_resolution_records_provenance_without_changing_requested_identity(
    tmp_path, name, fallback
):
    selected = replace(configured(tmp_path), model=name)
    snapshot = capture_model_settings(selected)
    assert snapshot.model_info.model == name
    assert snapshot.model_info.used_fallback_model_metadata is fallback
    serde = checkpoint_serializer()
    restored = serde.loads_typed(serde.dumps_typed(snapshot))
    assert restored == snapshot
    assert ModelSettingsSnapshot.from_payload(snapshot.to_payload()) == snapshot


def test_legacy_snapshot_has_unknown_provenance_not_an_invented_catalog_match(tmp_path):
    payload = capture_model_settings(configured(tmp_path)).to_payload()
    payload["model_info"].pop("used_fallback_model_metadata", None)
    saved = ModelSettingsSnapshot.from_payload(payload)
    assert saved.model_info.used_fallback_model_metadata is None
    rebound = bind_model_settings(configured(tmp_path), saved)
    assert capture_model_settings(rebound).model_info.used_fallback_model_metadata is None
    serde = checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(saved)) == saved


def test_legacy_msgpack_metadata_arrays_and_missing_provenance_are_restored(tmp_path):
    saved = capture_model_settings(configured(tmp_path))
    serde = checkpoint_serializer()
    kind, data = serde.dumps_typed(saved)

    def legacy_extension(code, payload):
        if code != 2:
            return ormsgpack.Ext(code, payload)
        module, name, values = ormsgpack.unpackb(payload, ext_hook=legacy_extension)
        if (module, name) == ("corki.protocol.context", "ModelContextInfo"):
            values.pop("used_fallback_model_metadata")
        return ormsgpack.Ext(code, ormsgpack.packb((module, name, values)))

    # Preserve the actual JsonPlus extension encoding, removing only the field
    # not present in batch156 checkpoints. Do not replace decoding with a mock.
    old_data = ormsgpack.packb(ormsgpack.unpackb(data, ext_hook=legacy_extension))
    restored = serde.loads_typed((kind, old_data))
    assert restored == replace(
        saved, model_info=replace(saved.model_info, used_fallback_model_metadata=None)
    )


@pytest.mark.parametrize("bad", [0, "false", {}, []])
def test_invalid_fallback_provenance_is_not_coerced(tmp_path, bad):
    payload = capture_model_settings(configured(tmp_path)).to_payload()
    payload["model_info"]["used_fallback_model_metadata"] = bad
    with pytest.raises(ValueError, match="used_fallback_model_metadata"):
        ModelSettingsSnapshot.from_payload(payload)


class Model:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


async def runtime(tmp_path, settings, model, thread=None):
    return await LangGraphRuntime.acreate(
        settings=settings,
        model=model,
        database_path=tmp_path / "sessions.db",
        thread_id=thread,
        home_path=tmp_path / "home",
        registry=ToolRegistry(),
    )


def fallback_warnings(events):
    return [e for e in events if isinstance(e, WarningEvent) and "fallback metadata" in e.message]


def test_unknown_warning_is_admitted_turn_owned_and_repeated_for_later_turns(tmp_path):
    async def scenario():
        model = Model()
        host = await runtime(tmp_path, configured(tmp_path, "unlisted"), model)
        try:
            stream = host.stream("first")
            first = await anext(stream)
            # A paused observer must not report the subsequent thread default.
            await host.update_thread_settings(model="large")
            events = [first, *[event async for event in stream]]
            assert isinstance(events[-1], TurnCompleted)
            warnings = fallback_warnings(events)
            assert len(warnings) == 1 and "`unlisted`" in warnings[0].message
            assert warnings[0].turn_id == first.turn_id
            known = [event async for event in host.stream("known")]
            assert not fallback_warnings(known)
            await host.update_thread_settings(model="unlisted")
            again = [event async for event in host.stream("unknown again")]
            assert len(fallback_warnings(again)) == 1
            assert [request.model for request in model.requests] == [
                "unlisted",
                "large",
                "unlisted",
            ]
        finally:
            await host.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("saved_unknown", [False, True])
def test_cold_pending_keeps_provenance_despite_catalog_replacement(tmp_path, saved_unknown):
    async def scenario():
        name = "unlisted" if saved_unknown else "large"
        old = await runtime(tmp_path, configured(tmp_path, name), Model())
        await old._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("durable admission", turn)
        state = _initial_state(old.thread_id, turn, old._settings, user)
        await old._repository.save_turn(
            TurnRecord(
                turn,
                old.thread_id,
                TurnStatus.RUNNING,
                user.content,
                model_settings=state["turn_model_settings"],
            )
        )
        await old.aclose()
        current = replace(
            configured(tmp_path, "small"), model_contexts=(ModelContextInfo(name, 3000),)
        )
        model = Model()
        cold = await runtime(tmp_path, current, model, old.thread_id)
        try:
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted)
            assert bool(fallback_warnings(events)) is saved_unknown
            assert model.requests[0].model_info.used_fallback_model_metadata is saved_unknown
            assert (
                cold._graph._settings.model_context_info(name + "-preview").context_window == 3000
            )
            assert cold.thread_settings.model == "small"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_cold_pending_previous_model_compaction_uses_current_catalog_not_admission_prefix(tmp_path):
    async def scenario():
        old_settings = replace(
            configured(tmp_path, "large-preview"),
            model_contexts=(ModelContextInfo("large", 200000, comp_hash="old"),),
        )
        old = await runtime(tmp_path, old_settings, Model())
        assert isinstance([event async for event in old.stream("old history")][-1], TurnCompleted)
        turn = new_turn_id()
        pending = replace(
            old_settings,
            model="large",
            model_contexts=(ModelContextInfo("large", 200000, comp_hash="new"),),
        )
        saved = capture_model_settings(pending)
        await old._repository.save_turn(
            TurnRecord(turn, old.thread_id, TurnStatus.RUNNING, "new input", model_settings=saved)
        )
        await old.aclose()
        model = Model()
        current = replace(
            configured(tmp_path, "small"),
            model_contexts=(ModelContextInfo("large", 30000, comp_hash="old"),),
        )
        cold = await runtime(tmp_path, current, model, old.thread_id)
        try:
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [r.model for r in model.requests] == ["large-preview", "large"]
            assert [r.model_info.context_window for r in model.requests] == [30000, 200000]
            assert "new input" not in str(model.requests[0].items)
            assert "new input" in str(model.requests[1].items)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["large", "unlisted"])
def test_checkpoint_only_snapshot_is_restored_without_business_ledger_backup(tmp_path, name):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        old = await runtime(tmp_path, configured(tmp_path, name), Model())
        await old._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("checkpoint input", turn)
        state = _initial_state(old.thread_id, turn, old._settings, user)
        # Legacy business row, but a current checkpoint: no ledger backup can
        # mask a serializer failure in the checkpoint's model metadata.
        await old._repository.save_turn(
            TurnRecord(turn, old.thread_id, TurnStatus.RUNNING, user.content)
        )
        await old._compiled.ainvoke(
            state,
            config=old._graph_config(turn),
            context=GraphRunContext(events=Sink()),
            interrupt_before=["call_model"],
        )
        checkpoint = await old._checkpointer.aget_tuple(old._graph_config(turn))
        assert (
            checkpoint.checkpoint["channel_values"]["turn_model_settings"]
            == state["turn_model_settings"]
        )
        await old.aclose()
        model = Model()
        current = replace(
            configured(tmp_path, "small"), model_contexts=(ModelContextInfo(name, 3000),)
        )
        cold = await runtime(tmp_path, current, model, old.thread_id)
        try:
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted)
            assert model.requests[0].model == name
            assert model.requests[0].model_info == state["turn_model_settings"].model_info
            assert model.requests[0].model_info.used_fallback_model_metadata is (name == "unlisted")
            inputs = [
                i
                for i in await cold._repository.load_items(old.thread_id)
                if isinstance(i, UserMessageItem)
            ]
            assert [i.content for i in inputs] == ["checkpoint input"]
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_present_but_null_checkpoint_snapshot_is_not_treated_as_legacy_absence(tmp_path):
    async def scenario():
        from types import SimpleNamespace

        host = await runtime(tmp_path, configured(tmp_path), Model())
        await host._ensure_ready()
        turn = new_turn_id()
        await host._repository.save_turn(
            TurnRecord(turn, host.thread_id, TurnStatus.RUNNING, "input")
        )
        original = host._checkpointer.aget_tuple

        async def corrupt(config):
            return SimpleNamespace(checkpoint={"channel_values": {"turn_model_settings": None}})

        host._checkpointer.aget_tuple = corrupt
        try:
            with pytest.raises(ValueError, match="invalid checkpoint model settings"):
                _ = [event async for event in host.resume_pending()]
            assert host._model.requests == []
        finally:
            host._checkpointer.aget_tuple = original
            await host.aclose()

    asyncio.run(scenario())
