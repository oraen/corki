"""History navigation reuses decoded rows, including styles, until source changes."""

import asyncio
from io import StringIO

from prompt_toolkit.data_structures import Point, Size
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from rich.console import Console

from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


def test_navigation_reuses_parsed_rows_and_refreshes_changed_history(tmp_path, monkeypatch):
    async def scenario():
        import corki.cli.history_rows as module

        ui = TerminalUI(
            CorkiSettings(tmp_path),
            tmp_path / "history",
            console=Console(file=StringIO(), force_terminal=True),
        )
        ui.show_assistant_message("\n\n".join(f"**line {i}**" for i in range(100)))
        view = ui._history_view
        original, parsed = module.ANSI, []

        def parse(text):
            parsed.append(text)
            return original(text)

        monkeypatch.setattr(module, "ANSI", parse)
        first = view.control.create_content(80, 10)
        for row in (0, 3, 10, 45):
            view.row = row
            content = view.control.create_content(80, 10)
            assert content.cursor_position == Point(0, row)
            assert content.line_count == first.line_count
            assert content.get_line(row) is first.get_line(row)
        assert len(parsed) == 1
        expected = list(
            split_lines(
                to_formatted_text(
                    original(ui._transcript.render(80, include_reasoning=True, expand_tools=True))
                )
            )
        )
        assert ["".join(p[1] for p in row) for row in view.lines] == [
            "".join(p[1] for p in row) for row in expected
        ]
        assert any(style for style, _, *_ in view.text())
        assert len(parsed) == 1
        ui.show_notice("new tail")
        second = view.control.create_content(80, 10)
        assert len(parsed) == 2
        assert second.line_count > first.line_count
        assert view.row == 45
        # Older frame content must remain immutable while newer source is visible.
        assert first.line_count < second.line_count
        assert "new tail" not in "".join(
            text for _, text, *_ in first.get_line(first.line_count - 1)
        )
        monkeypatch.setattr(ui._session.app.output, "get_size", lambda: Size(rows=10, columns=40))
        view.control.create_content(40, 10)
        assert len(parsed) == 3
        stores = tuple(view._stores)
        view.close()
        assert all(store.closed for store in stores)

    asyncio.run(scenario())
