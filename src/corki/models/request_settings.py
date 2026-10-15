"""Resolve model-owned request defaults without changing selection or transport state."""

from corki.models.types import ModelRequest


def resolve_reasoning(request: ModelRequest, inherited_effort: str | None) -> dict[str, str]:
    """Share pinned reasoning semantics across Responses and compatible adapters."""
    effort = (
        request.reasoning_effort
        if request.reasoning_effort is not None
        or request.reasoning_effort_resolved
        or request.model_info is not None
        else inherited_effort
    )
    info = request.model_info
    if info is not None and not request.reasoning_effort_resolved:
        effort = info.reasoning_effort_for_request(effort)
    summary = request.reasoning_summary
    if summary is None:
        summary = info.default_reasoning_summary if info is not None else "auto"
    result = {}
    if effort is not None:
        result["effort"] = effort
    if summary != "none" and (info is None or info.supports_reasoning_summary_parameter):
        result["summary"] = summary
    return result


def resolve_service_tier(request: ModelRequest) -> str | None:
    """Use only explicit, enabled and model-supported tiers; never infer a default."""
    if not request.fast_mode_enabled or request.model_info is None:
        return None
    return request.model_info.service_tier_for_request(request.service_tier)
