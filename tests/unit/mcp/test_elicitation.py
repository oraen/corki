import asyncio

import pytest

from corki.mcp.inbound import InboundService


@pytest.mark.parametrize("mode", ["legacy", "form", "url", "unknown_form", "invalid"])
def test_wire_request_without_host_declines_or_rejects_invalid_params(mode):
    async def scenario():
        replies = []

        async def send(message):
            replies.append(message)

        service = InboundService({}, send)
        params = {"message": "Input", "requestedSchema": {"type": "object", "properties": {}}}
        if mode != "legacy":
            params["mode"] = mode
        if mode == "url":
            params.update(url="https://fixture.invalid", elicitationId="server-token")
        if mode == "invalid":
            params.pop("requestedSchema")
        try:
            service.receive(
                {"jsonrpc": "2.0", "id": "7", "method": "elicitation/create", "params": params}
            )
            await asyncio.gather(*service._tasks)
            assert replies == [
                {
                    "jsonrpc": "2.0",
                    "id": "7",
                    **(
                        {"error": {"code": -32602, "message": "Invalid elicitation parameters"}}
                        if mode == "invalid"
                        else {"result": {"action": "decline"}}
                    ),
                }
            ]
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["accept", "decline", "cancel", "failure", "close"])
def test_host_routing_ownership_and_metadata(outcome, caplog):
    from corki.mcp.active_time import ActiveTime
    from corki.mcp.elicitation import ElicitationRouter

    async def scenario():
        queue, replies = asyncio.Queue(), []
        router, clock = ElicitationRouter(queue.put), ActiveTime()

        async def send(message):
            replies.append(message)

        service = InboundService(
            {}, send, server_name="server", elicitations=router, active_time=clock
        )
        if outcome == "failure":

            async def fail(request):
                raise OSError("PRIVATE_HOST_ERROR")

            router.handler = fail
        packet = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "elicitation/create",
            "params": {
                "message": "Input",
                "requestedSchema": {"type": "object", "properties": {}},
                "_meta": {"progressToken": 5, "secret": "HOST_ONLY"},
            },
        }
        try:
            service.receive(packet)
            if outcome != "failure":
                request = await asyncio.wait_for(queue.get(), 1)
                assert request.request_id != "1" and clock._pauses == 1
                assert request.params["_meta"] == {"secret": "HOST_ONLY"}
                assert packet["params"]["_meta"]["progressToken"] == 5
                with pytest.raises(ValueError):
                    router.respond("wrong", request.request_id, "accept")
                with pytest.raises(ValueError):
                    router.respond("server", request.request_id, "invalid")
                if outcome == "close":
                    await service.aclose()
                else:
                    router.respond("server", request.request_id, outcome, meta={"response": 1})
                with pytest.raises(ValueError):
                    router.respond("server", request.request_id, "accept")
            await asyncio.gather(*service._tasks, return_exceptions=True)
            assert not router._pending and clock._pauses == 0
            assert not clock._budgets
            if outcome == "failure":
                assert replies[0]["error"]["code"] == -32603
                assert "PRIVATE_HOST_ERROR" not in caplog.text
            elif outcome == "close":
                assert not replies
            else:
                result = {"action": outcome, "_meta": {"response": 1}}
                if outcome == "accept":
                    result["content"] = {}
                assert replies == [{"jsonrpc": "2.0", "id": 1, "result": result}]
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "metadata",
    [
        {"codex_approval_kind": "tool_suggestion"},
        {"codex_approval_kind": "mcp_tool_call"},
        {"codex_strict_auto_review": True},
        {"codex_strict_auto_review": "false"},
        {"codex_strict_auto_review": 0},
        {"codex_strict_auto_review": None},
    ],
)
def test_privileged_requests_do_not_become_ordinary_forms(metadata):
    from corki.mcp.elicitation import ElicitationRouter

    async def scenario():
        async def forbidden(request):
            raise AssertionError("privileged request must not reach ordinary host")

        router = ElicitationRouter(forbidden)
        assert await router.request("server", {"_meta": metadata}) == {"action": "decline"}

    asyncio.run(scenario())


def test_latest_handler_and_unique_tokens_across_servers_and_overlapping_requests():
    from corki.mcp.elicitation import ElicitationRouter

    async def scenario():
        first, second = asyncio.Queue(), asyncio.Queue()
        router = ElicitationRouter(first.put)
        a = asyncio.create_task(router.request("one", {}))
        ra = await first.get()
        router.handler = second.put
        b = asyncio.create_task(router.request("two", {}))
        rb = await second.get()
        barrier = asyncio.create_task(router.wait_until_clear())
        try:
            assert ra.request_id != rb.request_id
            router.respond("one", ra.request_id, "accept")
            await a
            assert not barrier.done()
            b.cancel()
            await asyncio.gather(b, return_exceptions=True)
            await asyncio.wait_for(barrier, 1)
            router.handler = None
            assert await router.request("one", {}) == {"action": "decline"}
        finally:
            for task in (a, b, barrier):
                task.cancel()
            await asyncio.gather(a, b, barrier, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("finish_paused", [False, True])
def test_counted_active_time_wait_and_remaining_timeout(finish_paused):
    from corki.mcp.active_time import ActiveTime

    async def scenario():
        clock, entered, finish = ActiveTime(), asyncio.Event(), asyncio.Event()

        async def operation():
            async with clock.timeout(0.08):
                entered.set()
                await finish.wait()

        task = asyncio.create_task(operation())
        await entered.wait()
        try:
            with clock.pause():
                with clock.pause():
                    await asyncio.sleep(0.1)
                await asyncio.sleep(0.1)
                assert not task.done() and clock._pauses == 1
                if finish_paused:
                    finish.set()
                    await task
            if not finish_paused:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(task, 0.3)
            assert not clock._budgets and clock._pauses == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["exec", "wait"])
def test_code_mode_captured_response_waits_for_every_host_request(control):
    from corki.code_mode.service import CodeModeService
    from corki.code_mode.tools import CodeModeExecTool, CodeModeWaitTool
    from corki.mcp.elicitation import ElicitationRouter
    from corki.protocol.ids import new_tool_call_id
    from corki.protocol.tools import ToolCall
    from corki.tools import ToolRegistry

    async def scenario():
        queue, captured = asyncio.Queue(), asyncio.Event()
        router = ElicitationRouter(queue.put)
        service = CodeModeService(ToolRegistry(), elicitations=router)
        a = asyncio.create_task(router.request("one", {}))
        b = asyncio.create_task(router.request("two", {}))
        ra, rb = await queue.get(), await queue.get()
        task = None
        try:
            if control == "exec":
                tool = CodeModeExecTool(service)
                call = ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    input_kind="freeform",
                    raw_arguments="text('captured output');",
                )
                original = service.execute
            else:
                await service.execute(
                    "initial",
                    "await new Promise(r => setTimeout(r, 50)); text('captured output');",
                    0,
                    1000,
                )
                cell_id = next(iter(service.cells))
                tool = CodeModeWaitTool(service)
                call = ToolCall(new_tool_call_id(), "wait", {"cell_id": cell_id})
                original = service.wait

            async def capture(*args):
                result = await original(*args)
                captured.set()
                return result

            if control == "exec":
                service.execute = capture
            else:
                service.wait = capture
            task = asyncio.create_task(tool.execute(call, None))
            await asyncio.wait_for(captured.wait(), 3)
            assert not task.done()
            router.respond(ra.server_name, ra.request_id, "accept")
            await a
            assert not task.done()
            router.respond(rb.server_name, rb.request_id, "cancel")
            await b
            result = await asyncio.wait_for(task, 1)
            assert "captured output" in result.content
        finally:
            for pending in (a, b, task):
                if pending is not None:
                    pending.cancel()
            await asyncio.gather(
                *(t for t in (a, b, task) if t is not None), return_exceptions=True
            )
            await service.aclose()

    asyncio.run(scenario())
