"""Provider-private item metadata is filtered on request copies, not durable history."""

METADATA = "internal_chat_message_metadata_passthrough"


def filter_request_metadata(items, *, supported=None, content_item_kinds=None):
    """Legacy flags cannot enable internal protocol fields; preserve business content."""
    result = []
    for original in items:
        item = dict(original)
        if isinstance(item.get("id"), str):
            prefix, separator, suffix = item["id"].partition("_")
            if not (prefix and separator and suffix):
                item.pop("id")
        item.pop(METADATA, None)
        if item.get("type") == "function_call":
            item.pop("encrypted_function_args", None)
        result.append(item)
    return result
