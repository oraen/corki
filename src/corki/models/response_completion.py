"""Ordinary Responses completion and token usage, without product billing fields."""

from corki.models.base import ModelError, ModelErrorKind
from corki.models.types import ModelCompleted, ModelUsage


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("expected completion object")
    return value


def _i64(value):
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError("completion usage requires signed i64 counters")
    return value


def _usage(value):
    source = _object(value)
    input_details = source.get("input_tokens_details")
    output_details = source.get("output_tokens_details")
    cached = cache_write = reasoning = 0
    if input_details is not None:
        details = _object(input_details)
        cached = _i64(details.get("cached_tokens"))
        cache_write = _i64(details.get("cache_write_tokens", 0))
    if output_details is not None:
        reasoning = _i64(_object(output_details).get("reasoning_tokens"))
    return ModelUsage(
        input_tokens=_i64(source.get("input_tokens")),
        output_tokens=_i64(source.get("output_tokens")),
        total_tokens=_i64(source.get("total_tokens")),
        cached_tokens=cached,
        reasoning_tokens=reasoning,
        cache_write_tokens=cache_write,
    )


def parse_response_completion(value) -> ModelCompleted:
    """Validate all fields before publishing any usage/termination state."""
    try:
        source = _object(value)
        response_id = source.get("id")
        if not isinstance(response_id, str):
            raise ValueError("Responses completion requires a string id")
        end_turn = source.get("end_turn")
        if end_turn is not None and not isinstance(end_turn, bool):
            raise ValueError("Responses end_turn must be a boolean or null")
        raw_usage = source.get("usage")
        usage = ModelUsage() if raw_usage is None else _usage(raw_usage)
        metadata = {"response_id": response_id}
        return ModelCompleted((), usage=usage, provider_metadata=metadata, end_turn=end_turn)
    except ValueError as error:
        raise ModelError(str(error), kind=ModelErrorKind.PROTOCOL, retryable=True) from error
