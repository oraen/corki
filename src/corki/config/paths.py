"""Filesystem layout used by Corki configuration and persistence adapters.

Keeping path construction in one place prevents UI and runtime code from
silently inventing incompatible locations as the project grows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CorkiPaths:
    """Resolved paths for configuration and durable runtime data."""

    home: Path
    config_file: Path
    history_dir: Path
    input_history_file: Path
    logs_dir: Path
    sessions_dir: Path
    skills_dir: Path
    plugins_dir: Path
    memories_dir: Path

    @classmethod
    def discover(cls) -> CorkiPaths:
        """Build the default layout, honoring ``CORKI_HOME`` when provided."""

        configured_home = os.environ.get("CORKI_HOME")
        home = Path(configured_home).expanduser() if configured_home else Path.home() / ".corki"
        return cls.from_home(home)

    @classmethod
    def from_home(cls, home: Path) -> CorkiPaths:
        """Build a layout rooted at ``home`` without touching the filesystem."""

        resolved_home = home.expanduser()
        history_dir = resolved_home / "history"
        return cls(
            home=resolved_home,
            config_file=resolved_home / "config.toml",
            history_dir=history_dir,
            input_history_file=history_dir / "input-history",
            logs_dir=resolved_home / "logs",
            sessions_dir=resolved_home / "sessions",
            skills_dir=resolved_home / "skills",
            plugins_dir=resolved_home / "plugins",
            memories_dir=resolved_home / "memories",
        )

    def ensure_exists(self) -> None:
        """Create private runtime directories and an initial config file."""

        for directory in (
            self.home,
            self.history_dir,
            self.logs_dir,
            self.sessions_dir,
            self.skills_dir,
            self.plugins_dir,
            self.memories_dir,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)

        if not self.config_file.exists():
            self.config_file.write_text(
                "# Corki configuration\n\n"
                "[agent]\n"
                'model = "gpt-5"\n'
                "max_steps = 24\n"
                "max_tool_calls = 64\n"
                "context_window_tokens = 65536\n"
                "auto_compact_tokens = 49152\n\n"
                "[provider]\n"
                '# name = "openai"\n'
                'base_url = "https://api.openai.com/v1"\n'
                'api_mode = "chat_completions"\n'
                "# thinking = true\n"
                '# reasoning_effort = "high"\n\n'
                "max_retries = 3\n"
                "retry_base_seconds = 0.5\n"
                "response_char_limit = 4000000\n\n"
                "[tools]\n"
                "command_timeout_seconds = 120\n"
                "command_yield_seconds = 10\n"
                "output_char_budget = 20000\n\n"
                "[runtime]\n"
                "event_queue_size = 256\n\n"
                "[skills]\n"
                "enabled = true\n\n"
                "[plugins]\n"
                "directories = []\n"
                "disabled = []\n\n"
                "[realtime]\n"
                "enabled = false\n"
                "max_inputs_per_turn = 16\n\n"
                "[memories]\n"
                "# Codex-aligned long-term memory pipeline; opt in explicitly.\n"
                "enabled = false\n"
                "generate = true\n"
                "use = true\n"
                "dedicated_tools = false\n"
                "disable_on_external_context = false\n"
                "max_raw_for_consolidation = 256\n"
                "max_unused_days = 30\n"
                "max_thread_age_days = 10\n"
                "max_threads_per_startup = 2\n"
                "min_thread_idle_hours = 6\n\n"
                "# Example MCP server:\n"
                "# [mcp.servers.filesystem]\n"
                '# transport = "stdio"\n'
                '# command = "npx"\n'
                '# args = ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]\n',
                encoding="utf-8",
            )
        # The provider table may contain a local-development API key. Keep
        # the file private even when the user's umask would create it as 0644.
        self.config_file.chmod(0o600)
