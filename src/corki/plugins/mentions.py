"""Exact plugin IDs from host selectors or native @ resource links."""

from corki.skills.mentions import linked_paths


def explicit_plugin_ids(text: str, mentions=()) -> frozenset[str]:
    paths = {mention.path for mention in mentions if mention.kind == "mention"}
    return frozenset(
        path[9:].split("?", 1)[0]
        for path in paths | linked_paths(text, sigil="@")
        if path.startswith("plugin://") and path[9:].split("?", 1)[0]
    )
