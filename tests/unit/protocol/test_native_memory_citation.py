"""Pinned native literal hidden-tag and independently optional metadata contract."""

import asyncio
import base64

import pytest

from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.items import AssistantMessageItem, item_from_payload, item_to_payload
from corki.protocol.memory import MemoryCitationStreamFilter, parse_memory_citation
from corki.storage import SQLiteSessionRepository

SOURCE = "019cc2ea-1dff-7902-8d40-c8f6e5d83cc4"


@pytest.mark.parametrize("tag", ["oai-mem-citation", "corki-memory-citation"])
@pytest.mark.parametrize(
    ("template", "visible"),
    [
        ("a<{tag}>bad</{tag}>b", "ab"),
        ("a<{tag}>bad", "a"),
        ("a<{tag}>one</{tag}>b<{tag}>two</{tag}>c", "abc"),
        ("a<{tag}>x<{tag}>y</{tag}>z</{tag}>b", "az</{tag}>b"),
        ("a\n\n<{tag}>bad</{tag}>", "a\n\n"),
        ("<{tag}></{tag}>", ""),
        ("<{tag}>bad</{tag}>literal <", "literal <"),
        ("literal <{tag}", "literal <{tag}"),
    ],
)
def test_complete_and_every_stream_split_share_visibility(tag, template, visible):
    text, expected = template.format(tag=tag), visible.format(tag=tag)
    assert parse_memory_citation(text) == (expected, None)
    for split in range(len(text) + 1):
        parser = MemoryCitationStreamFilter()
        actual = parser.push(text[:split]) + parser.push(text[split:])
        actual += parser.finish(citation_valid=False)
        assert actual == expected, split
    parser = MemoryCitationStreamFilter()
    assert "".join(parser.push(char) for char in text) + parser.finish() == expected


def test_closed_block_releases_suffix_before_finish_and_filter_can_be_reused():
    parser = MemoryCitationStreamFilter()
    assert parser.push("abc <oai-mem-") == "abc "
    assert parser.push("citation>bad</oai-mem-citation>suffix") == "suffix"
    assert parser.finish() == ""
    assert parser.push("next") == "next"


def test_optional_sections_multiple_aliases_and_raw_ids_are_independent():
    text = (
        "a<oai-mem-citation><citation_entries>\n"
        "MEMORY.md: 0 - 2 |note=[ first ]\n"
        "../inert.md:9-3|note=[metadata is not a read grant]\n"
        "bad.md:4294967296-4|note=[overflow]\n"
        "</citation_entries></oai-mem-citation>b"
        f"<corki-memory-citation><thread_ids>{SOURCE}\ninvalid\n{SOURCE}</thread_ids>"
        "</corki-memory-citation>c"
        f"<oai-mem-citation><rollout_ids>raw-id\n{SOURCE}</rollout_ids>"
        "<thread_ids>ignored</thread_ids></oai-mem-citation>"
    )
    visible, citation = parse_memory_citation(text)
    assert visible == "abc"
    assert citation is not None
    assert [(e.path, e.line_start, e.line_end, e.note) for e in citation.entries] == [
        ("MEMORY.md", 0, 2, "first"),
        ("../inert.md", 9, 3, "metadata is not a read grant"),
    ]
    assert citation.rollout_ids == (SOURCE, "invalid", "raw-id")
    assert citation.thread_ids == (SOURCE,)


@pytest.mark.parametrize("closed", [False, True])
def test_id_only_metadata_survives_invalid_uuid_and_unterminated_outer_tag(closed):
    text = "<oai-mem-citation><rollout_ids>not-a-uuid</rollout_ids>"
    if closed:
        text += "</oai-mem-citation>"
    visible, citation = parse_memory_citation(text)
    assert visible == ""
    assert citation is not None
    assert citation.rollout_ids == ("not-a-uuid",)
    assert citation.thread_ids == ()


def test_batch146_checkpoint_and_old_json_keep_legacy_citations(tmp_path):
    # Produced by the immutable installed batch146 package (2c8d92), not the
    # current serializer pretending to be an old checkpoint.
    old = (
        "yAGgApO0Y29ya2kucHJvdG9jb2wuaXRlbXO0QXNzaXN0YW50TWVzc2FnZUl0ZW2Jp2NvbnRlbnSubGVn"
        "YWN5IGRpc3BsYXmndHVybl9pZKhvbGQtdHVybqdzdGVwX2lkqG9sZC1zdGVwomlkqG9sZC1pdGVtqmNy"
        "ZWF0ZWRfYXS5MjAyNi0wOS0wMVQwMDowMDowMCswMDowMK9tZW1vcnlfY2l0YXRpb27HvwKTtWNvcmtp"
        "LnByb3RvY29sLm1lbW9yea5NZW1vcnlDaXRhdGlvboKnZW50cmllc5HHWgKTtWNvcmtpLnByb3RvY29s"
        "Lm1lbW9yebNNZW1vcnlDaXRhdGlvbkVudHJ5hKRwYXRoqU1FTU9SWS5tZKpsaW5lX3N0YXJ0AahsaW5l"
        "X2VuZAKkbm90ZaNvbGSqdGhyZWFkX2lkc5HZJDAxOWNjMmVhLTFkZmYtNzkwMi04ZDQwLWM4ZjZlNWQ4"
        "M2NjNKVwaGFzZcC7cmVzcG9uc2VfaXRlbV9tZXRhZGF0YV9qc29uwLJyZXNwb25zZV9ib2R5X2pzb27A"
    )

    async def scenario():
        codec = checkpoint_serializer()
        restored = codec.loads_typed(("msgpack", base64.b64decode(old)))
        assert isinstance(restored, AssistantMessageItem)
        assert restored.response_body_json is None
        assert restored.content == "legacy display"
        assert restored.memory_citation.rollout_ids == (SOURCE,)
        assert restored.memory_citation.thread_ids == (SOURCE,)
        payload = item_to_payload(restored)
        assert "rollout_ids" not in payload["memory_citation"]
        assert item_from_payload("assistant_message", payload) == restored
        assert codec.loads_typed(codec.dumps_typed(restored)) == restored
        repository = SQLiteSessionRepository(tmp_path / "s.db")
        try:
            await repository.create_thread("thread", tmp_path)
            await repository.append_items("thread", (restored,))
            await repository.append_items("thread", (restored,))
            assert await repository.load_items("thread") == (restored,)
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("body", "paths", "ids"),
    [
        (
            "<rollout_ids>one\rtwo\u2028three\n\x1craw\x1c</rollout_ids>",
            (),
            ("one\rtwo\u2028three", "\x1craw\x1c"),
        ),
        (
            "<citation_entries>a.md:" + "0" * 5000 + "-1|note=[zero]</citation_entries>",
            ("a.md",),
            (),
        ),
        ("<citation_entries>\x1ca.md:1-2|note=[x]</citation_entries>", ("\x1ca.md",), ()),
        ("<rollout_ids></rollout_ids><thread_ids>ignored</thread_ids>", (), ()),
    ],
)
def test_native_line_and_whitespace_rules(body, paths, ids):
    visible, parsed = parse_memory_citation("<oai-mem-citation>" + body + "</oai-mem-citation>")
    assert visible == ""
    if not paths and not ids:
        assert parsed is None
    else:
        assert tuple(e.path for e in parsed.entries) == paths
        assert parsed.rollout_ids == ids
