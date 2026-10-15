from io import StringIO

from rich.console import Console

from corki.cli import stream_markdown
from corki.cli.stream_markdown import StreamMarkdown


def test_rendered_lines_queue_without_output_then_drain_in_order(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(stream_markdown, "monotonic", lambda: now[0])
    output = StringIO()
    console = Console(file=output, width=40)
    stream = StreamMarkdown()
    stream.enqueue(console, "first\n\nsecond\n")
    assert stream.queued_lines > 1
    assert stream.emitted == 0 and output.getvalue() == ""
    assert stream.oldest_queued_age(10.25) == 0.25
    count = stream.queued_lines
    assert stream.drain(console, 1)
    assert stream.emitted == 1 and stream.queued_lines == count - 1
    now[0] = 11.0
    stream.enqueue(console, "first\n\nsecond\n\nthird\n")
    assert stream.oldest_queued_age(11.0) == 1.0
    assert stream.drain(console, stream.queued_lines)
    assert not stream.drain(console, 1)
    assert stream.oldest_queued_age(12.0) is None
    text = output.getvalue()
    assert text.count("first") == text.count("second") == text.count("third") == 1
    assert text.index("first") < text.index("second") < text.index("third")


def test_resize_cannot_discard_queued_wrapped_remainder():
    output = StringIO()
    console = Console(file=output, width=12)
    source = "alpha bravo charlie delta echo foxtrot golf hotel\n"
    stream = StreamMarkdown()
    stream.enqueue(console, source)
    assert stream.queued_lines >= 3
    stream.drain(console, stream.queued_lines - 1)
    console.width = 100
    stream.enqueue(console, source)
    assert stream.queued_lines >= 1
    assert stream.drain(console, stream.queued_lines)
    assert "hotel" in output.getvalue()


def test_source_refresh_does_not_reset_existing_queue_age(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(stream_markdown, "monotonic", lambda: now[0])
    stream = StreamMarkdown()
    console = Console(file=StringIO(), width=40)
    stream.enqueue(console, "one\n")
    count = stream.queued_lines
    now[0] = 2.0
    stream.enqueue(console, "one\n")
    assert stream.queued_lines == count
    assert stream.oldest_queued_age(2.0) == 1.0
    assert not stream.drain(console, 0)
