import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from corki.cli.backtrack import prompt_input
from corki.cli.command_completion import CommandCompleter
from corki.cli.inline_images import ImageDraft
from corki.cli.reference_completion import accept_reference, plugin_mention_name
from corki.plugins.mentions import explicit_plugin_ids
from corki.plugins.models import PluginManifest
from corki.protocol.items import UserMessageItem


@pytest.mark.parametrize(
    ("name", "display", "expected"),
    [
        ("mcp-search", "MCP Search", "MCP-Search"),
        ("google_calendar", "Google Calendar", "Google_Calendar"),
        ("sample", "Sample Plugin", "Sample"),
        ("browser-use", "Browser Use", "Browser-Use"),
    ],
)
def test_plugin_marker_matches_codex_examples(name, display, expected):
    assert plugin_mention_name(name, display) == expected


@pytest.mark.parametrize("query", ["MCP", "mcp-search", "personal", "team"])
def test_plugin_search_keeps_canonical_binding(query):
    async def scenario():
        plugins = tuple(
            PluginManifest(
                "mcp-search",
                None,
                "local fixture",
                Path("/plugins") / owner,
                plugin_id="mcp-search@" + owner,
                interface_display_name="MCP Search",
            )
            for owner in ("personal", "team")
        )
        completer = CommandCompleter()

        async def load():
            return (), plugins

        completer.references = load
        document = Document("@" + query)
        rows = [row async for row in completer.get_completions_async(document, CompleteEvent())]
        expected = (query,) if query in {"personal", "team"} else ("personal", "team")
        assert [row.reference.selector.path for row in rows] == [
            "plugin://mcp-search@" + owner for owner in expected
        ]
        for row, owner in zip(rows, expected, strict=True):
            assert row.text == "@MCP-Search"
            buffer = Buffer(document=document)
            draft = ImageDraft()
            draft.clear(document.text)
            buffer.on_text_changed += lambda b, draft=draft: draft.sync(b.text, b.cursor_position)
            buffer.start_completion = lambda **kwargs: None
            from prompt_toolkit.buffer import CompletionState

            buffer.complete_state = CompletionState(document, [row], 0)
            assert accept_reference(buffer, draft)
            assert draft.text == "@MCP-Search "
            assert explicit_plugin_ids(draft.text, draft.mentions) == {"mcp-search@" + owner}
            restored = prompt_input(UserMessageItem(draft.text, "turn", mentions=draft.mentions))
            assert restored.draft.bindings == draft.bindings

    asyncio.run(scenario())
