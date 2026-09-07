"""Default source inventory for tool discovery, bounded independently of tool output."""

from corki.protocol.tools import ToolSpec

MAX_SOURCE_DESCRIPTION_BYTES = 512 * 1024


def render_sources(specs: tuple[ToolSpec, ...]) -> str:
    """Reserve complete names before spending the remaining UTF-8 budget on descriptions."""
    sources: dict[str, str | None] = {}
    for spec in specs:
        if spec.exposure.is_deferred and spec.source and sources.get(spec.source) is None:
            sources[spec.source] = spec.source_description
    if not sources:
        return "None currently enabled."
    reserved_names = len(sources) - 1 + sum(2 + len(name.encode("utf-8")) for name in sources)
    description_budget = max(0, MAX_SOURCE_DESCRIPTION_BYTES - reserved_names)
    entries: list[str] = []
    rendered_bytes = 0
    for name, description in sorted(sources.items()):
        required = int(bool(entries)) + 2 + len(name.encode("utf-8"))
        if required > MAX_SOURCE_DESCRIPTION_BYTES - rendered_bytes:
            continue
        entry = "- " + name
        rendered_bytes += required
        if description is not None and description_budget >= 2:
            description_budget -= 2
            bounded = description.encode("utf-8")[:description_budget].decode(
                "utf-8", errors="ignore"
            )
            size = len(bounded.encode("utf-8"))
            entry += ": " + bounded
            description_budget -= size
            rendered_bytes += 2 + size
        entries.append(entry)
    return "\n".join(entries)
