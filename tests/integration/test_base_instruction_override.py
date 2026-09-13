"""Explicit host instructions take precedence over catalog and resumed history."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("accepted_turn", [False, True])
@pytest.mark.parametrize("same_instructions", [False, True])
def test_base_model_fallback_requires_different_instructions(
    tmp_path, accepted_turn, same_instructions
):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="BASE RULES"),
                ModelContextInfo(
                    "small",
                    200_000,
                    base_instructions="BASE RULES" if same_instructions else "SMALL RULES",
                ),
            ),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            await source._ensure_ready()
            if accepted_turn:
                assert isinstance([e async for e in source.stream("first")][-1], TurnCompleted)
            thread = source.thread_id
        finally:
            await source.aclose()
        model = Model()
        cold = make_runtime(
            tmp_path, model, configured=replace(configured, model="small"), thread=thread
        )
        try:
            assert isinstance([e async for e in cold.stream("cold")][-1], TurnCompleted)
            switches = [
                item
                for item in model.requests[-1].items
                if isinstance(item, ContextItem)
                and item.content_kind == "model_switch.instructions"
            ]
            assert len(switches) == int(accepted_turn or not same_instructions)
            assert model.requests[-1].instructions == "BASE RULES"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("provenance", [None, "custom"])
@pytest.mark.parametrize("matches_catalog", [False, True])
def test_legacy_base_origin_inferred_only_from_exact_current_catalog(
    tmp_path, provenance, matches_catalog
):
    async def scenario():
        text = "SMALL RULES" if matches_catalog else "UNKNOWN CUSTOM TEXT"
        configured = replace(
            settings(tmp_path),
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="LARGE RULES"),
                ModelContextInfo("small", 200_000, base_instructions="SMALL RULES"),
            ),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            await source._ensure_ready()
            thread = source.thread_id
            with source._repository._connect() as connection:
                connection.execute(
                    "UPDATE thread_base_instructions SET instructions=?, provenance=? "
                    "WHERE thread_id=?",
                    (text, provenance, str(thread)),
                )
        finally:
            await source.aclose()
        model = Model()
        runtime = make_runtime(
            tmp_path, model, configured=replace(configured, model="small"), thread=thread
        )
        try:
            await runtime._ensure_ready()
            # Change before any model section is persisted; only provenance can
            # establish the initial comparison identity in this scenario.
            await runtime.update_thread_settings(model="large")
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            assert model.requests[-1].instructions == text
            switches = [
                i
                for i in model.requests[-1].items
                if isinstance(i, ContextItem) and i.content_kind == "model_switch.instructions"
            ]
            assert len(switches) == int(provenance is None and matches_catalog)
            if switches:
                assert "LARGE RULES" in switches[0].content
            with runtime._repository._connect() as connection:
                row = connection.execute(
                    "SELECT model,instructions,provenance FROM thread_base_instructions "
                    "WHERE thread_id=?",
                    (str(thread),),
                ).fetchone()
            assert tuple(row) == ("large", text, provenance)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("absolute", [False, True])
def test_instruction_file_precedence_paths_and_frozen_runtime(tmp_path, absolute):
    async def scenario():
        config_dir = tmp_path / "config-source"
        config_dir.mkdir()
        instruction_file = config_dir / "rules.md"
        instruction_file.write_text(" \nFILE RULES\n ", encoding="utf-8")
        config = config_dir / "config.toml"
        path = str(instruction_file) if absolute else "rules.md"
        config.write_text(
            f"model_instructions_file = {json.dumps(path)}\ninstructions = 'TEXT FALLBACK'\n",
            encoding="utf-8",
        )
        configured = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            model="large",
            skills_enabled=False,
            include_environment_context=False,
        )
        assert configured.base_instructions == "FILE RULES"
        instruction_file.write_text("CHANGED AFTER CONFIG LOAD", encoding="utf-8")
        model = Model()
        runtime = make_runtime(tmp_path, model, configured=configured)
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert all(r.instructions == "FILE RULES" for r in model.requests)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["missing", "empty", "invalid_utf8", "non_string"])
def test_invalid_instruction_file_fails_before_runtime(tmp_path, failure):
    instruction_file = tmp_path / "rules.md"
    if failure == "empty":
        instruction_file.write_text(" \n\t", encoding="utf-8")
    elif failure == "invalid_utf8":
        instruction_file.write_bytes(b"\xff")
    config = tmp_path / "config.toml"
    value = "false" if failure == "non_string" else json.dumps(str(instruction_file))
    config.write_text(
        f"model_instructions_file = {value}\ninstructions = 'DO NOT FALL BACK'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="model_instructions_file"):
        CorkiSettings.for_directory(tmp_path, config_file=config)


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("override", ["HOST OVERRIDE", ""])
def test_explicit_instructions_override_catalog_and_history(tmp_path, resumed, override):
    async def scenario():
        catalog = (ModelContextInfo("large", 200_000, base_instructions="CATALOG BASE"),)
        model = Model()
        thread = None
        if resumed:
            source = make_runtime(
                tmp_path, model, configured=replace(settings(tmp_path), model_contexts=catalog)
            )
            try:
                assert isinstance([e async for e in source.stream("original")][-1], TurnCompleted)
                assert model.requests[-1].instructions == "CATALOG BASE"
                thread = source.thread_id
            finally:
                await source.aclose()
        config = tmp_path / "override.toml"
        config.write_text(f"instructions = {json.dumps(override)}\n", encoding="utf-8")
        configured = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            model="large",
            model_contexts=catalog,
            skills_enabled=False,
            include_environment_context=False,
        )
        runtime = make_runtime(tmp_path, model, configured=configured, thread=thread)
        try:
            assert isinstance([e async for e in runtime.stream("host")][-1], TurnCompleted)
            assert model.requests[-1].instructions == override
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert model.requests[-1].instructions == override
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = make_runtime(
                tmp_path,
                model,
                configured=replace(settings(tmp_path), model_contexts=catalog),
                thread=thread,
            )
            assert isinstance(
                [e async for e in runtime.stream("without override")][-1], TurnCompleted
            )
            assert model.requests[-1].instructions == ("CATALOG BASE" if resumed else override)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("target_override", [None, "FORK OVERRIDE"])
def test_fork_custom_base_origin_and_explicit_override(tmp_path, external, target_override):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            base_instructions="SOURCE CUSTOM",
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="LARGE CATALOG"),
                ModelContextInfo("small", 200_000, base_instructions="SMALL CATALOG"),
            ),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            # No prior model section: Custom provenance must not invent a switch.
            await source._ensure_ready()
            model = Model()
            target = await LangGraphRuntime.acreate(
                settings=replace(configured, model="small", base_instructions=target_override),
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / ("fork.db" if external else "sessions.db"),
                home_path=tmp_path / "home",
                fork_from_thread_id=source.thread_id,
                fork_source_repository=source._repository if external else None,
            )
            try:
                assert isinstance([e async for e in target.stream("fork")][-1], TurnCompleted)
                expected = target_override if target_override is not None else "SOURCE CUSTOM"
                assert model.requests[-1].instructions == expected
                assert not any(
                    isinstance(i, ContextItem) and i.content_kind == "model_switch.instructions"
                    for i in model.requests[-1].items
                )
                snapshot = await target._repository.load_fork_snapshot(target.thread_id)
                assert snapshot.base_instructions[1] == expected
                assert snapshot.base_instructions_provenance == "custom"
                original = await source._repository.load_fork_snapshot(source.thread_id)
                assert original.base_instructions == ("large", "SOURCE CUSTOM")
            finally:
                await target.aclose()
        finally:
            await source.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [False, 1, {}, []])
def test_host_instructions_reject_non_text(tmp_path, value):
    with pytest.raises(ValueError, match="instructions"):
        replace(settings(tmp_path), base_instructions=value)
