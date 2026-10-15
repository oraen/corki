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
            config_file=resolved_home / "settings.json",
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

        from corki.config.document import dumps
        from corki.config.migration import migrate_user_file
        from corki.config.toml_edits import write_text

        if not self.config_file.exists() and not migrate_user_file(self.config_file):
            write_text(
                self.config_file,
                "// Corki settings: comments and trailing commas are supported.\n"
                + dumps(
                    {
                        "default": {"provider": "default", "model": "gpt-5"},
                        "providers": {
                            "default": {
                                "base_url": "",
                                "api_mode": "chat_completions",
                                "max_retries": 3,
                                "retry_base_seconds": 0.5,
                                "models": {"gpt-5": {"context_window": 65536}},
                            }
                        },
                    }
                ),
            )
        # The provider table may contain a local-development API key. Keep
        # the file private even when the user's umask would create it as 0644.
        self.config_file.chmod(0o600)
