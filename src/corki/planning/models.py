"""Planning value objects shared by the update-plan tool and graph state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class PlanStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class PlanItem:
    step: str
    status: PlanStatus

    def as_dict(self) -> dict[str, str]:
        return {"step": self.step, "status": self.status.value}


def validate_plan(raw_plan: object) -> tuple[PlanItem, ...]:
    """Parse a model-provided plan and enforce Codex's single-active-step rule."""

    if not isinstance(raw_plan, list) or not raw_plan:
        raise ValueError("plan must be a non-empty array")
    items: list[PlanItem] = []
    for index, raw_item in enumerate(raw_plan):
        if not isinstance(raw_item, Mapping):
            raise ValueError(f"plan item {index} must be an object")
        step = raw_item.get("step")
        status = raw_item.get("status")
        if not isinstance(step, str) or not step.strip():
            raise ValueError(f"plan item {index} requires a non-empty step")
        try:
            parsed_status = PlanStatus(status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"plan item {index} has invalid status: {status!r}") from exc
        items.append(PlanItem(step=step.strip(), status=parsed_status))
    active_count = sum(item.status is PlanStatus.IN_PROGRESS for item in items)
    if active_count > 1:
        raise ValueError("at most one plan item may be in_progress")
    return tuple(items)
