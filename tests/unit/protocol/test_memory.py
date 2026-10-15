from corki.protocol.memory import MemoryCitationStreamFilter, parse_memory_citation


def test_memory_citation_is_parsed_and_removed_from_visible_answer() -> None:
    text = """Use the established command.

<corki-memory-citation>
<citation_entries>
MEMORY.md:10-12|note=[project verification command]
</citation_entries>
<thread_ids>
019cc2ea-1dff-7902-8d40-c8f6e5d83cc4
</thread_ids>
</corki-memory-citation>"""

    visible, citation = parse_memory_citation(text)

    assert visible == "Use the established command.\n\n"
    assert citation is not None
    assert citation.entries[0].path == "MEMORY.md"
    assert citation.entries[0].line_start == 10
    assert citation.thread_ids == ("019cc2ea-1dff-7902-8d40-c8f6e5d83cc4",)


def test_stream_filter_hides_valid_citation_even_when_marker_is_split() -> None:
    stream = MemoryCitationStreamFilter()
    chunks = ["visible<cor", "ki-memory-citation>", "hidden", "</corki-memory-citation>"]
    visible = "".join(stream.push(chunk) for chunk in chunks)
    visible += stream.finish(citation_valid=True)

    assert visible == "visible"


def test_stream_filter_does_not_delay_text_unrelated_to_marker() -> None:
    stream = MemoryCitationStreamFilter()

    assert stream.push("short answer") == "short answer"
    assert stream.finish(citation_valid=False) == ""


def test_stream_filter_hides_open_block_even_without_valid_metadata() -> None:
    stream = MemoryCitationStreamFilter()
    visible = stream.push("answer <corki-memory-citation> malformed")
    visible += stream.finish(citation_valid=False)

    assert visible == "answer "
