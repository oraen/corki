"""Line-only plan markup in ordinary assistant text, independent of transport."""

from dataclasses import dataclass
from typing import Literal

_OPEN, _CLOSE = "<proposed_plan>", "</proposed_plan>"
# Rust Unicode White_Space excludes Python's extra U+001C..001F characters.
_SPACE = (
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


@dataclass(frozen=True, slots=True)
class PlanSegment:
    kind: Literal["normal", "start", "delta", "end"]
    text: str = ""


class ProposedPlanParser:
    """Buffer only undecided tag prefixes; fences do not change tag recognition."""

    def __init__(self):
        self.active = False
        self.detect = True
        self.buffer = ""

    def push(self, text: str) -> tuple[PlanSegment, ...]:
        segments = []
        run = []
        for char in text:
            if self.detect:
                self.buffer += char
                if char == "\n":
                    self._line(segments)
                else:
                    slug = self.buffer.strip(_SPACE)
                    if not (_OPEN.startswith(slug) or _CLOSE.startswith(slug)):
                        self._text(self.buffer, segments)
                        self.buffer = ""
                        self.detect = False
            else:
                run.append(char)
                if char == "\n":
                    self._text("".join(run), segments)
                    run.clear()
                    self.detect = True
        self._text("".join(run), segments)
        return tuple(segments)

    def finish(self) -> tuple[PlanSegment, ...]:
        segments = []
        if self.buffer:
            self._line(segments)
        if self.active:
            segments.append(PlanSegment("end"))
            self.active = False
        self.detect = True
        return tuple(segments)

    def _line(self, segments):
        line, self.buffer = self.buffer, ""
        slug = line.strip(_SPACE)
        if slug == _OPEN and not self.active:
            self.active = True
            segments.append(PlanSegment("start"))
        elif slug == _CLOSE and self.active:
            self.active = False
            segments.append(PlanSegment("end"))
        else:
            self._text(line, segments)
        self.detect = True

    def _text(self, text, segments):
        if not text:
            return
        kind = "delta" if self.active else "normal"
        if segments and segments[-1].kind == kind:
            text = segments.pop().text + text
        segments.append(PlanSegment(kind, text))


def split_proposed_plan(text: str) -> tuple[str, str | None]:
    """Return visible prose and the last plan block, including an empty block."""
    parser = ProposedPlanParser()
    normal, plan = [], None
    for segment in (*parser.push(text), *parser.finish()):
        if segment.kind == "normal":
            normal.append(segment.text)
        elif segment.kind == "start":
            plan = []
        elif segment.kind == "delta" and plan is not None:
            plan.append(segment.text)
    return "".join(normal), "".join(plan) if plan is not None else None
