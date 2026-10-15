import pytest
from rich.console import Console

from corki.cli import syntax
from corki.cli.markdown import AssistantMarkdown
from corki.cli.patch_preview import render_changes, wrap_preview


@pytest.mark.parametrize("system", [None, "standard", "256", "truecolor"])
@pytest.mark.parametrize("kind", ["add", "delete"])
@pytest.mark.parametrize("width", [40, 100])
def test_light_diff_body_and_gutter_have_separate_palettes(system, kind, width):
    console = Console(width=width, color_system=system)
    preview = render_changes(
        [{"path": "a.txt", "change": {"kind": kind, "content": "plain " * 60}}], light=True
    )
    rows = wrap_preview(preview, console, width)
    start = next(i for i, row in enumerate(rows) if row.plain.startswith("  1 "))
    for row in rows[start:]:
        if not row.plain:
            break
        gutter = row.get_style_at_offset(console, 0)
        body = row.get_style_at_offset(console, 5)
        assert gutter.dim is False
        if system == "truecolor":
            assert gutter.color.get_truecolor().hex == "#1f2328"
            assert gutter.bgcolor.get_truecolor().hex == ("#aceebb" if kind == "add" else "#ffcecb")
            assert body.bgcolor.get_truecolor().hex == ("#dafbe1" if kind == "add" else "#ffebe9")
        elif system == "256":
            assert gutter.color.number == 236
            assert gutter.bgcolor.number == (157 if kind == "add" else 217)
            assert body.bgcolor.number == (194 if kind == "add" else 224)
        else:
            assert gutter.bgcolor is body.bgcolor is None
        if system in {"truecolor", "256"}:
            assert row.cell_len == width
            assert body.color.is_default
            assert row.get_style_at_offset(console, width - 1).bgcolor == body.bgcolor


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("system", [None, "standard", "256", "truecolor"])
@pytest.mark.parametrize("kind", ["add", "delete"])
def test_diff_background_fills_wrapped_rows_at_supported_depth(width, system, kind):
    code = "keep source " * 20
    console = Console(width=width, color_system=system)
    preview = render_changes([{"path": "a.txt", "change": {"kind": kind, "content": code}}])
    rows = wrap_preview(preview, console, width)
    start = next(i for i, row in enumerate(rows) if row.plain.startswith("  1 "))
    body = []
    for row in rows[start:]:
        if not row.plain:
            break
        body.append(row.plain[5:].rstrip())
        background = row.get_style_at_offset(console, 0).bgcolor
        if system in {"256", "truecolor"}:
            assert row.cell_len == width
            assert background is not None
            if system == "truecolor":
                assert background.get_truecolor().hex == ("#213a2b" if kind == "add" else "#4a221d")
            else:
                assert background.number == (22 if kind == "add" else 52)
            assert row.get_style_at_offset(console, width - 1).bgcolor == background
        else:
            assert background is None
    assert len(body) > 1
    assert preview.plain.endswith(code + "\n\n")


@pytest.mark.parametrize(
    "code",
    [("x" * 4095 + "\n") * 129, "x\n" * 10001, "x" * 4097],
    ids=["large_block", "many_lines", "long_line"],
)
def test_highlight_limits_skip_lexer_without_removing_text(monkeypatch, code):
    monkeypatch.setattr(
        syntax, "get_lexer_for_filename", lambda *a, **kw: pytest.fail("lexer must not run")
    )
    assert syntax.highlight_code(code, filename="large.py") is None
    preview = render_changes([{"path": "large.py", "change": {"kind": "add", "content": code}}])
    assert len([line for line in preview.plain.split("\n") if " +" in line]) == len(
        code.splitlines()
    )


@pytest.mark.parametrize("failure_site", ["Syntax", "get_lexer_for_filename", "get_lexer_by_name"])
def test_highlight_failure_falls_back_without_text_loss(monkeypatch, failure_site):
    def fail(*args, **kwargs):
        raise ValueError("injected lexer failure")

    monkeypatch.setattr(syntax, failure_site, fail)
    code = 'print("keep this")'
    if failure_site == "get_lexer_by_name":
        assert syntax.highlight_code(code, "python") is None
    else:
        assert syntax.highlight_code(code, filename="a.py") is None
    preview = render_changes([{"path": "a.py", "change": {"kind": "add", "content": code}}])
    assert f"  1 +{code}\n" in preview.plain
    console = Console(width=40, color_system=None)
    with console.capture() as output:
        console.print(AssistantMarkdown(f"```python\n{code}\n```"))
    assert code in output.get()


@pytest.mark.parametrize("width", [40, 100])
def test_multiline_hunk_highlight_survives_hard_wrap(width):
    body = "long string " * 12
    preview = render_changes(
        [
            {
                "path": "a.py",
                "change": {
                    "kind": "update",
                    "diff": (
                        '@@ -1,3 +1,3 @@\n value = """start\n-'
                        + body
                        + "\n+"
                        + body
                        + '\n end"""\n'
                    ),
                },
            }
        ]
    )
    console = Console(width=width)
    color = preview.get_style_at_offset(console, preview.plain.index('"""start')).color
    assert color is not None and color.triplet is not None
    assert preview.get_style_at_offset(console, preview.plain.index(body)).color == color
    rows = wrap_preview(preview, console, width)
    assert all(row.cell_len <= width for row in rows)
    parts = [row for row in rows if "long" in row.plain or "string" in row.plain]
    assert len(parts) >= 4
    for row in parts:
        offset = next(index for index, char in enumerate(row.plain) if char.isalpha())
        assert row.get_style_at_offset(console, offset).color == color
