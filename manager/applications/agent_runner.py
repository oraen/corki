"""Adapter over the existing runtime; no Agent loop is implemented here."""

import asyncio
from contextlib import aclosing, closing
from dataclasses import asdict

from corki.core.runtime import LangGraphRuntime
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    ContextCompacted,
    ContextCompactionStarted,
    ModelRetryScheduled,
    ToolCallCompleted,
    ToolCallStarted,
    ToolOutputDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    UserInputRequested,
    WarningEvent,
)
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import ContextItem, ContextRole
from corki.protocol.session_source import SessionSource
from corki.storage.usage_ledger import register_usage_owner

from .models import AppError


async def run_agent(host, run, app, session, text, settings, mentions):
    identity = run["id"]
    context = (
        ContextItem(
            key="host.application",
            role=ContextRole.DEVELOPER,
            content=(
                "Administrator application instructions. Keep existing execution "
                "and permission rules. During context summarization follow the "
                "dedicated summary instructions, not the user-facing reply format."
                "\n<application>\n"
            )
            + app["prompt"]
            + "\n</application>",
            turn_id=TurnId("application-guidance"),
            separate_message=True,
        ),
    )
    runtime = await (host.runtime_factory or LangGraphRuntime.acreate)(
        settings=settings,
        database_path=host.database,
        repository=host.repositories[0],
        home_path=host.store.home,
        thread_id=ThreadId(session["id"]),
        compatibility_home=host.compatibility_home,
        request_context=context,
        allow_self_evolution=False,
        session_source=SessionSource.from_startup_arg("application"),
    )
    host.runtimes[identity] = runtime

    async def approval(request):
        host.publish(
            identity,
            interaction=dict(
                id=request.request_id,
                kind=request.kind,
                server=request.server_name,
                params=request.params,
            ),
            status="waiting",
        )
        host.store.update_run(identity, status="waiting")
        try:
            await asyncio.Future()
        finally:
            current = host.live.get(identity, {}).get("interaction")
            if current and current["id"] == request.request_id:
                host.publish(identity, interaction=None)

    runtime.set_execution_approval_handler(approval)
    runtime.set_mcp_elicitation_handler(approval)
    complete = False
    try:
        async with aclosing(runtime.stream(text, mentions=mentions, realtime=True)) as stream:
            async for event in stream:
                view = host.live[identity]
                if isinstance(event, TurnStarted):
                    host.store.update_run(identity, core_turn=str(event.turn_id))
                elif isinstance(event, AssistantTextDelta):
                    item = event.item_id or "current"
                    messages = [dict(m) for m in view["messages"]]
                    if not messages or messages[-1]["id"] != item or not view.get("message_open"):
                        messages.append(dict(id=item, content=""))
                    messages[-1]["content"] += event.delta
                    if sum(len(m["content"]) for m in messages) > 4_000_000:
                        raise AppError("模型输出过长，已保存接收到的内容")
                    host.publish(identity, messages=messages, message_open=True)
                elif isinstance(event, AssistantMessageCompleted):
                    messages = [dict(m) for m in view["messages"]]
                    if messages and (
                        messages[-1]["id"] == (event.item_id or "current")
                        or (view.get("message_open") and messages[-1]["id"] == "current")
                    ):
                        messages[-1]["id"] = event.item_id or "current"
                        messages[-1]["content"] = event.text
                    else:
                        messages.append(dict(id=event.item_id or "current", content=event.text))
                    host.publish(identity, messages=messages, message_open=False)
                elif isinstance(event, ToolCallStarted):
                    tools = dict(view["tools"])
                    tools[str(event.tool_call_id)] = dict(
                        name=event.tool_name,
                        arguments=event.arguments_preview,
                        output="",
                        status="running",
                    )
                    host.publish(identity, tools=tools)
                elif isinstance(event, ToolOutputDelta):
                    tools = {k: dict(v) for k, v in view["tools"].items()}
                    tool = tools.get(str(event.tool_call_id))
                    if tool:
                        tool["output"] = (tool["output"] + event.delta)[-65536:]
                        host.publish(identity, tools=tools)
                elif isinstance(event, ToolCallCompleted):
                    tools = {k: dict(v) for k, v in view["tools"].items()}
                    tool = tools.setdefault(
                        str(event.tool_call_id), dict(name=event.tool_name, arguments="", output="")
                    )
                    tool["status"] = "failed" if event.is_error else "completed"
                    if event.mcp_error:
                        tool["output"] += event.mcp_error
                    host.publish(identity, tools=tools)
                elif isinstance(event, UserInputRequested):
                    host.publish(
                        identity,
                        interaction=dict(
                            id=event.call_id,
                            kind="question",
                            turn=str(event.turn_id),
                            questions=[asdict(q) for q in event.questions],
                        ),
                        status="waiting",
                    )
                    host.store.update_run(identity, status="waiting")
                elif isinstance(event, (WarningEvent, ModelRetryScheduled)):
                    host.publish(identity, notice=getattr(event, "message", "模型请求重试中"))
                elif isinstance(event, ContextCompactionStarted):
                    host.publish(identity, notice="正在压缩上下文")
                elif isinstance(event, ContextCompacted):
                    host.publish(identity, notice="")
                elif isinstance(event, TurnFailed):
                    raise AppError(event.error)
                elif isinstance(event, TurnCancelled):
                    raise asyncio.CancelledError()
                elif isinstance(event, TurnCompleted):
                    host.publish(identity, answer=event.final_answer)
                    complete = True
        if not complete:
            raise AppError("Agent 未返回完成状态")
    finally:
        host.runtimes.pop(identity, None)
        try:
            await asyncio.wait_for(runtime.aclose(), 25)
        finally:
            # The core owns execution history. Only content-free usage facts are
            # projected into the global ledger, with the original stable step IDs.
            await asyncio.to_thread(sync_usage, host, app, session)


def sync_usage(host, app, session):
    import sqlite3

    from .ownership import thread_family

    with closing(sqlite3.connect(host.database)) as source:
        family = thread_family(source, session["id"])
        for thread in family:
            register_usage_owner(host.ledger, thread, kind="application", channel=app["id"])
        with closing(sqlite3.connect(host.ledger, timeout=10)) as target, target:
            for thread in family:
                rows = source.execute("SELECT * FROM usage_events WHERE thread_id=?", (thread,))
                target.executemany(
                    "INSERT OR IGNORE INTO usage_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows
                )
