"""Read-only approval pager within the existing form application's input owner."""

import json

from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl
from prompt_toolkit.output import ColorDepth
from rich.console import Console
from rich.text import Text

from corki.cli.patch_preview import render_changes, wrap_preview
from corki.protocol.wire_numbers import dumps_wire


def request_details(request, *, light=False):
    parts = [request.params["message"]]
    metadata = request.params.get("_meta", {})
    if request.kind in {"tool_approval", "shell_approval", "patch_approval"}:
        if "patch_retry" in metadata:
            parts.append(
                "Patch retry evidence:\n"
                + json.dumps(metadata["patch_retry"], indent=2, ensure_ascii=False)
            )
        arguments = metadata["tool_params"]
        patch = None
        if request.kind == "patch_approval" and isinstance(arguments.get("patch"), str):
            arguments = dict(arguments)
            patch = arguments.pop("patch")
            if arguments.get("changes"):
                parts.append(
                    render_changes(arguments["changes"], arguments.get("cwd"), light=light)
                )
        parts.append(
            "Tool arguments:\n"
            + (
                dumps_wire(arguments)
                if request.kind == "tool_approval"
                else json.dumps(arguments, indent=2, ensure_ascii=False)
            )
        )
        if patch is not None:
            parts.append("Patch:\n" + patch)
    if request.params.get("mode") == "url":
        parts.append("URL: " + request.params["url"])
    return Text("\n\n").join(part if isinstance(part, Text) else Text(part) for part in parts)


class ApprovalDetails(UIControl):
    def __init__(self, session, source, *, palette=None):
        self.session, self.source = session, source
        self.palette = palette
        self.light = palette.light if palette is not None else False
        self.active = False
        self.row = 0
        self.content = None
        self.styled_content = None
        self.width = None
        self.color_depth = None
        self.lines = [[("", "")]]
        self.previous_focus = None
        self.original = session.layout.container
        self.screen_modes = (session.app.full_screen, session.app.renderer.full_screen)
        self.bindings = KeyBindings()
        active = Condition(lambda: self.active)
        self.window = Window(self, wrap_lines=False)
        session.layout.container = HSplit(
            [
                ConditionalContainer(
                    HSplit(
                        [
                            Window(FormattedTextControl("Approval details — read only"), height=1),
                            self.window,
                            Window(
                                FormattedTextControl(
                                    "↑/↓ · PgUp/PgDn · Home/End · q/Ctrl+C return"
                                ),
                                height=1,
                            ),
                        ]
                    ),
                    active,
                ),
                ConditionalContainer(self.original, ~active),
            ]
        )

        @self.bindings.add("c-a", eager=True)
        def open_or_close(event):
            if self.active:
                self.close()
            else:
                if self.content is None:
                    self._load_content()
                self.previous_focus = session.layout.current_control
                session.app.renderer.erase()
                self.active = True
                session.app.full_screen = session.app.renderer.full_screen = True
                session.layout.focus(self.window)

        @self.bindings.add("q", filter=active, eager=True)
        @self.bindings.add("c-c", filter=active, eager=True)
        @self.bindings.add("escape", filter=active, eager=True)
        def back(event):
            self.close()

        for key, direction in (("up", -1), ("down", 1), ("pageup", -1), ("pagedown", 1)):

            def move(event, key=key, direction=direction):
                page = max(1, session.app.output.get_size().rows - 2)
                self.row = max(
                    0,
                    min(len(self.lines) - 1, self.row + direction * (page if "page" in key else 1)),
                )

            self.bindings.add(key, filter=active, eager=True)(move)

        @self.bindings.add("home", filter=active, eager=True)
        def first(event):
            self.row = 0

        @self.bindings.add("end", filter=active, eager=True)
        def last(event):
            self.row = len(self.lines) - 1

        @self.bindings.add(Keys.Any, filter=active)
        @self.bindings.add(Keys.BracketedPaste, filter=active)
        @self.bindings.add("enter", filter=active, eager=True)
        def ignore(event):
            pass

    def is_focusable(self):
        return True

    def _load_content(self):
        value = self.source()
        self.styled_content = value.copy() if isinstance(value, Text) else Text(value)
        self.content = "".join(
            c if c.isprintable() or c == "\n" else " " for c in self.styled_content.plain
        )
        self.styled_content.plain = self.content
        self.width = None

    def create_content(self, width, height):
        light = self.palette.light if self.palette is not None else False
        if self.light != light:
            self.light = light
            self._load_content()
        color_depth = self.session.app.color_depth
        if self.width != width or self.color_depth != color_depth:
            self.width = width
            self.color_depth = color_depth
            console = Console(
                width=max(1, width),
                color_system={
                    ColorDepth.DEPTH_24_BIT: "truecolor",
                    ColorDepth.DEPTH_8_BIT: "256",
                    ColorDepth.DEPTH_4_BIT: "standard",
                }.get(color_depth),
            )
            rows = wrap_preview(self.styled_content or Text(self.content or ""), console, width)
            self.lines = []
            for line in rows:
                fragments = []
                for segment in line.render(console, end=""):
                    style = segment.style
                    attributes = []
                    if style is not None:
                        if style.color is not None and not style.color.is_default:
                            attributes.append("fg:" + style.color.get_truecolor().hex)
                        if style.bgcolor is not None:
                            attributes.append("bg:" + style.bgcolor.get_truecolor().hex)
                        if style.dim:
                            attributes.append("dim")
                        for attribute in ("bold", "italic", "underline"):
                            if getattr(style, attribute):
                                attributes.append(attribute)
                    fragments.append((" ".join(attributes), segment.text))
                self.lines.append(fragments)
            self.lines = self.lines or [[("", "")]]
        self.row = min(self.row, len(self.lines) - 1)
        return UIContent(
            get_line=self.lines.__getitem__,
            line_count=len(self.lines),
            cursor_position=Point(0, self.row),
        )

    def close(self):
        if not self.active:
            return
        self.session.app.renderer.erase()
        self.active = False
        self.session.app.full_screen, self.session.app.renderer.full_screen = self.screen_modes
        if self.previous_focus is not None:
            self.session.layout.focus(self.previous_focus)
        if self.session.app.is_running and not self.session.app.is_done:
            self.session.app._request_absolute_cursor_position()
        self.session.app.invalidate()

    def restore(self):
        self.close()
        self.session.layout.container = self.original
