"""Validated static model catalog for providers without a native catalog endpoint."""

from dataclasses import replace

from corki.protocol.context import ModelContextInfo
from corki.protocol.instruction_template import ModelInstructionTemplate
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.permission_messages import ModelPermissionMessages
from corki.protocol.truncation import TruncationPolicy


def match_model_context(
    model: str, candidates: tuple[ModelContextInfo, ...]
) -> ModelContextInfo | None:
    """Codex lookup order: full-name longest prefix, then one scoped namespace.

    Metadata is inherited; the requested model identity is never rewritten to
    the matching catalog prefix. Equal-length entries retain input order.
    """

    def longest_prefix(name: str) -> ModelContextInfo | None:
        best = None
        for candidate in candidates:
            if name.startswith(candidate.model) and (
                best is None or len(candidate.model) > len(best.model)
            ):
                best = candidate
        return best

    selected = longest_prefix(model)
    if selected is None:
        namespace, slash, suffix = model.partition("/")
        if (
            slash
            and namespace
            and "/" not in suffix
            and all(char.isascii() and (char.isalnum() or char in "_-") for char in namespace)
        ):
            selected = longest_prefix(suffix)
    return replace(selected, model=model) if selected is not None else None


def parse_model_contexts(value: object) -> tuple[ModelContextInfo, ...] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("models.catalog must be a table keyed by model name")
    entries = []
    for name, fields in value.items():
        if not isinstance(fields, dict):
            raise ValueError("each models.catalog entry must be a table")
        entries.append(
            ModelContextInfo(
                model=name,
                base_instructions=fields.get("base_instructions"),
                instruction_template=ModelInstructionTemplate.from_catalog(
                    fields.get("model_messages")
                ),
                tool_mode=fields.get("tool_mode"),
                supports_search_tool=fields.get("supports_search_tool", False),
                activation_authority=ModelAuthority.from_catalog(fields),
                permission_messages=ModelPermissionMessages.from_catalog(
                    fields.get("model_messages")
                ),
                context_window=fields.get("context_window"),
                max_context_window=fields.get("max_context_window"),
                effective_context_window_percent=fields.get("effective_context_window_percent", 95),
                truncation_policy=TruncationPolicy.from_mapping(fields.get("truncation_policy")),
                comp_hash=fields.get("comp_hash"),
                service_tiers=_service_tiers(fields.get("service_tiers", [])),
                default_service_tier=fields.get("default_service_tier"),
                supported_reasoning_levels=_reasoning_levels(
                    fields.get("supported_reasoning_levels", [])
                ),
                default_reasoning_level=fields.get("default_reasoning_level"),
                multi_agent_reasoning_effort=fields.get("multi_agent_reasoning_effort"),
                default_reasoning_summary=fields.get("default_reasoning_summary", "auto"),
                supports_reasoning_summary_parameter=fields.get(
                    "supports_reasoning_summary_parameter", True
                ),
            )
        )
    return tuple(entries)


def _reasoning_levels(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(preset, dict)
        or not isinstance(preset.get("effort"), str)
        or not preset["effort"]
        for preset in value
    ):
        raise ValueError("supported_reasoning_levels must be an array of nonempty effort tables")
    return tuple(preset["effort"] for preset in value)


def _service_tiers(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(tier, dict) or not isinstance(tier.get("id"), str) for tier in value
    ):
        raise ValueError("service_tiers must be an array of string id tables")
    return tuple(tier["id"] for tier in value)
