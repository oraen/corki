import asyncio
import json

from corki.core.turn_diff import TurnDiffTracker
from corki.protocol.tools import ToolResult


class Sink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def result(identity, *changes, exact=True):
    return ToolResult(
        identity,
        "apply_patch",
        "ok",
        patch_delta_json=json.dumps(
            {
                "version": 1,
                "exact": exact,
                "changes": list(changes),
            }
        ),
    )


def change(path, kind, **fields):
    return {"path": path, "change": {"kind": kind, **fields}}


def test_revision_cache_dedup_and_permanent_invalidation(tmp_path):
    async def scenario():
        rendered = []

        async def render(compiler, cwd, path, left, dest, right):
            rendered.append((path, dest))
            return None if left == right else f"{path} {left!r}->{right!r}\n"

        tracker, sink = TurnDiffTracker(render), Sink()

        async def apply(value):
            await tracker.observe(
                value,
                is_patch=True,
                compiler=None,
                cwd=tmp_path,
                thread="thread",
                turn="turn",
                events=sink,
            )

        a, b = str(tmp_path / "a"), str(tmp_path / "b")
        first = result("1", change(a, "add", content="one", overwritten_content=None))
        await apply(first)
        await apply(result("2", change(b, "add", content="stable", overwritten_content=None)))
        await apply(
            result(
                "3",
                change(
                    a,
                    "update",
                    old_content="one",
                    new_content="two",
                    move_path=None,
                    overwritten_move_content=None,
                ),
            )
        )
        assert len(rendered) == 3
        assert tracker.baseline == {}
        assert tracker.current[a][1] == "two" and tracker.current[b][1] == "stable"
        before = list(sink.events)
        await apply(first)
        assert sink.events == before and len(rendered) == 3
        await apply(result("empty"))
        assert sink.events == before
        await apply(result("4", exact=False))
        assert sink.events[-1].unified_diff == "" and not tracker.valid
        await apply(result("5", change(a, "add", content="later", overwritten_content=None)))
        assert len(rendered) == 3 and tracker.diff is None
        assert len(sink.events) == len(before) + 1

    asyncio.run(scenario())


def test_move_overwrite_order_and_original_baselines(tmp_path):
    async def scenario():
        views = []

        async def render(compiler, cwd, path, left, dest, right):
            views.append((path, left, dest, right))
            return "changed\n" if left != right else None

        tracker, sink = TurnDiffTracker(render), Sink()
        a, b = str(tmp_path / "a"), str(tmp_path / "b")
        delta = result(
            "1",
            change(b, "delete", content="previous"),
            change(
                a,
                "update",
                old_content="source",
                new_content="new",
                move_path=b,
                overwritten_move_content=None,
            ),
        )
        await tracker.observe(
            delta,
            is_patch=True,
            compiler=None,
            cwd=tmp_path,
            thread="thread",
            turn="turn",
            events=sink,
        )
        assert views == [(a, "source", a, None), (b, "previous", b, "new")]

    asyncio.run(scenario())


def test_pure_move_uses_origin_and_emits_no_content_diff(tmp_path):
    async def scenario():
        views = []

        async def render(compiler, cwd, path, left, dest, right):
            views.append((path, left, dest, right))
            return None

        tracker, sink = TurnDiffTracker(render), Sink()
        a, b = str(tmp_path / "a"), str(tmp_path / "b")
        await tracker.observe(
            result(
                "1",
                change(
                    a,
                    "update",
                    old_content="same",
                    new_content="same",
                    move_path=b,
                    overwritten_move_content=None,
                ),
            ),
            is_patch=True,
            compiler=None,
            cwd=tmp_path,
            thread="t",
            turn="u",
            events=sink,
        )
        assert views == [(a, "same", b, "same")] and sink.events == []

    asyncio.run(scenario())
