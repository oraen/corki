import asyncio

import pytest

from corki.config import CorkiSettings, TokenBudgetConfig
from corki.config.token_budget import DEFAULT_REMINDER, parse_token_budget
from corki.context.tokens import estimate_item_tokens
from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import BudgetNoticeItem, item_from_payload, item_kind, item_to_payload
from corki.storage import SQLiteSessionRepository


def test_notice_codec_checkpoint_legacy_and_token_cost(tmp_path):
    async def scenario():
        item = BudgetNoticeItem("plain developer guidance", TurnId("turn"), "window", "reminder")
        assert item_from_payload(item_kind(item), item_to_payload(item)) == item
        serializer = checkpoint_serializer()
        assert serializer.loads_typed(serializer.dumps_typed(item)) == item
        assert estimate_item_tokens(item) > 12
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread = ThreadId("thread")
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (item,))
        reopened = SQLiteSessionRepository(tmp_path / "sessions.db")
        assert await reopened.load_items(thread) == (item,)
        messages = await reopened.load_messages(thread)
        assert len(messages) == 1
        assert messages[0].role.value == "developer" and messages[0].content == item.content

    asyncio.run(scenario())


def test_table_configuration_normalization_and_disabled_defaults(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""[features.token_budget]
enabled = true
reminder_threshold_tokens = 123
guidance_message = "  keep spaces  "
auto_compact_fallback_prompt = "  save notes  "
auto_compact_fallback_buffer_tokens = 456
""")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.token_budget_enabled
    assert settings.token_budget == TokenBudgetConfig(
        reminder_threshold_tokens=123,
        guidance_message="  keep spaces  ",
        auto_compact_fallback_prompt="save notes",
        auto_compact_fallback_buffer_tokens=456,
    )
    enabled, blank = parse_token_budget(
        {
            "enabled": True,
            "guidance_message": " \t ",
            "auto_compact_fallback_prompt": " \n ",
            "auto_compact_fallback_buffer_tokens": 999,
        }
    )
    assert enabled and blank.guidance_message is None
    assert blank.auto_compact_fallback_prompt is None and blank.fallback_buffer_tokens == 0
    assert parse_token_budget({}) == (False, None)
    assert parse_token_budget({"enabled": False, "reminder_threshold_tokens": -1}) == (False, None)
    assert parse_token_budget(True) == (True, None)
    assert TokenBudgetConfig().reminder_message_template == DEFAULT_REMINDER


@pytest.mark.parametrize(
    "option",
    [
        {"reminder_threshold_tokens": "many"},
        {"reminder_threshold_tokens": True},
        {"guidance_message": 3},
        {"use_history_notes_extension": "true"},
        {"auto_compact_fallback_buffer_tokens": 2**63},
    ],
)
def test_disabled_table_still_requires_valid_field_types(option):
    with pytest.raises(ValueError, match="type"):
        parse_token_budget({"enabled": False, **option})


def test_notes_config_is_opt_in_and_legacy_backend_declaration_is_inert(tmp_path, monkeypatch):
    from corki.config import CorkiSettings

    monkeypatch.delenv("CORKI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = tmp_path / "config.toml"
    config.write_text(
        "[features.token_budget]\nenabled = true\nuse_history_notes_extension = true\n"
        '[provider]\nname = "openai"\napi_mode = "responses"\ncodex_backend = true\n',
        encoding="utf-8",
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.token_budget.use_history_notes_extension
    assert not hasattr(settings, "codex_backend")


@pytest.mark.parametrize(
    "field", ["reminder_message_template", "guidance_message", "auto_compact_fallback_prompt"]
)
def test_text_limits_count_utf8_bytes(field):
    base = {"auto_compact_fallback_buffer_tokens": 1}
    assert TokenBudgetConfig(**base, **{field: "界" * 666 + "aa"})
    with pytest.raises(ValueError, match="2000 UTF-8 bytes"):
        TokenBudgetConfig(**base, **{field: "界" * 667})


@pytest.mark.parametrize(
    "options",
    [
        {"reminder_threshold_tokens": 0},
        {"reminder_threshold_tokens": True},
        {"reminder_threshold_tokens": 1.5},
        {"auto_compact_fallback_buffer_tokens": -1},
        {"auto_compact_fallback_buffer_tokens": False},
        {"reminder_message_template": " \t "},
        {"reminder_message_template": None},
        {"guidance_message": 1},
        {"auto_compact_fallback_prompt": "save"},
        {"use_history_notes_extension": "true"},
    ],
)
def test_invalid_active_budget_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        TokenBudgetConfig(**options)


@pytest.mark.parametrize(
    "options",
    [{"enabled": 1}, {"enabled": True, "typo": 1}, {"enabled": False, "typo": 1}, "true", None, []],
)
def test_invalid_feature_table_is_rejected(options):
    with pytest.raises(ValueError):
        parse_token_budget(options)
