"""Model completion validation and continuation decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from corki.protocol.items import ConversationItem, ToolCallItem


class EvaluationDecision(StrEnum):
    EXECUTE_TOOLS = "execute_tools"
    CONTINUE = "continue"
    FINALIZE = "finalize"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class StepEvaluation:
    decision: EvaluationDecision
    error: str | None = None


def evaluate_model_step(
    items: tuple[ConversationItem, ...],
    *,
    end_turn: bool | None = None,
) -> StepEvaluation:
    """Validate one normalized model step before selecting the next graph edge."""

    tool_calls = tuple(item.call for item in items if isinstance(item, ToolCallItem))
    needs_follow_up = bool(tool_calls) or end_turn is False
    if tool_calls:
        call_ids = [call.id for call in tool_calls]
        if len(set(call_ids)) != len(call_ids):
            return StepEvaluation(EvaluationDecision.FAIL, "model returned duplicate tool call ids")
        return StepEvaluation(EvaluationDecision.EXECUTE_TOOLS)
    if needs_follow_up:
        return StepEvaluation(EvaluationDecision.CONTINUE)
    # A validated ModelCompleted may have no user-facing output. Missing stream
    # completion is a protocol failure handled before reaching this evaluator.
    return StepEvaluation(EvaluationDecision.FINALIZE)
