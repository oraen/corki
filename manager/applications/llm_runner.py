"""Direct model sampling: no Agent, tools, skills, hooks or memory service."""

import asyncio
import time
from contextlib import aclosing

from corki.context.tokens import estimate_request_tokens
from corki.models.factory import create_model
from corki.models.types import ModelCompleted, ModelRequest, ModelTextDelta
from corki.protocol.ids import new_model_step_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem
from corki.storage.usage_ledger import record_external_usage, register_usage_owner

from .models import AppError, now


async def run_llm(host, run, app, session, text, settings):
    step, turn = new_model_step_id(), new_turn_id()
    items = []
    if app["context"]:
        with host.store.db() as db:
            # Read only the most recent bounded candidates, never the whole transcript.
            rows = db.execute(
                (
                    "SELECT input,answer FROM runs WHERE session=? AND "
                    "status='completed' AND id!=? ORDER BY created DESC LIMIT 51"
                ),
                (session["id"], run["id"]),
            ).fetchall()
        if len(rows) > 50:
            host.publish(run["id"], notice="较早的对话未纳入本轮上下文")
        for row in reversed(rows[:50]):
            items.extend(
                (
                    UserMessageItem(content=row["input"], turn_id=turn),
                    AssistantMessageItem(content=row["answer"], turn_id=turn, step_id=step),
                )
            )
    current = UserMessageItem(content=text, turn_id=turn)
    items.append(current)
    budget = max(1, int(settings.main_context_limits.usable_tokens * 0.8))
    while len(items) > 1 and estimate_request_tokens(app["prompt"], items, ()) > budget:
        del items[:2]
        host.publish(run["id"], notice="较早的对话未纳入本轮上下文")
    if estimate_request_tokens(app["prompt"], items, ()) > budget:
        raise AppError("提示词和本轮输入超过模型上下文容量，请缩短内容或选择更大窗口的模型")
    request = ModelRequest(
        model=settings.model,
        instructions=app["prompt"],
        context_items=(),
        items=tuple(items),
        tools=(),
        reasoning_effort=settings.reasoning_effort,
        model_info=settings.model_context_info(settings.model),
        thread_id=session["id"],
    )
    model = (host.model_factory or create_model)(settings)
    complete = False
    usage_recorded = False
    flushed = time.monotonic()
    register_usage_owner(host.ledger, session["id"], kind="application", channel=app["id"])
    try:
        async with aclosing(model.stream(request)) as stream:
            async for event in stream:
                if isinstance(event, ModelTextDelta):
                    view = host.snapshot(run["id"])
                    answer = view["answer"] + event.delta
                    if len(answer) > 4_000_000:
                        raise AppError("模型输出过长，已保留接收到的内容")
                    host.publish(run["id"], answer=answer)
                    if time.monotonic() - flushed > 1:
                        host.store.update_run(run["id"], answer=answer)
                        flushed = time.monotonic()
                elif isinstance(event, ModelCompleted):
                    complete = True
                    answer = "\n\n".join(
                        i.content for i in event.items if isinstance(i, AssistantMessageItem)
                    )
                    if answer:
                        host.publish(run["id"], answer=answer)
                    await asyncio.to_thread(
                        record_external_usage,
                        host.ledger,
                        step_id=step,
                        thread_id=session["id"],
                        turn_id=turn,
                        created_at=now(),
                        provider=settings.provider_id,
                        model=settings.model,
                        usage=event.usage,
                    )
                    usage_recorded = True
        if not complete:
            raise AppError("模型连接提前结束，未收到完成标记")
    except Exception as error:
        if getattr(error, "usage", None) and not usage_recorded:
            await asyncio.to_thread(
                record_external_usage,
                host.ledger,
                step_id=step,
                thread_id=session["id"],
                turn_id=turn,
                created_at=now(),
                provider=settings.provider_id,
                model=settings.model,
                usage=error.usage,
            )
        raise
    finally:
        await model.aclose()
