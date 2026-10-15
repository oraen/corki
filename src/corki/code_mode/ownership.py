"""Host-owned cell identity inherited by admitted nested tool tasks."""

from contextvars import ContextVar

parent_call_id: ContextVar[str | None] = ContextVar("code_mode_parent_call_id", default=None)
