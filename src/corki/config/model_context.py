"""Validated static model catalog for providers without a native catalog endpoint."""

from dataclasses import replace

from corki.protocol.context import ModelContextInfo
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
                context_window=fields.get("context_window"),
                max_context_window=fields.get("max_context_window"),
                effective_context_window_percent=fields.get("effective_context_window_percent", 95),
                truncation_policy=TruncationPolicy.from_mapping(fields.get("truncation_policy")),
            )
        )
    return tuple(entries)
