"""Finite protocol type allowlist for durable graph state (never pickle)."""

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_CHECKPOINT_TYPES = tuple(
    (module, name)
    for module, names in (
        (
            "corki.protocol.items",
            (
                "UserMessageItem",
                "AssistantMessageItem",
                "ReasoningItem",
                "ToolCallItem",
                "ToolResultItem",
                "HostedToolItem",
                "ContextItem",
                "ContextRole",
                "CompactionItem",
                "RemoteHistoryItem",
                "TurnAbortedItem",
                "BudgetNoticeItem",
            ),
        ),
        (
            "corki.protocol.tools",
            (
                "ToolSpec",
                "ToolCall",
                "ToolArgumentMap",
                "ToolResult",
                "ToolStateUpdate",
                "ToolExposure",
                "ToolConcurrency",
                "ToolInputKind",
                "TextContent",
                "EncryptedContent",
                "ImageAttachment",
                "AudioAttachment",
                "CodeModeOutput",
            ),
        ),
        ("corki.protocol.memory", ("MemoryCitation", "MemoryCitationEntry")),
        ("corki.protocol.input_mentions", ("InputMention",)),
        ("corki.protocol.settings", ("ModelSettingsSnapshot",)),
        ("corki.protocol.context", ("ModelContextInfo",)),
        ("corki.protocol.instruction_template", ("ModelInstructionTemplate",)),
        ("corki.protocol.model_authority", ("ModelAuthority",)),
        ("corki.protocol.permission_messages", ("ModelPermissionMessages",)),
        ("corki.protocol.truncation", ("TruncationPolicy",)),
        ("corki.protocol.messages", ("Message", "MessageRole")),
    )
    for name in names
)


def checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=_CHECKPOINT_TYPES,
        allowed_json_modules=_CHECKPOINT_TYPES,
        pickle_fallback=False,
    )
