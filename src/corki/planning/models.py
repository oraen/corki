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
    """Parse plan data; single-active-step guidance belongs to the tool description."""

    if not isinstance(raw_plan, list):
        raise ValueError("plan must be an array")
    items: list[PlanItem] = []
    for index, raw_item in enumerate(raw_plan):
        if not isinstance(raw_item, Mapping):
            raise ValueError(f"plan item {index} must be an object")
        step = raw_item.get("step")
        status = raw_item.get("status")
        if not isinstance(step, str):
            raise ValueError(f"plan item {index} requires a string step")
        try:
            parsed_status = PlanStatus(status)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"plan item {index} has invalid status: {status!r}") from exc
        items.append(PlanItem(step=step, status=parsed_status))
    return tuple(items)
