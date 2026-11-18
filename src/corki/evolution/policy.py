"""Hermes agent_init and background_review policy, read once at turn admission."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewPolicy:
    interval: int = 10
    enabled: bool = True
    provider: str | None = None
    model: str | None = None
    max_input_tokens: int | None = None
    extra_tools: tuple[str, ...] = ()

    @classmethod
    def load(cls, document):
        skills = document.get("skills", {})
        auxiliary = document.get("auxiliary", {})
        task = auxiliary.get("background_review", {}) if isinstance(auxiliary, dict) else {}
        task = task if isinstance(task, dict) else {}
        interval = skills.get("creation_nudge_interval", 10) if isinstance(skills, dict) else 10
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = 10
        budget = task.get("max_input_tokens")
        try:
            budget = int(budget) if budget is not None else None
        except (TypeError, ValueError):
            budget = None
        extras = task.get("extra_tools", [])
        extras = extras if isinstance(extras, list) else []
        return cls(
            interval,
            task.get("enabled", True) is not False,
            task.get("provider"),
            task.get("model"),
            budget,
            tuple(t.strip() for t in extras if isinstance(t, str) and t.strip()),
        )
