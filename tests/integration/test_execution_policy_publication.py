"""Pending rule approvals use the latest complete policy for live publication."""

import asyncio
import json
import shlex

import pytest
from test_execution_policy_live_inheritance import (
    Model,
    child_source,
    live_policy,
    notices,
    run,
    runtime_for,
)
from test_execution_policy_live_inheritance import compiler as compiler

from corki.config import CorkiSettings
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.permissions import ExecutionPermissions
from corki.execution import rules as rule_writer
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import WarningEvent
from corki.protocol.model_authority import ModelAuthority


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("relation", ["narrower", "identical", "disjoint", "absolute"])
@pytest.mark.parametrize("first", ["parent", "child"])
@pytest.mark.parametrize("cancel_second", [False, True])
def test_redundant_pending_prefix_is_saved_without_redundant_live_publication(
    tmp_path, compiler, mode, relation, first, cancel_second, monkeypatch
):
    async def scenario():
        parent_model, child_model = Model(mode), Model(mode)
        parent = runtime_for(tmp_path, compiler, parent_model)
        child = None
        tasks = []
        gates = {name: asyncio.Event() for name in ("parent", "child")}
        entered = {name: asyncio.Event() for name in ("parent", "child")}
        proposals = {}
        write_entered, write_release = asyncio.Event(), asyncio.Event()
        original_write = rule_writer.run_owned
        write_count = 0

        async def held_second_write(*args, **kwargs):
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                write_entered.set()
                await write_release.wait()
            return await original_write(*args, **kwargs)

        if cancel_second:
            monkeypatch.setattr(rule_writer, "run_owned", held_second_write)
        wide_target, narrow_target = tmp_path / "wide", tmp_path / "narrow"
        child_prefix = {
            "narrower": ["touch", str(narrow_target)],
            "identical": ["touch"],
            "disjoint": ["mkdir"],
            "absolute": ["/usr/bin/touch"],
        }[relation]
        try:
            await run(parent, parent_model)
            child = runtime_for(
                tmp_path,
                compiler,
                child_model,
                name="child",
                settings=parent._settings,
                session_source=child_source(parent),
                inherited_exec_policy=live_policy(parent),
            )
            await run(child, child_model)

            def handler(runtime, name):
                async def respond(request):
                    proposal = request.params["_meta"]["execpolicy_amendment"]
                    proposals[name] = proposal
                    entered[name].set()
                    await gates[name].wait()
                    runtime.respond_execution_approval(
                        request.request_id, "accept", execpolicy_amendment=proposal
                    )

                return respond

            parent.set_execution_approval_handler(handler(parent, "parent"))
            child.set_execution_approval_handler(handler(child, "child"))
            tasks = [
                asyncio.create_task(
                    run(
                        parent,
                        parent_model,
                        {
                            "cmd": "touch " + shlex.quote(str(wide_target)),
                            "sandbox_permissions": "require_escalated",
                            "prefix_rule": ["touch"],
                        },
                    )
                ),
                asyncio.create_task(
                    run(
                        child,
                        child_model,
                        {
                            "cmd": child_prefix[0] + " " + shlex.quote(str(narrow_target)),
                            "sandbox_permissions": "require_escalated",
                            "prefix_rule": child_prefix,
                        },
                    )
                ),
            ]
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 5)
            assert proposals == {"parent": ["touch"], "child": child_prefix}
            assert not parent._process_manager.approvals.rules.prefixes
            first_index = 0 if first == "parent" else 1
            second = "child" if first == "parent" else "parent"
            gates[first].set()
            await asyncio.wait_for(asyncio.shield(tasks[first_index]), 5)
            assert (wide_target.exists(), narrow_target.exists()) == (
                first == "parent",
                first == "child",
            )
            assert parent._process_manager.approvals.rules.prefixes == (tuple(proposals[first]),)
            gates[second].set()
            if cancel_second:
                await asyncio.wait_for(write_entered.wait(), 5)
                second_runtime = child if second == "child" else parent
                await second_runtime.cancel_active()
                assert not second_runtime._active_run.done.is_set()
                write_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(tasks[1 - first_index]), 5)
                assert (wide_target.exists(), narrow_target.exists()) == (
                    first == "parent",
                    first == "child",
                )
            else:
                await asyncio.wait_for(asyncio.shield(tasks[1 - first_index]), 5)
                assert wide_target.exists() and narrow_target.exists()
            assert len((tmp_path / "home/rules/default.rules").read_text().splitlines()) == (
                1 if relation == "identical" else 2
            )
            # Native current-policy match runs after disk append under the lock.
            skip_second = relation == "identical" or (
                first == "parent" and relation in {"narrower", "absolute"}
            )
            expected = (tuple(proposals[first]),)
            if not skip_second:
                expected += (tuple(proposals[second]),)
            assert child._process_manager.approvals.rules.prefixes == expected
            await run(parent, parent_model)
            await run(child, child_model)
            for model in (parent_model, child_model):
                text = "\n".join(notices(model))
                for prefix in expected:
                    assert json.dumps(list(prefix)) in text
                if skip_second and relation != "identical":
                    assert json.dumps(proposals[second]) not in text
        finally:
            write_release.set()
            for gate in gates.values():
                gate.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if child is not None:
                await child.aclose()
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "case",
    [
        "user_allow",
        "user_prompt",
        "managed_allow",
        "managed_forbidden",
        "absolute",
        "exact_forbidden",
        "restricted_alias",
        "late_disk",
        "captured_allow",
        "safe_unmatched",
        "cyber",
        "disk_exact",
        "bad_user",
    ],
)
def test_host_publication_uses_full_captured_policy_not_disk_or_model_filter(
    tmp_path, compiler, mode, case
):
    async def scenario():
        home = tmp_path / "home"
        home.mkdir()
        (home / "rules").mkdir()
        source = home / "rules/initial.rules"
        allow = 'prefix_rule(pattern=["touch"], decision="allow")\n'
        text = ""
        if case in {
            "user_allow",
            "managed_forbidden",
            "absolute",
            "exact_forbidden",
            "restricted_alias",
            "captured_allow",
            "cyber",
        }:
            text = allow
        if case == "user_prompt":
            text = 'prefix_rule(pattern=["touch"], decision="prompt")\n'
        if case == "bad_user":
            text = "not valid rules !"
        if case == "exact_forbidden":
            text += 'prefix_rule(pattern=["/usr/bin/touch"], decision="forbidden")\n'
        if case == "restricted_alias":
            text += 'host_executable(name="touch", paths=["/different/touch"])\n'
        source.write_text(text)
        requirements = ()
        if case.startswith("managed_"):
            requirements = (
                ExecutionRequirementsLayer(
                    "managed",
                    json.dumps(
                        {
                            "rules": {
                                "prefix_rules": [
                                    {
                                        "pattern": [{"token": "touch"}],
                                        "decision": "allow"
                                        if case == "managed_allow"
                                        else "forbidden",
                                    }
                                ]
                            }
                        }
                    ),
                ),
            )
        model = Model(mode)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            model="fixture",
            tool_mode=mode,
            model_contexts=(
                ModelContextInfo(
                    model="fixture", activation_authority=ModelAuthority(cyber=case == "cyber")
                ),
            ),
            execution_permissions=ExecutionPermissions(
                compiler,
                tmp_path,
                '{"type":"read-only"}',
                requirements=requirements,
                approval_policy_json='"on-request"',
            ),
        )
        runtime = runtime_for(tmp_path, compiler, model, settings=settings)
        try:
            if case == "managed_allow":
                with pytest.raises(ValueError, match="not permitted in requirements.toml"):
                    await run(runtime, model)
                assert not model.requests and not (home / "rules/default.rules").exists()
                return
            await run(runtime, model)
            if case == "late_disk":
                source.write_text(allow)
            elif case == "captured_allow":
                source.write_text("")
            target = tmp_path / "host-approved"
            program = (
                "/usr/bin/touch"
                if case in {"absolute", "exact_forbidden", "restricted_alias"}
                else "printf"
                if case == "safe_unmatched"
                else "touch"
            )
            prefix = (program, str(target))
            if case == "disk_exact":
                (home / "rules/default.rules").write_text(
                    f'prefix_rule(pattern={json.dumps(list(prefix))}, decision="allow")\n'
                )
            warnings = []

            async def warn(message):
                warnings.append(message)

            # This is a host-native publication contract test. Model-owned tool
            # selection is covered by the simultaneous-approval scenarios above.
            await runtime._process_manager.approvals.rules.persist(
                runtime._settings.execution_permissions, prefix, warn
            )
            assert not warnings
            assert json.dumps(str(target)) in (home / "rules/default.rules").read_text()
            expected_publication = case not in {
                "user_allow",
                "managed_allow",
                "absolute",
                "captured_allow",
                "cyber",
            }
            assert runtime._process_manager.approvals.rules.prefixes == (
                (prefix,) if expected_publication else ()
            )
            await run(runtime, model)
            assert bool(notices(model)) is expected_publication
            if case in {"managed_forbidden", "exact_forbidden"}:
                await run(runtime, model, {"cmd": program + " " + shlex.quote(str(target))})
                assert not target.exists()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_missing_native_publication_ack_preserves_only_current_approval(
    tmp_path, compiler, mode, monkeypatch
):
    async def scenario():
        model = Model(mode)
        runtime = runtime_for(tmp_path, compiler, model)
        original = rule_writer.run_owned
        writes = []

        async def old_ack(*args, **kwargs):
            writes.append(json.loads(args[1]))
            response = json.loads(await original(*args, **kwargs))
            response["ok"].pop("execpolicy_amendment_published")
            return json.dumps(response).encode()

        monkeypatch.setattr(rule_writer, "run_owned", old_ack)

        async def respond(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["touch"]
            )

        runtime.set_execution_approval_handler(respond)
        first, second = tmp_path / "first", tmp_path / "second"
        try:
            events = await run(
                runtime,
                model,
                {
                    "cmd": "touch " + shlex.quote(str(first)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
            )
            assert first.exists() and len(writes) == 1
            assert (tmp_path / "home/rules/default.rules").is_file()
            assert any(
                isinstance(e, WarningEvent) and "current-policy publication" in e.message
                for e in events
            )
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(second))})
            assert not second.exists() and len(writes) == 1
            assert not runtime._process_manager.approvals.rules.prefixes
            assert not notices(model)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", [[], ["touch"]])
def test_bare_native_append_shape_retains_validation_and_write_semantics(
    tmp_path, compiler, prefix
):
    async def scenario():
        path = tmp_path / "default.rules"
        output = await rule_writer.run_owned(
            [str(compiler)],
            (
                json.dumps({"append_execpolicy": {"path": str(path), "prefix": prefix}}) + "\n"
            ).encode(),
            cwd=tmp_path,
            output_limit=4_000_000,
        )
        response = json.loads(output)
        if prefix:
            assert response["ok"] == {
                "execpolicy_amendment_written": True,
                "execpolicy_amendment_published": True,
            }
            assert path.read_text() == 'prefix_rule(pattern=["touch"], decision="allow")\n'
        else:
            assert "error" in response and not path.exists()

    asyncio.run(scenario())
