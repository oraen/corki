"""Immutable runtime configuration models.

Configuration is resolved once at the composition root.  Graph nodes receive
the resulting value object instead of consulting environment variables or the
filesystem halfway through a turn.
"""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from corki.config.features import MCPServerSettings, parse_mcp_servers


@dataclass(frozen=True, slots=True)
class CorkiSettings:
    """Settings that are safe to freeze for one interactive CLI session."""

    working_directory: Path
    model: str = "gpt-5"
    api_base: str = "https://api.openai.com/v1"
    provider_name: str | None = None
    api_key: str | None = None
    api_mode: str = "chat_completions"
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None
    command_timeout_seconds: float = 120.0
    command_yield_seconds: float = 10.0
    max_steps: int = 24
    max_tool_calls: int = 64
    context_window_tokens: int = 65_536
    auto_compact_tokens: int = 49_152
    tool_output_char_budget: int = 20_000
    tool_search_mode: str = "compatible"
    model_max_retries: int = 3
    model_retry_base_seconds: float = 0.5
    model_response_char_limit: int = 4_000_000
    event_queue_size: int = 256
    skills_enabled: bool = True
    plugin_dirs: tuple[Path, ...] = ()
    disabled_plugins: frozenset[str] = frozenset()
    mcp_servers: tuple[MCPServerSettings, ...] = ()
    realtime_enabled: bool = False
    max_realtime_inputs: int = 16
    # Long-term memories are feature-gated like Codex's MemoryTool.  The
    # generation and read switches stay separate so operators can temporarily
    # stop background sampling without hiding an existing memory index.
    memories_enabled: bool = False
    memories_generate: bool = True
    memories_use: bool = True
    memories_dedicated_tools: bool = False
    memories_disable_on_external_context: bool = False
    memories_max_raw_for_consolidation: int = 256
    memories_max_unused_days: int = 30
    memories_max_thread_age_days: int = 10
    memories_max_threads_per_startup: int = 2
    memories_min_thread_idle_hours: int = 6
    memories_lease_seconds: int = 3_600
    memories_retry_delay_seconds: int = 3_600
    memories_summary_token_limit: int = 2_500
    memories_extraction_token_limit: int = 150_000
    memories_extraction_model: str | None = None
    memories_consolidation_model: str | None = None

    def __post_init__(self) -> None:
        self._validate()

    @classmethod
    def for_directory(
        cls,
        directory: Path | None = None,
        *,
        config_file: Path | None = None,
    ) -> CorkiSettings:
        """Resolve settings from TOML, then apply explicit environment overrides.

        Unknown TOML keys are ignored so future releases can add settings
        without making older Corki binaries unable to start.  Invalid values
        for known keys fail fast with a useful configuration error.
        """

        working_directory = (directory or Path.cwd()).expanduser().resolve()
        defaults = cls(working_directory=working_directory)
        values: dict[str, Any] = {}
        if config_file is not None and config_file.is_file():
            with config_file.open("rb") as handle:
                document = tomllib.load(handle)
            agent = document.get("agent", {})
            provider = document.get("provider", {})
            tools = document.get("tools", {})
            runtime = document.get("runtime", {})
            skills = document.get("skills", {})
            plugins = document.get("plugins", {})
            mcp = document.get("mcp", {})
            realtime = document.get("realtime", {})
            memories = document.get("memories", {})
            memory = document.get("memory", {})  # pre-0.2 compatibility
            if not all(
                isinstance(section, dict)
                for section in (
                    agent,
                    provider,
                    tools,
                    runtime,
                    skills,
                    plugins,
                    mcp,
                    realtime,
                    memories,
                    memory,
                )
            ):
                raise ValueError("Corki config sections must be TOML tables")
            plugin_directories = plugins.get("directories", [])
            disabled_plugins = plugins.get("disabled", [])
            if not isinstance(plugin_directories, list) or not all(
                isinstance(value, str) for value in plugin_directories
            ):
                raise ValueError("plugins.directories must be an array of paths")
            if not isinstance(disabled_plugins, list) or not all(
                isinstance(value, str) for value in disabled_plugins
            ):
                raise ValueError("plugins.disabled must be an array of names")
            values.update(
                model=agent.get("model", defaults.model),
                max_steps=agent.get("max_steps", defaults.max_steps),
                max_tool_calls=agent.get("max_tool_calls", defaults.max_tool_calls),
                context_window_tokens=agent.get(
                    "context_window_tokens", defaults.context_window_tokens
                ),
                auto_compact_tokens=agent.get("auto_compact_tokens", defaults.auto_compact_tokens),
                api_base=provider.get("base_url", defaults.api_base),
                provider_name=provider.get("name", defaults.provider_name),
                api_key=provider.get("api_key"),
                api_mode=provider.get("api_mode", defaults.api_mode),
                thinking_enabled=provider.get("thinking", defaults.thinking_enabled),
                reasoning_effort=provider.get("reasoning_effort", defaults.reasoning_effort),
                model_max_retries=provider.get("max_retries", defaults.model_max_retries),
                model_retry_base_seconds=provider.get(
                    "retry_base_seconds", defaults.model_retry_base_seconds
                ),
                model_response_char_limit=provider.get(
                    "response_char_limit", defaults.model_response_char_limit
                ),
                command_timeout_seconds=tools.get(
                    "command_timeout_seconds", defaults.command_timeout_seconds
                ),
                command_yield_seconds=tools.get(
                    "command_yield_seconds", defaults.command_yield_seconds
                ),
                tool_output_char_budget=tools.get(
                    "output_char_budget", defaults.tool_output_char_budget
                ),
                tool_search_mode=tools.get("search_mode", defaults.tool_search_mode),
                event_queue_size=runtime.get(
                    "event_queue_size",
                    memory.get("event_queue_size", defaults.event_queue_size),
                ),
                skills_enabled=skills.get("enabled", defaults.skills_enabled),
                plugin_dirs=tuple(Path(value).expanduser() for value in plugin_directories),
                disabled_plugins=frozenset(disabled_plugins),
                mcp_servers=parse_mcp_servers(mcp.get("servers")),
                realtime_enabled=realtime.get("enabled", defaults.realtime_enabled),
                max_realtime_inputs=realtime.get(
                    "max_inputs_per_turn", defaults.max_realtime_inputs
                ),
                memories_enabled=memories.get("enabled", defaults.memories_enabled),
                memories_generate=memories.get("generate", defaults.memories_generate),
                memories_use=memories.get("use", defaults.memories_use),
                memories_dedicated_tools=memories.get(
                    "dedicated_tools", defaults.memories_dedicated_tools
                ),
                memories_disable_on_external_context=memories.get(
                    "disable_on_external_context",
                    defaults.memories_disable_on_external_context,
                ),
                memories_max_raw_for_consolidation=memories.get(
                    "max_raw_for_consolidation",
                    defaults.memories_max_raw_for_consolidation,
                ),
                memories_max_unused_days=memories.get(
                    "max_unused_days", defaults.memories_max_unused_days
                ),
                memories_max_thread_age_days=memories.get(
                    "max_thread_age_days", defaults.memories_max_thread_age_days
                ),
                memories_max_threads_per_startup=memories.get(
                    "max_threads_per_startup",
                    defaults.memories_max_threads_per_startup,
                ),
                memories_min_thread_idle_hours=memories.get(
                    "min_thread_idle_hours",
                    defaults.memories_min_thread_idle_hours,
                ),
                memories_lease_seconds=memories.get(
                    "lease_seconds", defaults.memories_lease_seconds
                ),
                memories_retry_delay_seconds=memories.get(
                    "retry_delay_seconds", defaults.memories_retry_delay_seconds
                ),
                memories_summary_token_limit=memories.get(
                    "summary_token_limit", defaults.memories_summary_token_limit
                ),
                memories_extraction_token_limit=memories.get(
                    "extraction_token_limit", defaults.memories_extraction_token_limit
                ),
                memories_extraction_model=memories.get("extraction_model"),
                memories_consolidation_model=memories.get("consolidation_model"),
            )

        if model_override := os.environ.get("CORKI_MODEL"):
            values["model"] = model_override
        else:
            values.setdefault("model", defaults.model)
        if api_base_override := os.environ.get("CORKI_API_BASE"):
            values["api_base"] = api_base_override.rstrip("/")
        else:
            values.setdefault("api_base", defaults.api_base)
            if isinstance(values["api_base"], str):
                values["api_base"] = values["api_base"].rstrip("/")
        values["api_key"] = (
            os.environ.get("CORKI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or values.get("api_key")
        )
        return cls(working_directory=working_directory, **values)

    def _validate(self) -> None:
        """Reject values that would make turn bounds or truncation ineffective."""

        if not isinstance(self.tool_search_mode, str) or self.tool_search_mode not in {
            "disabled",
            "compatible",
            "native",
        }:
            raise ValueError("tools.search_mode must be disabled, compatible, or native")
        if self.tool_search_mode == "native" and self.api_mode != "responses":
            raise ValueError("native tool search requires a capable Responses provider/model")

        integer_values = {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "context_window_tokens": self.context_window_tokens,
            "auto_compact_tokens": self.auto_compact_tokens,
            "tool_output_char_budget": self.tool_output_char_budget,
            "model_max_retries": self.model_max_retries,
            "model_response_char_limit": self.model_response_char_limit,
            "event_queue_size": self.event_queue_size,
            "max_realtime_inputs": self.max_realtime_inputs,
            "memories_max_raw_for_consolidation": self.memories_max_raw_for_consolidation,
            "memories_max_unused_days": self.memories_max_unused_days,
            "memories_max_thread_age_days": self.memories_max_thread_age_days,
            "memories_max_threads_per_startup": self.memories_max_threads_per_startup,
            "memories_min_thread_idle_hours": self.memories_min_thread_idle_hours,
            "memories_lease_seconds": self.memories_lease_seconds,
            "memories_retry_delay_seconds": self.memories_retry_delay_seconds,
            "memories_summary_token_limit": self.memories_summary_token_limit,
            "memories_extraction_token_limit": self.memories_extraction_token_limit,
        }
        invalid_types = [
            name
            for name, value in integer_values.items()
            if not isinstance(value, int) or isinstance(value, bool)
        ]
        if invalid_types:
            raise ValueError(f"Corki settings must be integers: {', '.join(invalid_types)}")
        numeric_values = {
            "command_timeout_seconds": self.command_timeout_seconds,
            "command_yield_seconds": self.command_yield_seconds,
            "model_retry_base_seconds": self.model_retry_base_seconds,
        }
        invalid_numeric = [
            name
            for name, value in numeric_values.items()
            if not isinstance(value, (int, float)) or isinstance(value, bool)
        ]
        if invalid_numeric:
            raise ValueError(f"Corki settings must be numbers: {', '.join(invalid_numeric)}")
        non_finite = [name for name, value in numeric_values.items() if not math.isfinite(value)]
        if non_finite:
            raise ValueError(f"Corki settings must be finite: {', '.join(non_finite)}")
        positive_numbers = {
            "command_timeout_seconds": self.command_timeout_seconds,
            "command_yield_seconds": self.command_yield_seconds,
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "context_window_tokens": self.context_window_tokens,
            "auto_compact_tokens": self.auto_compact_tokens,
            "tool_output_char_budget": self.tool_output_char_budget,
            "model_retry_base_seconds": self.model_retry_base_seconds,
            "model_response_char_limit": self.model_response_char_limit,
            "event_queue_size": self.event_queue_size,
            "max_realtime_inputs": self.max_realtime_inputs,
            "memories_max_raw_for_consolidation": self.memories_max_raw_for_consolidation,
            "memories_max_thread_age_days": self.memories_max_thread_age_days,
            "memories_max_threads_per_startup": self.memories_max_threads_per_startup,
            "memories_lease_seconds": self.memories_lease_seconds,
            "memories_retry_delay_seconds": self.memories_retry_delay_seconds,
            "memories_summary_token_limit": self.memories_summary_token_limit,
            "memories_extraction_token_limit": self.memories_extraction_token_limit,
        }
        invalid = [name for name, value in positive_numbers.items() if value <= 0]
        if invalid:
            raise ValueError(f"Corki settings must be positive: {', '.join(invalid)}")
        if self.model_max_retries < 0:
            raise ValueError("provider.max_retries must be zero or greater")
        if not 1 <= self.memories_max_raw_for_consolidation <= 4_096:
            raise ValueError("memories.max_raw_for_consolidation must be between 1 and 4096")
        if not 1 <= self.memories_max_threads_per_startup <= 128:
            raise ValueError("memories.max_threads_per_startup must be between 1 and 128")
        if not 0 <= self.memories_max_unused_days <= 365:
            raise ValueError("memories.max_unused_days must be between 0 and 365")
        if self.memories_min_thread_idle_hours < 0:
            raise ValueError("memories.min_thread_idle_hours must be zero or greater")
        if self.auto_compact_tokens >= self.context_window_tokens:
            raise ValueError("agent.auto_compact_tokens must be below context_window_tokens")
        if not isinstance(self.api_mode, str) or self.api_mode not in {
            "chat_completions",
            "responses",
        }:
            raise ValueError(f"unsupported provider api_mode: {self.api_mode}")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("agent.model must be a non-empty string")
        if not isinstance(self.api_base, str) or urlparse(self.api_base).scheme not in {
            "http",
            "https",
        }:
            raise ValueError("provider.base_url must be an http(s) URL")
        if self.thinking_enabled is not None and not isinstance(self.thinking_enabled, bool):
            raise ValueError("provider.thinking must be true or false")
        if self.reasoning_effort is not None and (
            not isinstance(self.reasoning_effort, str) or not self.reasoning_effort.strip()
        ):
            raise ValueError("provider.reasoning_effort must be a non-empty string")
        if self.provider_name is not None and (
            not isinstance(self.provider_name, str) or not self.provider_name.strip()
        ):
            raise ValueError("provider.name must be a non-empty string")
        if self.api_key is not None and not isinstance(self.api_key, str):
            raise ValueError("provider.api_key must be a string")
        if not isinstance(self.skills_enabled, bool):
            raise ValueError("skills.enabled must be true or false")
        if not isinstance(self.realtime_enabled, bool):
            raise ValueError("realtime.enabled must be true or false")
        memory_booleans = {
            "memories.enabled": self.memories_enabled,
            "memories.generate": self.memories_generate,
            "memories.use": self.memories_use,
            "memories.dedicated_tools": self.memories_dedicated_tools,
            "memories.disable_on_external_context": self.memories_disable_on_external_context,
        }
        invalid_memory_booleans = [
            name for name, value in memory_booleans.items() if not isinstance(value, bool)
        ]
        if invalid_memory_booleans:
            raise ValueError(
                "Corki memory settings must be booleans: " + ", ".join(invalid_memory_booleans)
            )
        for name, value in (
            ("memories.extraction_model", self.memories_extraction_model),
            ("memories.consolidation_model", self.memories_consolidation_model),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.plugin_dirs, tuple) or not all(
            isinstance(value, Path) for value in self.plugin_dirs
        ):
            raise ValueError("plugins.directories must be an array of paths")
        if not isinstance(self.disabled_plugins, frozenset) or not all(
            isinstance(value, str) for value in self.disabled_plugins
        ):
            raise ValueError("plugins.disabled must be an array of names")
