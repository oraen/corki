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


@pytest.mark.parametrize("explicit_queue", [False, True])
def test_final_answer_or_queue_mode_waits_for_completion(tmp_path, explicit_queue):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            if not explicit_queue:
                s.live[p["threadId"]].append(
                    {
                        "type": "AssistantTextDelta",
                        "data": {
                            "phase": "final_answer",
                            "delta": "answer",
                            "turn_id": str(r.turn),
                        },
                    }
                )
            q = follow(p, followUpMode="queue" if explicit_queue else "steer")
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
            q = follow(p, followUpMode="queue")
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
            a, b = follow(p, "a", followUpMode="queue"), follow(p, "b", followUpMode="queue")
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
        q = follow(p, followUpMode="queue")
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


def test_invalid_configuration_pauses_without_discarding(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            q = follow(p, followUpMode="queue")
            await s.submit(q)
            (tmp_path / "settings.json").write_text("{}")
            r.release.set()
            await wait_until(lambda: s.followups.rows(p["threadId"])[0]["state"] == "failed")
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


def test_different_model_waits_and_invalid_attachment_is_not_queued(tmp_path):
    async def check():
        s, r, p, _ = await host(tmp_path)
        try:
            config = json.loads((tmp_path / "settings.json").read_text())
            config["providers"]["test"]["models"]["second"] = {"context_window": 262144}
            (tmp_path / "settings.json").write_text(json.dumps(config))
            q = follow(p, model="second")
            assert (await s.submit(q))["state"] == "queued"
            assert not r.steers
            with pytest.raises(ValueError):
                await s.submit(
                    follow(p, attachments=[{"type": "image", "name": "bad", "data": "invalid"}])
                )
            assert len(s.followups.snapshot(p["threadId"])["messages"]) == 1
        finally:
            await s.close()

    asyncio.run(check())
