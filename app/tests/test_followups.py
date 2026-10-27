"""Queue/steering lifecycle tests with an isolated home and controllable runtime."""

import asyncio
import json
import uuid

import pytest
from test_bridge import FakeRuntime, Service, setup

from corki.protocol.events import TurnCancelled, TurnCompleted, TurnStarted
from corki.protocol.ids import TurnId
from corki.protocol.items import UserMessageItem
from corki.realtime import RealtimeController
from corki.sessions.models import TurnRecord, TurnStatus


class ControlledRuntime(FakeRuntime):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.inputs = []
        self.realtime = RealtimeController(20)
        self.steers = 0

    async def stream(self, message, **kw):
        assert kw["realtime"] is True
        self.inputs.append(message)
        self.cancelled = False
        self.release.clear()
        turn = TurnId(str(uuid.uuid4()))
        self.turn = turn
        self.realtime.activate(turn)
        await self.repository.create_thread(self.thread, self.settings.working_directory)
        await self.repository.save_turn(TurnRecord(turn, self.thread, TurnStatus.RUNNING, message))
        await self.repository.append_items(self.thread, (UserMessageItem(message, turn),))
        yield TurnStarted(self.thread, turn)
        self.started.set()
        try:
            await self.release.wait()
            returned = self.realtime.unrecorded_items if self.cancelled else ()
            for item in self.realtime.unrecorded_items:
                if not self.cancelled:
                    await self.repository.append_items(self.thread, (item,))
                self.realtime.acknowledge((item,))
            status = TurnStatus.CANCELLED if self.cancelled else TurnStatus.COMPLETED
            await self.repository.save_turn(TurnRecord(turn, self.thread, status, message))
            yield (
                TurnCancelled(self.thread, turn, returned)
                if self.cancelled
                else TurnCompleted(self.thread, turn, "done")
            )
        finally:
            self.realtime.deactivate()

    async def steer(self, message, **kw):
        self.steers += 1
        return await self.realtime.steer(message, **kw)

    async def cancel_active(self):
        self.cancelled = True
        self.release.set()


async def host(tmp_path):
    setup(tmp_path)
    events = []

    async def emit(event):
        events.append(event)

    async def factory(**kw):
        return ControlledRuntime(**kw)

    s = Service(tmp_path, emit, factory)
    request = {
        "requestId": str(uuid.uuid4()),
        "threadId": str(uuid.uuid4()),
        "content": "first",
        "provider": "test",
        "model": "test-model",
        "reasoning": None,
    }
    await s.submit(request)
    runtime = s.runtimes[request["threadId"]]
    await runtime.started.wait()
    return s, runtime, request, events


def follow(request, content="next", **kw):
    return {**request, "requestId": str(uuid.uuid4()), "content": content, **kw}


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_steering_dedup_and_canonical_identity(tmp_path):
    async def check():
        s, r, p, events = await host(tmp_path)
        try:
            q = follow(p)
            a, b = await asyncio.gather(s.submit(q), s.submit(q))
            assert a["id"] == b["id"] and a["state"] == "accepted"
            assert r.steers == 1 and len(r.inputs) == 1
            live = next(
                e["params"]["data"] for e in events if e["params"]["type"] == "UserMessageSubmitted"
            )
            assert live["item_id"] == str(r.realtime.unrecorded_items[0].id)
            r.release.set()
            await wait_until(lambda: not s.tasks)
            assert s.state.request(q["requestId"])["state"] == "completed"
            items = await s.repository.load_items(r.thread)
            assert sum(isinstance(i, UserMessageItem) and i.content == "next" for i in items) == 1
        finally:
            await s.close()

    asyncio.run(check())


def final_output(s, p):
    s.live[p["threadId"]].append({
        "type": "AssistantTextDelta",
        "data": {"phase": "final_answer", "delta": "answer"},
    })


def test_final_answer_waits_for_completion(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            final_output(s, p)
            q = follow(p)
            assert (await s.submit(q))["state"] == "queued"
            assert r.steers == 0
            r.release.set()
            await wait_until(lambda: len(r.inputs) == 2)
            assert r.inputs == ["first", "next"]
            assert not s.followups.rows(p["threadId"])
        finally:
            await s.close()

    asyncio.run(check())


def test_stop_pauses_queue_and_resume_starts_next(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            final_output(s, p)
            q = follow(p)
            await s.submit(q)
            await s.stop({"threadId": p["threadId"]})
            await wait_until(lambda: not s.tasks)
            assert len(r.inputs) == 1 and s.followups.snapshot(p["threadId"])["paused"]
            await s.followups.action({"threadId": p["threadId"], "action": "resume"})
            await wait_until(lambda: len(r.inputs) == 2)
        finally:
            await s.close()

    asyncio.run(check())


def test_queue_edit_reorder_delete_and_force_steer(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            final_output(s, p)
            a, b = follow(p, "a"), follow(p, "b")
            await s.submit(a)
            await s.submit(b)

            async def action(name, **kw):
                return await s.followups.action({"threadId": p["threadId"], "action": name, **kw})

            await action("move", id=b["requestId"], direction=-1)
            assert s.followups.rows(p["threadId"])[0]["id"] == b["requestId"]
            await action("edit", id=b["requestId"], content="edited")
            await action("send", id=b["requestId"])
            assert r.realtime.unrecorded_items[0].content == "edited"
            await action("delete", id=a["requestId"])
            assert not s.followups.snapshot(p["threadId"])["messages"]
            with pytest.raises(ValueError):
                await s.followups.action(
                    {"threadId": "other", "action": "delete", "id": b["requestId"]}
                )
        finally:
            await s.close()

    asyncio.run(check())


def test_restart_preserves_queue_but_never_replays_unknown(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        final_output(s, p)
        q = follow(p)
        await s.submit(q)
        s.followups.mark(q["requestId"], "dispatching")
        await s.close()

        async def emit(e):
            pass

        s = Service(tmp_path, emit)
        try:
            snap = s.followups.snapshot(p["threadId"])
            assert snap["paused"] and snap["messages"][0]["state"] == "unknown"
            assert not s.tasks
            with pytest.raises(ValueError, match="核对"):
                await s.followups.action({"threadId": p["threadId"], "action": "resume"})
            with pytest.raises(ValueError, match="待确认"):
                await s.followups.action(
                    {"threadId": p["threadId"], "action": "send", "id": q["requestId"]}
                )
        finally:
            await s.close()

    asyncio.run(check())


def test_closed_boundary_queues_instead_of_losing_input(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            r.realtime.close_input()
            q = follow(p)
            assert (await s.submit(q))["state"] == "queued"
            r.release.set()
            await wait_until(lambda: len(r.inputs) == 2)
        finally:
            await s.close()

    asyncio.run(check())


def test_invalid_configuration_pauses_without_discarding(tmp_path, monkeypatch):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            final_output(s, p)
            q = follow(p)
            await s.submit(q)
            # Isolate admission failure from the deliberately cached config loader.
            def invalid_settings(*args, **kwargs):
                raise ValueError("fixture invalid configuration")
            monkeypatch.setattr(s, "settings", invalid_settings)
            r.release.set()
            await wait_until(lambda: any(row["state"] == "failed" for row in s.followups.rows(p["threadId"])))
            assert len(r.inputs) == 1 and s.followups.snapshot(p["threadId"])["paused"]
        finally:
            await s.close()

    asyncio.run(check())


def test_cancel_returns_uncommitted_steer_to_paused_queue(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            q = follow(p, "preserve this input")
            await s.submit(q)
            assert not s.followups.snapshot(p["threadId"])["messages"]
            await s.stop({"threadId": p["threadId"]})
            await wait_until(lambda: not s.tasks)
            state = s.followups.snapshot(p["threadId"])
            assert state["paused"] and state["messages"][0]["content"] == "preserve this input"
            assert (await s.request_read({"requestId": q["requestId"]}))["state"] == "queued"
            await s.followups.action({"threadId": p["threadId"], "action": "resume"})
            await wait_until(lambda: len(r.inputs) == 2)
            assert r.inputs[-1] == "preserve this input"
        finally:
            await s.close()

    asyncio.run(check())


def test_different_model_steers_active_task_and_invalid_attachment_is_not_queued(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            config = json.loads((tmp_path / "settings.json").read_text())
            config["providers"]["test"]["models"]["second"] = {"context_window": 262144}
            (tmp_path / "settings.json").write_text(json.dumps(config))
            q = follow(p, model="second")
            assert (await s.submit(q))["state"] == "accepted"
            assert r.steers == 1
            assert s.active_models[p["threadId"]][1] == "test-model"
            with pytest.raises(ValueError):
                await s.submit(
                    follow(p, attachments=[{"type": "image", "name": "bad", "data": "invalid"}])
                )
            assert not s.followups.snapshot(p["threadId"])["messages"]
            r.release.set()
            await wait_until(lambda: not s.tasks)
            assert r.inputs == ["first"]
            assert s.state.request(q["requestId"])["state"] == "completed"
            assert not s.followups.snapshot(p["threadId"])["messages"]
        finally:
            await s.close()

    asyncio.run(check())


def test_new_send_after_stop_is_not_blocked_or_replays_old_pending_input(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            final_output(s, p)
            old = follow(p, "old unsent input")
            await s.submit(old)
            await s.stop({"threadId": p["threadId"]})
            await wait_until(lambda: not s.tasks)
            new = follow(p, "new explicit input")
            assert (await s.submit(new))["state"] == "accepted"
            await wait_until(lambda: len(r.inputs) == 2)
            assert r.inputs == ["first", "new explicit input"]
            snapshot = s.followups.snapshot(p["threadId"])
            assert snapshot["messages"][0]["state"] == "failed"
            assert snapshot["messages"][0]["content"] == "old unsent input"
            assert snapshot["messages"][0]["createdAt"]
            # An old failed input cannot prevent a later deferred input draining.
            final_output(s, p)
            next_input = follow(p, "next deferred input")
            await s.submit(next_input)
            r.release.set()
            await wait_until(lambda: len(r.inputs) == 3)
            assert r.inputs[-1] == "next deferred input"
        finally:
            await s.close()
    asyncio.run(check())


def test_pending_text_is_not_truncated_for_chat_display(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            content = "fixture input " * 1000
            final_output(s, p)
            await s.submit(follow(p, content))
            assert s.followups.snapshot(p["threadId"])["messages"][0]["content"] == content
        finally:
            await s.close()
    asyncio.run(check())


def test_legacy_queue_preference_does_not_defer_followup(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            s.followups.setting(p["threadId"], mode="queue")
            q = follow(p, followUpMode="queue")
            assert (await s.submit(q))["state"] == "accepted"
            assert r.steers == 1 and r.inputs == ["first"]
        finally:
            await s.close()
    asyncio.run(check())
