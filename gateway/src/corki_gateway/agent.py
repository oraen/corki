"""The sole Corki adapter: reuse runtime, permissions, tools and durable sessions."""

import asyncio
import json
from contextlib import aclosing
from dataclasses import asdict, replace
from pathlib import Path


class Agent:
    def __init__(self, host):
        from corki.storage import SQLiteSessionRepository

        self.host = host
        self.database = host.store.home / "sessions/corki.db"
        self.repository = SQLiteSessionRepository(self.database)

    async def run(self, thread, text, workdir, channel, *, attachments=(), image_positions=()):
        from corki.config.settings import CorkiSettings
        from corki.core.runtime import LangGraphRuntime
        from corki.protocol.events import (
            AssistantMessageCompleted,
            TurnCancelled,
            TurnCompleted,
            TurnFailed,
            UserInputRequested,
        )
        from corki.protocol.ids import ThreadId
        from corki.protocol.session_source import SessionSource

        from .model_selection import validate_selection
        from .reply_guidance import request_context

        config = (self.host.store.connection(channel) or {}).get("config", {})
        provider, model = config.get("agent_provider"), config.get("agent_model")
        settings = CorkiSettings.for_directory(
            Path(workdir),
            config_file=self.host.store.home / "settings.json",
            provider=provider or None,
            model=model or None,
        )
        if provider or model:
            validate_selection(json.loads(settings._provider_document), provider, model)
        if attachments and not settings.supports_image_input:
            raise ValueError("Agent 当前模型未声明支持图片输入，请切换支持图片的模型。")
        # A remote chat must eventually settle even during an indefinite network outage.
        settings = replace(
            settings, model_unbounded_connection_retries=False, model_request_max_retries=3
        )
        runtime_options = {}
        if (self.host.store.connection(channel) or {}).get("type_id") in (
            "wechat",
            "telegram",
            "whatsapp",
        ):
            from .wechat_file_tools import SendWeChatFile
            from .wechat_tools import SendWeChatImage

            runtime_options["additional_tools"] = (
                SendWeChatImage(self.host, channel, thread),
                SendWeChatFile(self.host, channel, thread),
                SendWeChatFile(self.host, channel, thread, video=True),
            )
        runtime = await LangGraphRuntime.acreate(
            request_context=request_context(config.get("reply_guidance", "")),
            settings=settings,
            database_path=self.database,
            repository=self.repository,
            home_path=self.host.store.home,
            thread_id=ThreadId(thread),
            session_source=SessionSource.from_startup_arg("gateway"),
            **runtime_options,
        )
        self.host.runtimes[thread] = runtime
        self.host.live[thread] = {}

        async def approval(request):
            identity = request.request_id
            self.host.interactions[identity] = {
                "thread": thread,
                "channel": channel,
                **asdict(request),
            }
            try:
                card = self.host.reply_cards.get(thread)
                if card:
                    card.waiting()
                await self.host.notify(
                    thread, "本次任务需要确认，请在 Corki 管理页面的转发网关中处理。"
                )
                await asyncio.Future()
            finally:
                self.host.interactions.pop(identity, None)

        runtime.set_execution_approval_handler(approval)
        runtime.set_mcp_elicitation_handler(approval)
        try:
            completed, answer = False, ""
            completed_messages = {}
            async with aclosing(
                runtime.stream(text, attachments=attachments, image_positions=image_positions)
            ) as events:
                async for event in events:
                    card = self.host.reply_cards.get(thread)
                    if card:
                        card.offer(event)
                    if isinstance(event, AssistantMessageCompleted):
                        completed_messages[str(event.turn_id)] = (event.item_id, event.text)
                    elif isinstance(event, UserInputRequested):
                        self.host.interactions[event.call_id] = {
                            "kind": "question",
                            "thread": thread,
                            "channel": channel,
                            "request_id": event.call_id,
                            **asdict(event),
                        }
                        await self.host.notify(
                            thread, "Agent 需要补充信息，请在 Corki 管理页面的转发网关中回答。"
                        )
                    elif isinstance(event, TurnFailed):
                        reason = {
                            "transport": "模型通信失败，请检查模型服务或网络后重试",
                            "connection": "无法连接模型服务，请检查模型地址或网络",
                            "authentication": "模型认证失败，请检查供应商密钥或登录状态",
                            "server": "模型服务暂时不可用，请稍后重试",
                            "rate_limit": "模型请求受到限流，请稍后重试",
                            "quota": "模型额度不足，请检查供应商账户",
                        }.get(event.error_kind)
                        raise ValueError(
                            "Agent " + reason
                            if reason
                            else "Agent 执行失败，请在会话日志中查看原因"
                        )
                    elif isinstance(event, TurnCancelled):
                        raise asyncio.CancelledError()
                    elif isinstance(event, TurnCompleted):
                        completed, answer = True, event.final_answer
                        delivery = self.host.deliveries.get(thread)
                        if delivery:
                            item_id, item_text = completed_messages.get(
                                str(event.turn_id), (None, None)
                            )
                            self.host.store.http_queue.bind_answer(
                                channel,
                                delivery[1]["id"],
                                thread,
                                item_id if item_text == answer else "",
                            )
            if not completed:
                raise ValueError("Agent 未返回完成状态")
            return answer or "本次任务已完成，没有文本回复。"
        finally:
            self.host.runtimes.pop(thread, None)
            self.host.live.pop(thread, None)
            for key, request in list(self.host.interactions.items()):
                if request["thread"] == thread:
                    self.host.interactions.pop(key, None)
            await asyncio.wait_for(runtime.aclose(), timeout=20)
