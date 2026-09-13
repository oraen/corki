import asyncio
import json

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import ColorDepth, DummyOutput
from rich.console import Console
from rich.text import Text

from corki.cli.approval import choose_approval
from corki.cli.approval_details import ApprovalDetails, request_details
from corki.cli.patch_preview import render_changes, wrap_preview
from corki.cli.terminal_palette import TerminalPalette
from corki.mcp.elicitation import ElicitationRequest


def test_open_pager_refreshes_when_background_sample_arrives():
    async def scenario(pipe):
        palette = TerminalPalette()
        session = PromptSession(
            input=pipe, output=DummyOutput(), color_depth=ColorDepth.DEPTH_24_BIT
        )
        calls = []

        def source():
            calls.append(palette.light)
            return render_changes(
                [{"path": "a.py", "change": {"kind": "add", "content": 'print("visible")'}}],
                light=palette.light,
            )

        pager = ApprovalDetails(session, source, palette=palette)
        try:
            pager._load_content()
            original = pager.content
            pager.create_content(40, 20)
            dark_lines = pager.lines
            palette.foreground, palette.background = (0, 0, 0), (255, 255, 255)
            pager.create_content(40, 20)
            assert pager.lines != dark_lines
            assert any("bg:#dafbe1" in style for line in pager.lines for style, _ in line)
            assert any("bg:#aceebb" in style for line in pager.lines for style, _ in line)
            assert pager.content == original
            pager.create_content(40, 20)
            assert calls == [False, True]
        finally:
            pager.restore()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_pager_uses_effective_color_depth_and_invalidates_styles():
    async def scenario(pipe):
        session = PromptSession(
            input=pipe, output=DummyOutput(), color_depth=ColorDepth.DEPTH_24_BIT
        )
        pager = ApprovalDetails(session, lambda: "")
        preview = render_changes(
            [{"path": "a.txt", "change": {"kind": "add", "content": "visible"}}]
        )
        pager.styled_content = preview
        pager.content = preview.plain
        try:
            for selected, background in (
                (ColorDepth.DEPTH_24_BIT, "#213a2b"),
                (ColorDepth.DEPTH_8_BIT, "#005f00"),
                (ColorDepth.DEPTH_4_BIT, None),
                (ColorDepth.DEPTH_1_BIT, None),
            ):
                session.color_depth = selected
                frame = pager.create_content(40, 20)
                lines = [frame.get_line(i) for i in range(frame.line_count)]
                line = next(parts for parts in lines if "visible" in "".join(t for _, t in parts))
                if background:
                    assert all("bg:" + background in style for style, text in line if text), line
                    assert len("".join(text for _, text in line)) == 40
                else:
                    assert not any("bg:" in style for style, _ in line)
                assert pager.content == preview.plain
        finally:
            pager.restore()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize("kind", ["add", "delete", "update"])
def test_patch_preview_only_counts_source_newlines(kind):
    content = "a\u2028b\vc\rd\r\n\nlast\r"
    change = {"kind": kind, "content": content}
    if kind == "update":
        change = {"kind": kind, "diff": "@@ -0,0 +1,3 @@\n+a\u2028b\vc\rd\r\n+\n+last\r"}
    preview = render_changes([{"path": "a", "change": change}]).plain
    sign = "-" if kind == "delete" else "+"
    assert f"a (+{0 if kind == 'delete' else 3} -{3 if kind == 'delete' else 0})" in preview
    assert f"  1 {sign}a b c d\n  2 {sign}\n  3 {sign}last \n" in preview


@pytest.mark.parametrize("width", [40, 100])
def test_patch_preview_wraps_at_body_column_without_losing_text_or_style(width):
    content = "one two " + "界e\u0301 " * 60 + "TAIL  "
    preview = render_changes([{"path": "a", "change": {"kind": "add", "content": content}}])
    rows = wrap_preview(preview, Console(width=width), width)
    start = next(index for index, row in enumerate(rows) if row.plain.startswith("  1 +"))
    body = []
    for row in rows[start:]:
        if not row.plain:
            break
        assert row.cell_len <= width
        body.append(row.plain[5:])
        if len(body) > 1:
            assert row.plain.startswith("     ")
        assert row.get_style_at_offset(Console(), 5).color.name == "green"
    assert len(body) > 1
    assert "".join(body) == content


def test_patch_preview_hard_wrap_does_not_reinterpret_plain_text():
    console = Console(width=12)
    preview = render_changes(
        [{"path": "a", "change": {"kind": "add", "content": "ab cd ef gh\n\n"}}]
    )
    rows = [row.plain for row in wrap_preview(preview, console, 12)]
    start = rows.index("  1 +ab cd e")
    assert rows[start:] == ["  1 +ab cd e", "     f gh", "  2 +", ""]
    plain = Text("  1 +ab cd ef gh")
    assert wrap_preview(plain, console, 12) == list(plain.wrap(console, 12))


def test_patch_preview_orders_files_and_numbers_add_delete_and_update():
    preview = render_changes(
        [
            {
                "path": "/work/c.txt",
                "change": {
                    "kind": "update",
                    "move_path": "/work/d.txt",
                    "diff": "--- c.txt\n+++ d.txt\n@@ -3,2 +3,2 @@\n-old\n+new\n same\n",
                },
            },
            {"path": "/work/b.txt", "change": {"kind": "delete", "content": "gone\n"}},
            {"path": "/work/a.txt", "change": {"kind": "add", "content": "alpha\nbeta\n"}},
        ],
        "/work",
    )
    assert preview.plain == (
        "Changes:\na.txt (+2 -0)\n\n  1 +alpha\n  2 +beta\n\n"
        "b.txt (+0 -1)\n\n  1 -gone\n\n"
        "c.txt → /work/d.txt (+1 -1)\n\n  3 -old\n  3 +new\n  4  same\n\n"
    )
    colored = [(str(span.style), preview.plain[span.start : span.end]) for span in preview.spans]
    assert ("green", "+alpha\n") in colored
    assert ("red", "-gone\n") in colored
    assert ("green", "+new\n") in colored
    assert ("red", "-old\n") in colored


def test_patch_preview_separates_hunks_without_raw_diff_headers():
    preview = render_changes(
        [
            {
                "path": "a.py",
                "change": {
                    "kind": "update",
                    "diff": (
                        "--- a.py\n+++ a.py\n@@ -1 +1 @@\n-old\n+new\n"
                        "@@ -120 +121 @@\n-before\n+after\n"
                    ),
                },
            }
        ]
    )
    assert preview.plain == (
        "Changes:\na.py (+2 -2)\n\n    1 -old\n    1 +new\n      ⋮\n  120 -before\n  121 +after\n\n"
    )


@pytest.mark.parametrize("kind", ["add", "delete", "update"])
def test_patch_preview_highlights_destination_language_and_preserves_text(kind):
    content = 'def example():\n    return "value"\n'
    change = {"kind": kind, "content": content}
    path = "example.py"
    if kind == "update":
        path = "example.unknown_extension"
        change = {
            "kind": "update",
            "move_path": "example.py",
            "diff": '@@ -1,2 +1,2 @@\n def example():\n-    return "old"\n+    return "value"\n',
        }
    preview = render_changes([{"path": path, "change": change}])
    style = preview.get_style_at_offset(Console(), preview.plain.index("def example"))
    assert style.color is not None and style.color.triplet is not None
    assert bool(style.dim) is (kind == "delete")
    assert 'return "value"' in preview.plain


def test_patch_details_preserve_full_retry_evidence_and_original_patch():
    evidence = {
        "sandbox": "seatbelt",
        "output": "original failed; no rollback",
        "execution": {"stdout": "", "stderr": "permission denied"},
        "committed_delta": {
            "exact": False,
            "changes": [{"path": "/first", "change": {"content": "界" * 14000 + "RISKTAIL"}}],
        },
    }
    arguments = {
        "patch": "*** Begin Patch\n*** Add File: review.txt\n+" + "p" * 26000 + "PATCHTAIL"
    }
    request = ElicitationRequest(
        "local-shell",
        "request",
        {
            "message": "Retry?",
            "_meta": {"tool_params": arguments, "patch_retry": evidence},
            "requestedSchema": {"type": "object", "properties": {}},
        },
        "patch_approval",
    )
    details = request_details(request).plain
    assert details == (
        "Retry?\n\nPatch retry evidence:\n"
        + json.dumps(evidence, indent=2, ensure_ascii=False)
        + "\n\nTool arguments:\n"
        + "{}\n\nPatch:\n"
        + arguments["patch"]
    )
    assert request.params["_meta"]["tool_params"] == arguments


@pytest.mark.parametrize("cancel", [False, True])
def test_full_details_are_read_only_and_restore_pending_approval(cancel):
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        session.app.ttimeoutlen = 0.01
        original_layout = session.layout.container
        ready = asyncio.Event()
        prompt = session.prompt_async

        async def started(*args, **kwargs):
            return await prompt(*args, **kwargs, pre_run=ready.set)

        session.prompt_async = started
        text = "line\n" * 3000 + "TAIL_BEYOND_PREVIEW\x1b[31m"
        task = asyncio.create_task(choose_approval(session, details=lambda: text))
        try:
            await asyncio.wait_for(ready.wait(), 2)
            pipe.send_text("\x1b[B\x01")
            async with asyncio.timeout(2):
                while not isinstance(session.layout.current_control, ApprovalDetails):
                    await asyncio.sleep(0.01)
            pager = session.layout.current_control
            for width in (40, 100):
                frame = pager.create_content(width, 20)
                assert frame.line_count > 3000
                assert "TAIL_BEYOND_PREVIEW" in "".join(
                    fragment[1] for fragment in frame.get_line(frame.line_count - 1)
                )
                assert "\x1b" not in pager.content
            pipe.send_text("y1\r\x1b[200~q\x03\x1b[201~\x1b[F")
            async with asyncio.timeout(2):
                while pager.row != len(pager.lines) - 1:
                    await asyncio.sleep(0.01)
            assert not task.done()
            assert pager.active
            assert session.default_buffer.text == ""
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                pipe.send_text("q")
                async with asyncio.timeout(2):
                    while pager.active:
                        await asyncio.sleep(0.01)
                assert not task.done(), "closing details must not decide approval"
                pipe.send_text("\r")
                assert await asyncio.wait_for(task, 2) == "decline"
            assert session.layout.container is original_layout
            assert not session.app.full_screen
            assert not session.app.renderer.full_screen
            assert not session.app.renderer._in_alternate_screen
            assert list(session.history.get_strings()) == []
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
