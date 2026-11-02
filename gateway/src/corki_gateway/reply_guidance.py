"""One per-run reply instruction, carried on requests rather than in history."""

from corki.protocol.ids import TurnId
from corki.protocol.items import ContextItem, ContextRole


def request_context(text: str) -> tuple[ContextItem, ...]:
    text = text.strip()
    if not text:
        return ()
    return (
        ContextItem(
            key="gateway.reply_guidance",
            role=ContextRole.DEVELOPER,
            content=(
                "The following administrator guidance applies to replies to the external user. "
                "Keep the agent's existing execution, permission and tool requirements. "
                "For internal context summarization, follow the dedicated summary instructions "
                "rather than the user-facing reply format; do not copy these reply instructions "
                "into the summary.\n"
                "<channel_reply_guidance>\n" + text + "\n</channel_reply_guidance>"
            ),
            turn_id=TurnId("gateway-reply-guidance"),
            separate_message=True,
        ),
    )
