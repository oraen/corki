"""Frame terminal replies before the existing input parser can turn them into keys.

No reads, queries, timers, or global parser-table changes happen here. The input
object retains its sole reader and owns this framing state across prompt changes.
"""

from prompt_toolkit.input.vt100_parser import Vt100Parser

from corki.cli.keyboard import decode_key


def _color(response):
    slot, separator, payload = response[2:].partition(";")
    if not separator or slot not in {"10", "11"}:
        return None
    kind, separator, values = payload.strip().partition(":")
    parts = values.split("/")
    if not separator or kind.lower() not in {"rgb", "rgba"}:
        return None
    if len(parts) != (4 if kind.lower() == "rgba" else 3):
        return None
    if any(
        not 1 <= len(part) <= 4 or any(c not in "0123456789abcdefABCDEF" for c in part)
        for part in parts
    ):
        return None
    rgb = tuple(int(part, 16) * 255 // (16 ** len(part) - 1) for part in parts[:3])
    return int(slot), rgb


class TerminalResponseParser:
    """Bounded OSC framing, preserving normal keyboard and bracketed-paste parsing."""

    def __init__(self, parser):
        self.parser = parser
        self.pending = ""
        self.osc = False
        self.csi = False
        self.overflow = False
        self.escape = False
        self.colors = {}
        self.color_listeners = set()
        self.focus_listeners = set()

    def _clear(self):
        self.pending = ""
        self.osc = self.csi = self.overflow = self.escape = False

    def feed(self, data):
        cursor = 0
        while cursor < len(data):
            char = data[cursor]
            if not self.osc and not self.pending and char != "\x1b":
                end = data.find("\x1b", cursor)
                end = len(data) if end < 0 else end
                self.parser.feed(data[cursor:end])
                cursor = end
                continue
            cursor += 1
            if self.csi:
                if char == "\x1b" or ord(char) < 32:
                    self._clear()
                    if char == "\x1b":
                        self.pending = char
                    else:
                        self.parser.feed(char)
                    continue
                if len(self.pending) < 256:
                    self.pending += char
                else:
                    self.overflow = True
                if "@" <= char <= "~":
                    if not self.overflow:
                        if self.pending in {"\x1b[I", "\x1b[O"}:
                            for listener in tuple(self.focus_listeners):
                                listener(self.pending == "\x1b[I")
                        else:
                            self.parser.feed(decode_key(self.pending))
                    self._clear()
                continue
            if self.osc:
                if char == "\x07" or (self.escape and char == "\\"):
                    response = self.pending[:-1] if self.escape else self.pending
                    color = None if self.overflow else _color(response)
                    if color is not None:
                        self.colors[color[0]] = color[1]
                        for listener in tuple(self.color_listeners):
                            listener(*color)
                    elif self.parser._in_bracketed_paste:
                        self.parser.feed(self.pending + char)
                    self._clear()
                    continue
                if self.escape:
                    # A new escape sequence (including paste end) resynchronizes
                    # an unfinished OSC. Never consume the paste's closing mark.
                    if self.parser._in_bracketed_paste:
                        self.parser.feed(self.pending[:-1])
                    self._clear()
                    self.pending = "\x1b"
                elif char in {"\x03", "\x04"} and not self.parser._in_bracketed_paste:
                    self._clear()
                    self.parser.feed(char)
                    continue
                else:
                    if len(self.pending) < 1024:
                        self.pending += char
                    elif self.parser._in_bracketed_paste:
                        # Oversized OSC-looking paste is user content, not a
                        # response; preserve it without retaining another copy.
                        self.parser.feed(self.pending + char)
                        self._clear()
                        continue
                    else:
                        self.overflow = True
                    self.escape = char == "\x1b"
                    continue
            if self.pending:
                if char == "[" and not self.parser._in_bracketed_paste:
                    self.pending += char
                    self.csi = True
                    continue
                if char == "]":
                    self.pending += char
                    self.osc = True
                    continue
                self.parser.feed(self.pending)
                self.pending = ""
            if char == "\x1b":
                self.pending = char
            else:
                self.parser.feed(char)

    def flush(self):
        if self.pending and not self.osc and not self.csi:
            self.parser.feed(self.pending)
            self.pending = ""
        # An incomplete reply is not an Escape key. Retain bounded framing for
        # late fragments, but still flush unrelated keys in the underlying parser.
        self.parser.flush()

    def reset(self, request=False):
        self._clear()
        self.parser.reset(request=request)


class NativeConsoleFocus:
    """Observe the existing Windows console reader, without reading any input.

    prompt-toolkit otherwise discards FOCUS_EVENT_RECORD. Keep the original
    key/paste processing and sole reader; listeners are scoped by reporting().
    """

    native_only = True

    def __init__(self, reader):
        self.focus_listeners = set()
        self.color_listeners = set()
        original = reader._get_keys

        def keys(read, records):
            for index in range(read.value):
                record = records[index]
                if record.EventType == 16:  # FOCUS_EVENT, never keyboard/paste bytes
                    for listener in tuple(self.focus_listeners):
                        listener(bool(record.Event.FocusEvent.SetFocus))
            yield from original(read, records)

        reader._get_keys = keys


def frame_terminal_responses(input):
    """Frame VT replies or observe native Windows focus on the existing reader."""
    parser = getattr(input, "vt100_parser", None)
    if isinstance(parser, Vt100Parser):
        parser = input.vt100_parser = TerminalResponseParser(parser)
    if isinstance(parser, TerminalResponseParser):
        return parser
    reader = getattr(input, "console_input_reader", None)
    if reader is None:
        return None
    parser = getattr(reader, "_vt100_parser", None)
    if isinstance(parser, Vt100Parser):
        parser = reader._vt100_parser = TerminalResponseParser(parser)
    if isinstance(parser, TerminalResponseParser):
        # VT-mode console readers can also receive native focus records.
        native = _native_console_focus(reader)
        if native is not None:
            native.focus_listeners = parser.focus_listeners
        return parser
    return _native_console_focus(reader)


def _native_console_focus(reader):
    parser = getattr(reader, "_corki_focus_parser", None)
    if parser is None and callable(getattr(reader, "_get_keys", None)):
        parser = reader._corki_focus_parser = NativeConsoleFocus(reader)
    return parser
