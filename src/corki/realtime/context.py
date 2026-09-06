"""Prompt state that tells the model when the user can steer a live turn."""

from __future__ import annotations

from pathlib import Path

from corki.prompting import PromptContribution, PromptRole, PromptSlot


class RealtimeContextContributor:
    def contributions(self, *, cwd: Path, user_input: str, realtime_active: bool):
        del cwd, user_input
        if not realtime_active:
            return ()
        return (
            PromptContribution(
                key="realtime.active",
                template_name="realtime/start",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.REALTIME,
            ),
        )
