"""Public, authenticated application protocol; execution remains owned by Applications."""

import asyncio
import contextlib
import hmac
import json
import re
import sqlite3
import time
import uuid

from aiohttp import web

from .models import AppError
from .run_control import cancel_run, respond_run
from .run_view import run_view
from .store import ACTIVE

MAX_CONNECTIONS = 128


class ApplicationProtocol:
    def __init__(self, applications, api_key):
        self.apps = applications
        self.api_key = api_key
        self.connections = 0
        self.operations = asyncio.Semaphore(16)

    async def call(self, function, *args, **kwargs):
        async with self.operations:
            return await asyncio.to_thread(function, *args, **kwargs)

    def web_app(self):
        @web.middleware
        async def boundary(request, handler):
            if request.path == "/health" and request.method == "GET":
                return web.json_response({"service": "corki-applications", "version": 1})
            authorization = request.headers.get("Authorization", "")
            if not hmac.compare_digest(authorization.encode(), ("Bearer " + self.api_key).encode()):
                return web.json_response(
                    {"error": {"code": "unauthorized", "message": "需要有效的 Bearer API Key"}},
                    status=401,
                    headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
                )
            if self.connections >= MAX_CONNECTIONS:
                return web.json_response(
                    {"error": {"code": "busy", "message": "连接较多，请稍后重试"}}, status=429
                )
            self.connections += 1
            try:
                result = await handler(request)
                result.headers["Cache-Control"] = "no-store"
                return result
            except AppError as error:
                return web.json_response(
                    {"error": {"code": "application_error", "message": str(error)}},
                    status=error.status,
                )
            except web.HTTPException as error:
                return web.json_response(
                    {"error": {"code": "http_error", "message": error.reason}}, status=error.status
                )
            except (ValueError, TypeError, KeyError, UnicodeError):
                return web.json_response(
                    {"error": {"code": "invalid_request", "message": "请求参数无效"}}, status=400
                )
            except (OSError, sqlite3.Error):
                return web.json_response(
                    {
                        "error": {
                            "code": "storage_error",
                            "message": "应用数据暂时不可用，请稍后重试",
                        }
                    },
                    status=503,
                )
            except Exception:
                return web.json_response(
                    {"error": {"code": "internal_error", "message": "应用网关处理失败"}}, status=500
                )
            finally:
                self.connections -= 1

        app = web.Application(middlewares=[boundary], client_max_size=2 * 1024 * 1024)
        app.router.add_get("/health", self.health)
        app.router.add_get("/app", self.retrieve)
        app.router.add_post("/app", self.invoke)
        app.router.add_post("/app/cancel", self.cancel)
        app.router.add_post("/app/respond", self.respond)
        return app

    async def health(self, request):  # normally handled before authentication
        return web.json_response({"service": "corki-applications", "version": 1})

    def resolve(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,18}", value):
            raise AppError("appid 必须是应用卡片上的数字 ID")
        with self.apps.store.db() as db:
            row = db.execute(
                "SELECT id,public_id,type FROM apps WHERE public_id=?", (int(value),)
            ).fetchone()
        if row is None:
            raise AppError("应用不存在", 404)
        return dict(row)

    async def application(self, request):
        if len(request.query.getall("appid", [])) != 1:
            raise AppError("请通过 ?appid=应用ID 指定一个应用")
        app = await self.call(self.resolve, request.query["appid"])
        await self.call(self.apps.access.authorize, app, "http")
        return app

    async def body(self, request, allowed):
        if request.content_type != "application/json":
            raise AppError("请使用 application/json 请求体", 415)
        data = await request.json()
        if not isinstance(data, dict) or set(data) - allowed:
            raise AppError("请求包含不支持的字段")
        return data

    def request_key(self, app, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", identity):
            raise AppError("request_id 仅支持 1–64 个字母、数字、横线、下划线、点或冒号")
        return f"gateway:{app['public_id']}:{identity}"

    def view(self, app, run_id):
        return run_view(self.apps, app, run_id)

    async def retrieve(self, request):
        app = await self.application(request)
        run = request.query.get("run_id")
        if not run and request.query.get("request_id"):
            receipt = await self.call(
                self.apps.get,
                dict(
                    action="submission",
                    app=app["id"],
                    request=self.request_key(app, request.query["request_id"]),
                ),
            )
            run = receipt["run"]
        return web.json_response(await self.call(self.view, app, run))

    async def invoke(self, request):
        app = await self.application(request)
        data = await self.body(request, {"input", "session_id", "request_id", "stream", "wait"})
        for flag in ("stream", "wait"):
            if flag in data and type(data[flag]) is not bool:
                raise AppError(f"{flag} 必须是布尔值")
        session = data.get("session_id")
        if session is not None and (
            not isinstance(session, str) or not re.fullmatch(r"[a-f0-9]{32}", session)
        ):
            raise AppError("session_id 无效")
        supplied = data.get("request_id")
        header = request.headers.get("Idempotency-Key")
        if supplied is not None and header is not None and supplied != header:
            raise AppError("request_id 与 Idempotency-Key 不一致")
        identity = (
            supplied if supplied is not None else header if header is not None else uuid.uuid4().hex
        )
        receipt = await self.call(
            self.apps.submit,
            dict(
                app=app["id"],
                text=data.get("input"),
                session=session,
                request=self.request_key(app, identity),
            ),
            transport="http",
        )
        if data.get("stream", False):
            return await self.stream(request, app, receipt, identity)
        deadline = time.monotonic() + (30 if data.get("wait", True) else 0)
        while True:
            view = await self.call(self.view, app, receipt["run"])
            view["request_id"] = identity
            if (
                view["status"] not in ACTIVE
                or view["status"] == "waiting"
                or time.monotonic() >= deadline
            ):
                code = (
                    202 if view["status"] in ACTIVE else 502 if view["status"] == "failed" else 200
                )
                return web.json_response(view, status=code)
            await asyncio.sleep(0.2)

    async def stream(self, request, app, receipt, request_id):
        run = receipt["run"]
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            }
        )
        response.enable_chunked_encoding()
        await response.prepare(request)
        sequence = 0

        async def event(kind, data):
            nonlocal sequence
            sequence += 1
            packet = (
                f"id: {sequence}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            )
            await asyncio.wait_for(response.write(packet.encode()), timeout=10)

        texts, last_status, last_interaction = {}, None, None
        heartbeat = time.monotonic()
        try:
            await event(
                "run.created",
                dict(
                    appid=app["public_id"],
                    run_id=run,
                    session_id=receipt["session"],
                    request_id=request_id,
                ),
            )
            while True:
                view = await self.call(self.view, app, run)
                if last_status != view["status"]:
                    await event(
                        "run.status",
                        {k: view[k] for k in ("appid", "run_id", "session_id", "status")},
                    )
                    last_status = view["status"]
                for message in view["messages"]:
                    old = texts.get(message["id"], "")
                    text = message["content"]
                    if old != text:
                        await event(
                            "message.delta" if text.startswith(old) else "message.replace",
                            dict(
                                id=message["id"],
                                content=text[len(old) :] if text.startswith(old) else text,
                            ),
                        )
                        texts[message["id"]] = text
                interaction = view["interaction"]
                if interaction and interaction != last_interaction:
                    await event("interaction.required", interaction)
                    last_interaction = interaction
                if view["status"] not in ACTIVE:
                    await event("run." + view["status"], view)
                    break
                if time.monotonic() - heartbeat >= 10:
                    await asyncio.wait_for(response.write(b": ping\n\n"), timeout=10)
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.2)
        except (ConnectionError, TimeoutError, RuntimeError):
            pass  # Disconnection never cancels or replays the accepted task.
        except AppError as error:
            with contextlib.suppress(ConnectionError, TimeoutError, RuntimeError):
                await event("error", {"message": str(error)})
        return response

    async def cancel(self, request):
        app = await self.application(request)
        data = await self.body(request, {"run_id"})
        return web.json_response(await self.call(cancel_run, self.apps, app, data.get("run_id")))

    async def respond(self, request):
        app = await self.application(request)
        data = await self.body(request, {"run_id", "interaction_id", "decision", "response"})
        return web.json_response(await self.call(respond_run, self.apps, app, data))
