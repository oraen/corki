"""Static local plugin guidance is independent of mutable capability catalogs."""

from corki.protocol.items import CompactionItem, ContextItem, ContextRole, ConversationItem

KEY = "extensions.plugins.guidance"
CATALOG_KEY = "extensions.plugins.catalog"
LEGACY_REVOCATION = (
    "The previously provided instructions and facts for "
    "'extensions.plugins.catalog' no longer apply."
)


def is_retained(history: tuple[ConversationItem, ...]) -> bool:
    legacy_revoked = False
    for item in reversed(history):
        if isinstance(item, CompactionItem):
            break
        if (
            isinstance(item, ContextItem)
            and item.key == KEY
            and item.role is ContextRole.DEVELOPER
            and item.content_kind == "plugins.usage_instructions"
            and item.content
        ):
            return True
        if (
            isinstance(item, ContextItem)
            and item.key == CATALOG_KEY
            and item.role is ContextRole.DEVELOPER
        ):
            # Old generic tombstones revoked the whole mixed section. Do not
            # mistake an earlier, revoked usage paragraph for active guidance.
            if not legacy_revoked and (
                "<plugins_instructions>" in item.content
                and "Plugins are not invoked directly." in item.content
            ):
                return True
            if LEGACY_REVOCATION in item.content or not item.content:
                legacy_revoked = True
    return False
