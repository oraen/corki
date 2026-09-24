"""Immutable runtime configuration models.

Configuration is resolved once at the composition root.  Graph nodes receive
the resulting value object instead of consulting environment variables or the
filesystem halfway through a turn.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from corki.config.base_instructions import resolve_base_instructions
from corki.config.bundled_models import BUNDLED_MODEL_CONTEXTS
from corki.config.features import MCPServerSettings, parse_mcp_servers
from corki.config.hooks import ManagedHookPolicy
from corki.config.instructions import ProjectInstructionsConfig
from corki.config.layers import LocalConfigState, load_local_config
from corki.config.model_context import match_model_context, parse_model_contexts
from corki.config.permissions import (
    ExecutionDefault,
    ExecutionPermissions,
    parse_execution_permissions,
)
from corki.config.plugins import LayerDisabledPlugins, LayerPluginDirectories, plugin_selection
from corki.config.project_trust import active_project_trust
from corki.config.providers import select_provider
from corki.config.shell_environment import ShellEnvironmentPolicy, parse_shell_environment_policy
from corki.config.skills import SkillRule, skill_rules_from_layers
from corki.config.token_budget import TokenBudgetConfig, parse_token_budget
from corki.protocol.collaboration import ModeKind, validate_mode
from corki.protocol.context import ContextLimits, ModelContextInfo
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.settings import ThreadModelSettings

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CorkiSettings:
    """Settings that are safe to freeze for one interactive CLI session."""

    working_directory: Path
    model: str = "gpt-5"
    collaboration_mode: ModeKind = "default"
    collaboration_instructions: str | None = None
    personality: str | None = None
    personality_enabled: bool = True
    plan_mode_reasoning_effort: str | None = None
    request_user_input_enabled: bool = True
    default_mode_request_user_input: bool = False
    api_base: str = ""
    provider_name: str | None = None
    api_key: str | None = None
    api_mode: str = "chat_completions"
    supports_audio_input: bool = False
    supports_encrypted_tool_output: bool = False
    supports_image_input: bool = True
    supports_image_detail_original: bool = False
    unified_image_budget: bool = False
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None
    command_timeout_seconds: float | None = None
    allow_login_shell: bool = True
    shell_environment_policy: ShellEnvironmentPolicy = field(default_factory=ShellEnvironmentPolicy)
    execution_permissions: ExecutionPermissions | None | ExecutionDefault = ExecutionDefault.AUTO
    write_stdin_approval: bool = False
    command_yield_seconds: float = 10.0
    background_terminal_max_timeout: int = 300_000
    max_steps: int | None = None
    max_tool_calls: int | None = None
    context_window_tokens: int = 65_536
    include_environment_context: bool = True
    include_permissions_instructions: bool = True
    project_instructions: ProjectInstructionsConfig = field(
        default_factory=ProjectInstructionsConfig
    )
    auto_compact_tokens: int | None = None
    auto_compact_token_limit_scope: str = "total"
    token_budget_enabled: bool = False
    # Compatibility tombstone, never selects a model protocol.
    remote_compaction_v2: bool = False
    token_budget: TokenBudgetConfig | None = None
    effective_context_window_percent: int = 95
    # Legacy UI preview cap; model history uses model_info.truncation_policy.
    tool_output_char_budget: int = 20_000
    tool_output_token_limit: int | None = None
    tool_search_mode: str = "compatible"
    turn_metadata_includes_tool_info: bool = False
    error_on_tool_collisions: bool = False
    deferred_tool_world_state: bool = False
    non_prefixed_mcp_tool_names: bool = False
    non_prefixed_mcp_tool_servers: tuple[str, ...] | None = None
    tool_freeform_mode: str = "compatible"
    tool_namespace_mode: str = "compatible"
    tool_mode: str = "direct"
    code_mode_disable_fallback: bool = False
    code_mode_direct_only_tool_namespaces: tuple[str, ...] = ()
    code_mode_excluded_tool_namespaces: tuple[str, ...] = ()
    model_max_retries: int = 5
    model_request_max_retries: int = 4
    model_unbounded_connection_retries: bool = True
    model_retry_base_seconds: float = 0.2
    model_response_char_limit: int = 4_000_000
    # Zero matches the native unbounded output channel; positive values opt into backpressure.
    event_queue_size: int = 0
    skills_enabled: bool = True
    skill_mcp_dependency_install: bool = True
    skills_max_context_tokens: int | None = None
    plugins_enabled: bool = True
    plugin_dirs: tuple[Path, ...] = field(default_factory=LayerPluginDirectories)
    disabled_plugins: frozenset[str] = field(default_factory=LayerDisabledPlugins)
    mcp_servers: tuple[MCPServerSettings, ...] = ()
    # File startup is implemented; other policies remain unavailable, not File aliases.
    mcp_oauth_credentials_store: str = "auto"
    orchestrator_mcp_enabled: bool = True
    mcp_optional_startup_grace_ms: int = 1000
    # MCP user review only, independent of local execution permissions.
    mcp_approval_policy: str = "never"
    mcp_auth_elicitation: bool = False
    mcp_tool_call_elicitation: bool = True
    # CLI live text input; not a provider Realtime API capability.
    realtime_enabled: bool = True
    notifications: bool | tuple[str, ...] = True
    notification_method: str = "auto"
    notification_condition: str = "unfocused"
    max_realtime_inputs: int = 16
    # Long-term memories are feature-gated like Codex's MemoryTool.  The
    # Source eligibility, automatic background work and recall are independent.
    # The background pause is a Corki host extension, not Codex's generate flag.
    memories_enabled: bool = False
    memories_generate: bool = True
    memories_background_enabled: bool = True
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
    memories_extraction_token_limit: int | None = None
    memories_extraction_model: str | None = None
    memories_consolidation_model: str | None = None
    skills_config: tuple[SkillRule, ...] = ()
    skills_include_instructions: bool = True
    compact_prompt: str | None = None
    agent_interrupt_message_enabled: bool = True
    model_contexts: tuple[ModelContextInfo, ...] | None = None
    model_context_window_override: int | None = None
    provider_memory_extraction_model: str | None = None
    provider_memory_consolidation_model: str | None = None
    reasoning_summary: str | None = None
    service_tier: str | None = None
    fast_mode: bool = True
    supports_service_tier: bool | None = None
    content_item_kinds: bool = True
    provider_id: str | None = None
    step_model_switching: bool = False
    # A recovered exact-model view is not an authoritative catalog/prefix entry.
    _admitted_model_info: ModelContextInfo | None = field(default=None, repr=False)
    configuration: LocalConfigState | None = None
    managed_hook_policy: ManagedHookPolicy | None = None
    hooks_enabled: bool = True
    # Directory loading records the pre-layer default, not the last effective value.
    _hooks_reload_default: bool | None = field(default=None, repr=False)
    base_instructions: str | None = None

    def __post_init__(self) -> None:
        if type(self.personality_enabled) is not bool:
            raise ValueError("features.personality must be a boolean")
        if self.personality not in (None, "none", "friendly", "pragmatic"):
            raise ValueError("personality must be none, friendly, pragmatic, or unset")
        if self.base_instructions is not None and not isinstance(self.base_instructions, str):
            raise ValueError("instructions must be a string or None")
        if type(self.hooks_enabled) is not bool:
            raise ValueError("features.hooks must be a boolean")
        if self._hooks_reload_default is not None and type(self._hooks_reload_default) is not bool:
            raise ValueError("hook reload default must be a boolean")
        if self.managed_hook_policy is not None and not isinstance(
            self.managed_hook_policy, ManagedHookPolicy
        ):
            raise ValueError("managed hook policy must be host-owned")
        validate_mode(self.collaboration_mode, self.collaboration_instructions)
        if self.plan_mode_reasoning_effort is not None and (
            not isinstance(self.plan_mode_reasoning_effort, str)
            or not self.plan_mode_reasoning_effort.strip()
        ):
            raise ValueError("plan_mode_reasoning_effort must be a non-empty string or None")
        for name in ("request_user_input_enabled", "default_mode_request_user_input"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.mcp_oauth_credentials_store not in ("auto", "file", "keyring"):
            raise ValueError("mcp_oauth_credentials_store must be auto, file, or keyring")
        object.__setattr__(self, "mcp_auth_elicitation", False)
        if type(self.mcp_tool_call_elicitation) is not bool:
            raise ValueError("features.tool_call_mcp_elicitation must be a boolean")
        if type(self.orchestrator_mcp_enabled) is not bool:
            raise ValueError("orchestrator.mcp.enabled must be a boolean")
        for name in ("direct_only_tool_namespaces", "excluded_tool_namespaces"):
            value = getattr(self, "code_mode_" + name)
            if not isinstance(value, (list, tuple)) or not all(isinstance(s, str) for s in value):
                raise ValueError(f"features.code_mode.{name} must be an array of strings")
            object.__setattr__(self, "code_mode_" + name, tuple(value))
        if type(self.write_stdin_approval) is not bool:
            raise ValueError("features.write_stdin_approval must be a bool")
        if self.configuration is not None and not isinstance(self.configuration, LocalConfigState):
            raise ValueError("configuration sources must be host-owned")
        if not isinstance(self.project_instructions, ProjectInstructionsConfig):
            raise ValueError("project instructions must be a host-owned configuration")
        if (
            self.execution_permissions is not None
            and self.execution_permissions is not ExecutionDefault.AUTO
            and not isinstance(self.execution_permissions, ExecutionPermissions)
        ):
            raise ValueError("execution_permissions must be host-owned ExecutionPermissions")
        if self.mcp_approval_policy not in ("never", "on-request"):
            raise ValueError("mcp.approval_policy must be never or on-request")
        if (
            type(self.mcp_optional_startup_grace_ms) is not int
            or not 0 <= self.mcp_optional_startup_grace_ms < 2**64
        ):
            raise ValueError("mcp_optional_startup_grace_ms must be a nonnegative uint64")
        if type(self.step_model_switching) is not bool:
            raise ValueError("features.step_model_switching must be a bool")
        if self._admitted_model_info is not None and (
            not isinstance(self._admitted_model_info, ModelContextInfo)
            or self._admitted_model_info.model != self.model
        ):
            raise ValueError("admitted model metadata must match the selected model")
        if not isinstance(self.content_item_kinds, bool):
            raise ValueError("features.content_item_kinds must be a bool")
        if not isinstance(self.fast_mode, bool):
            raise ValueError("features.fast_mode must be a bool")
        if self.supports_service_tier is not None and not isinstance(
            self.supports_service_tier, bool
        ):
            raise ValueError("provider.supports_service_tier must be a bool or None")
        if self.service_tier is not None and not isinstance(self.service_tier, str):
            raise ValueError("provider.service_tier must be a string or None")
        if self.service_tier in ("fast", "priority"):
            object.__setattr__(self, "service_tier", "priority" if self.fast_mode else None)
        if self.reasoning_summary is not None and self.reasoning_summary not in (
            "auto",
            "concise",
            "detailed",
            "none",
        ):
            raise ValueError("provider.reasoning_summary is invalid")
        if not isinstance(self.shell_environment_policy, ShellEnvironmentPolicy):
            raise ValueError("shell_environment_policy must be a ShellEnvironmentPolicy")
        if self.model_contexts is not None:
            object.__setattr__(self, "model_contexts", tuple(self.model_contexts))
            if not all(isinstance(info, ModelContextInfo) for info in self.model_contexts):
                raise ValueError("model_contexts must contain ModelContextInfo entries")
            if len({info.model for info in self.model_contexts}) != len(self.model_contexts):
                raise ValueError("model_contexts contains duplicate model names")
        for name, value in (
            ("models.context_window_override", self.model_context_window_override),
            ("memories.extraction_token_limit", self.memories_extraction_token_limit),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")
        self._validate()
        if self.execution_permissions is ExecutionDefault.AUTO:
            object.__setattr__(
                self,
                "execution_permissions",
                parse_execution_permissions(
                    None,
                    self.working_directory.expanduser().resolve(),
                    configuration={},
                    project_trust=self.project_instructions.trust_level,
                ),
            )

    def model_context_info(self, model: str) -> ModelContextInfo:
        if self._admitted_model_info is not None and model == self._admitted_model_info.model:
            return self._admitted_model_info
        catalog = self.model_contexts if self.model_contexts is not None else BUNDLED_MODEL_CONTEXTS
        info = match_model_context(model, catalog)
        if info is None:
            if model == self.model:
                info = ModelContextInfo(
                    model,
                    self.context_window_tokens,
                    effective_context_window_percent=self.effective_context_window_percent,
                    used_fallback_model_metadata=True,
                )
            else:
                _LOG.warning(
                    "Unknown model %s uses fallback window metadata (272000 tokens)", model
                )
                info = ModelContextInfo(model, 272_000, 272_000, used_fallback_model_metadata=True)
        else:
            # Provenance describes this lookup, not any marker in a catalog entry.
            info = replace(
                info,
                used_fallback_model_metadata=False,
                activation_authority=info.activation_authority or ModelAuthority(),
            )
        if (
            self.model_contexts is None
            and model == self.model
            and not info.used_fallback_model_metadata
        ):
            # Preserve Corki's existing main-agent window settings; the bundled
            # maximum still constrains this local override. Custom catalogs
            # retain their own windows, as before this bundled fallback existed.
            info = replace(
                info.with_override(self.context_window_tokens),
                effective_context_window_percent=self.effective_context_window_percent,
            )
        info = info.with_override(self.model_context_window_override)
        if self.tool_output_token_limit is not None:
            info = replace(
                info,
                truncation_policy=info.truncation_policy.with_token_override(
                    self.tool_output_token_limit
                ),
            )
        return info

    @property
    def session_service_tier(self) -> str | None:
        """Capture initial-model eligibility separately from the retained configuration."""
        if not self.fast_mode:
            return None
        if self.service_tier == "default":
            return "default"
        return self.model_context_info(self.model).service_tier_for_request(self.service_tier)

    @property
    def service_tier_warning(self) -> str | None:
        """Report an unsupported configured tier without silently substituting another."""
        if (
            self.fast_mode
            and self.service_tier is not None
            and self.service_tier != "default"
            and self.session_service_tier is None
        ):
            return (
                f"Configured service tier `{self.service_tier}` is not advertised as supported "
                f"for model `{self.model}` and will be omitted from requests."
            )
        return None

    @property
    def resolved_memory_extraction_model(self) -> str:
        return self.memories_extraction_model or self.provider_memory_extraction_model or self.model

    @property
    def resolved_memory_consolidation_model(self) -> str:
        return (
            self.memories_consolidation_model
            or self.provider_memory_consolidation_model
            or self.model
        )

    @property
    def main_context_limits(self) -> ContextLimits:
        info = self.model_context_info(self.model)
        return ContextLimits(
            info.resolved_context_window or self.context_window_tokens,
            self.auto_compact_tokens,
            info.effective_context_window_percent,
        )

    @classmethod
    def for_directory(
        cls,
        directory: Path | None = None,
        *,
        config_file: Path | None = None,
        resume_model_settings: ThreadModelSettings | None = None,
        model: str | None = None,
        provider: str | None = None,
        reasoning_effort: str | None = None,
    ) -> CorkiSettings:
        """Resolve settings from TOML, then apply explicit environment overrides.

        Unknown TOML keys are ignored so future releases can add settings
        without making older Corki binaries unable to start.  Invalid values
        for known keys fail fast with a useful configuration error.
        """

        working_directory = (directory or Path.cwd()).expanduser().resolve()
        # A value template, not an admitted execution configuration. Read the
        # complete config/trust before resolving a backend or a user override.
        defaults = cls(working_directory=working_directory, execution_permissions=None)
        values: dict[str, Any] = {}
        # Explicit host selection skips the whole persisted group, not just the
        # selected field. API-key rotation alone is not a model/provider override.
        if any(value is not None for value in (model, provider, reasoning_effort)) or any(
            os.environ.get(name) for name in ("CORKI_MODEL", "CORKI_API_BASE")
        ):
            resume_model_settings = None
        selected_provider = (
            resume_model_settings.provider if resume_model_settings is not None else provider
        )
        document, values["configuration"] = load_local_config(
            (directory or Path.cwd()).expanduser().absolute(), config_file
        )
        trust = active_project_trust(document, (directory or Path.cwd()).expanduser().absolute())
        values["base_instructions"] = resolve_base_instructions(document)
        values["project_instructions"] = ProjectInstructionsConfig.from_document(
            document, trust_level=trust
        )
        if document or selected_provider is not None:
            agent = document.get("agent", {})
            agents = document.get("agents", {})
            provider_id, provider_values = select_provider(document, selected_provider)
            tools = document.get("tools", {})
            runtime = document.get("runtime", {})
            skills = document.get("skills", {})
            plugins = document.get("plugins", {})
            mcp = document.get("mcp", {})
            realtime = document.get("realtime", {})
            tui = document.get("tui", {})
            memories = document.get("memories", {})
            memory = document.get("memory", {})  # pre-0.2 compatibility
            models = document.get("models", {})
            features = document.get("features", {})
            orchestrator = document.get("orchestrator", {})
            if not isinstance(orchestrator, dict):
                raise ValueError("orchestrator must be a TOML table")
            orchestrator_mcp = orchestrator.get("mcp", {})
            if not isinstance(orchestrator_mcp, dict):
                raise ValueError("orchestrator.mcp must be a TOML table")
            if not all(
                isinstance(section, dict)
                for section in (
                    agent,
                    agents,
                    provider_values,
                    tools,
                    runtime,
                    skills,
                    plugins,
                    mcp,
                    realtime,
                    tui,
                    memories,
                    memory,
                    models,
                    features,
                )
            ):
                raise ValueError("Corki config sections must be TOML tables")
            if not isinstance(features.get("tool_registry", {}), dict):
                raise ValueError("features.tool_registry must be a TOML table")
            user_input = tools.get("experimental_request_user_input", {})
            if not isinstance(user_input, dict):
                raise ValueError("tools.experimental_request_user_input must be a TOML table")
            values["request_user_input_enabled"] = user_input.get("enabled", True)
            values["plan_mode_reasoning_effort"] = document.get("plan_mode_reasoning_effort")
            values["default_mode_request_user_input"] = features.get(
                "default_mode_request_user_input", False
            )
            code_mode = features.get("code_mode", {})
            if isinstance(code_mode, bool):
                code_mode = {"enabled": code_mode}
            if not isinstance(code_mode, dict):
                raise ValueError("features.code_mode must be a boolean or TOML table")
            if "enabled" in code_mode and type(code_mode["enabled"]) is not bool:
                raise ValueError("features.code_mode.enabled must be a boolean")
            code_mode_only = features.get("code_mode_only", False)
            if type(code_mode_only) is not bool:
                raise ValueError("features.code_mode_only must be a boolean")
            plugin_directories = plugins.get("directories", [])
            disabled_plugins = plugins.get("disabled", [])
            # Native opaque package IDs can also be named directories/disabled.
            # Tables are package policies; arrays retain Corki's legacy selectors.
            if isinstance(plugin_directories, dict):
                plugin_directories = []
            if isinstance(disabled_plugins, dict):
                disabled_plugins = []
            if not isinstance(plugin_directories, list) or not all(
                isinstance(value, str) for value in plugin_directories
            ):
                raise ValueError("plugins.directories must be an array of paths")
            if not isinstance(disabled_plugins, list) or not all(
                isinstance(value, str) for value in disabled_plugins
            ):
                raise ValueError("plugins.disabled must be an array of names")
            plugin_directories, disabled_plugins = plugin_selection(
                values["configuration"], strict=True
            )
            budget_enabled, budget_config = parse_token_budget(features.get("token_budget", False))
            values.update(
                shell_environment_policy=parse_shell_environment_policy(
                    document.get("shell_environment_policy", {})
                ),
                token_budget=budget_config,
                model=agent.get("model", defaults.model),
                personality=agent.get("personality", defaults.personality),
                personality_enabled=features.get("personality", defaults.personality_enabled),
                max_steps=agent.get("max_steps", defaults.max_steps),
                agent_interrupt_message_enabled=agents.get(
                    "interrupt_message", defaults.agent_interrupt_message_enabled
                ),
                max_tool_calls=agent.get("max_tool_calls", defaults.max_tool_calls),
                context_window_tokens=agent.get(
                    "context_window_tokens", defaults.context_window_tokens
                ),
                include_environment_context=agent.get(
                    "include_environment_context", defaults.include_environment_context
                ),
                include_permissions_instructions=document.get(
                    "include_permissions_instructions", defaults.include_permissions_instructions
                ),
                auto_compact_tokens=agent.get("auto_compact_tokens", defaults.auto_compact_tokens),
                auto_compact_token_limit_scope=agent.get(
                    "auto_compact_token_limit_scope", defaults.auto_compact_token_limit_scope
                ),
                token_budget_enabled=budget_enabled,
                effective_context_window_percent=agent.get(
                    "effective_context_window_percent", defaults.effective_context_window_percent
                ),
                api_base=provider_values.get("base_url", defaults.api_base),
                provider_name=provider_values.get("name", defaults.provider_name),
                provider_id=provider_id,
                api_key=provider_values.get("api_key"),
                api_mode=provider_values.get("api_mode", defaults.api_mode),
                supports_audio_input=provider_values.get(
                    "supports_audio_input", defaults.supports_audio_input
                ),
                supports_encrypted_tool_output=provider_values.get(
                    "supports_encrypted_tool_output", defaults.supports_encrypted_tool_output
                ),
                supports_image_input=provider_values.get(
                    "supports_image_input", defaults.supports_image_input
                ),
                supports_image_detail_original=provider_values.get(
                    "supports_image_detail_original", defaults.supports_image_detail_original
                ),
                unified_image_budget=provider_values.get(
                    "unified_image_budget", defaults.unified_image_budget
                ),
                thinking_enabled=provider_values.get("thinking", defaults.thinking_enabled),
                reasoning_effort=provider_values.get("reasoning_effort", defaults.reasoning_effort),
                reasoning_summary=provider_values.get(
                    "reasoning_summary", defaults.reasoning_summary
                ),
                service_tier=provider_values.get("service_tier"),
                fast_mode=features.get("fast_mode", defaults.fast_mode),
                hooks_enabled=features.get("hooks", defaults.hooks_enabled),
                _hooks_reload_default=defaults.hooks_enabled,
                write_stdin_approval=features.get("write_stdin_approval", False),
                step_model_switching=features.get("step_model_switching", False),
                content_item_kinds=features.get("content_item_kinds", defaults.content_item_kinds),
                supports_service_tier=provider_values.get("supports_service_tier"),
                provider_memory_extraction_model=provider_values.get("memory_extraction_model"),
                provider_memory_consolidation_model=provider_values.get(
                    "memory_consolidation_model"
                ),
                model_max_retries=provider_values.get(
                    "stream_max_retries",
                    provider_values.get("max_retries", defaults.model_max_retries),
                ),
                model_request_max_retries=provider_values.get(
                    "request_max_retries", defaults.model_request_max_retries
                ),
                model_unbounded_connection_retries=provider_values.get(
                    "unbounded_connection_retries", defaults.model_unbounded_connection_retries
                ),
                model_retry_base_seconds=provider_values.get(
                    "retry_base_seconds", defaults.model_retry_base_seconds
                ),
                model_response_char_limit=provider_values.get(
                    "response_char_limit", defaults.model_response_char_limit
                ),
                command_timeout_seconds=tools.get(
                    "command_timeout_seconds", defaults.command_timeout_seconds
                ),
                allow_login_shell=tools.get("allow_login_shell", defaults.allow_login_shell),
                command_yield_seconds=tools.get(
                    "command_yield_seconds", defaults.command_yield_seconds
                ),
                background_terminal_max_timeout=tools.get(
                    "background_terminal_max_timeout", defaults.background_terminal_max_timeout
                ),
                tool_output_char_budget=tools.get(
                    "output_char_budget", defaults.tool_output_char_budget
                ),
                tool_output_token_limit=tools.get("output_token_limit"),
                tool_search_mode=tools.get("search_mode", defaults.tool_search_mode),
                error_on_tool_collisions=features.get("tool_registry", {}).get(
                    "error_on_tool_collisions", False
                ),
                deferred_tool_world_state=tools.get(
                    "deferred_tool_world_state", defaults.deferred_tool_world_state
                ),
                non_prefixed_mcp_tool_names=tools.get(
                    "non_prefixed_mcp_tool_names", defaults.non_prefixed_mcp_tool_names
                ),
                non_prefixed_mcp_tool_servers=tools.get("non_prefixed_mcp_tool_servers"),
                tool_freeform_mode=tools.get("freeform_mode", defaults.tool_freeform_mode),
                tool_namespace_mode=tools.get("namespace_mode", defaults.tool_namespace_mode),
                tool_mode=tools.get(
                    "mode",
                    "code_mode_only"
                    if code_mode_only
                    else "code_mode"
                    if code_mode.get("enabled", False)
                    else defaults.tool_mode,
                ),
                code_mode_direct_only_tool_namespaces=code_mode.get(
                    "direct_only_tool_namespaces", ()
                ),
                code_mode_excluded_tool_namespaces=code_mode.get("excluded_tool_namespaces", ()),
                code_mode_disable_fallback=tools.get(
                    "code_mode_disable_fallback", defaults.code_mode_disable_fallback
                ),
                event_queue_size=runtime.get(
                    "event_queue_size",
                    memory.get("event_queue_size", defaults.event_queue_size),
                ),
                skills_enabled=skills.get("enabled", defaults.skills_enabled),
                skill_mcp_dependency_install=features.get("skill_mcp_dependency_install", True),
                skills_max_context_tokens=skills.get("max_context_tokens"),
                skills_config=skill_rules_from_layers(values["configuration"]),
                skills_include_instructions=skills.get("include_instructions", True),
                compact_prompt=document.get("compact_prompt"),
                plugin_dirs=LayerPluginDirectories(
                    Path(value).expanduser() for value in plugin_directories
                ),
                disabled_plugins=LayerDisabledPlugins(disabled_plugins),
                plugins_enabled=features.get("plugins", True),
                mcp_servers=parse_mcp_servers(mcp.get("servers")),
                mcp_oauth_credentials_store=document.get("mcp_oauth_credentials_store", "auto"),
                orchestrator_mcp_enabled=orchestrator_mcp.get("enabled", True),
                mcp_optional_startup_grace_ms=document.get(
                    "mcp_optional_startup_grace_ms", defaults.mcp_optional_startup_grace_ms
                ),
                mcp_approval_policy=mcp.get("approval_policy", defaults.mcp_approval_policy),
                mcp_tool_call_elicitation=features.get("tool_call_mcp_elicitation", True),
                realtime_enabled=realtime.get("enabled", defaults.realtime_enabled),
                notifications=tui.get("notifications", defaults.notifications),
                notification_method=tui.get("notification_method", defaults.notification_method),
                notification_condition=tui.get(
                    "notification_condition", defaults.notification_condition
                ),
                max_realtime_inputs=realtime.get(
                    "max_inputs_per_turn", defaults.max_realtime_inputs
                ),
                memories_enabled=memories.get("enabled", defaults.memories_enabled),
                memories_generate=memories.get("generate", defaults.memories_generate),
                memories_background_enabled=memories.get(
                    "background_enabled", defaults.memories_background_enabled
                ),
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
                model_contexts=parse_model_contexts(models.get("catalog")),
                model_context_window_override=models.get("context_window_override"),
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
        if resume_model_settings is not None:
            # A fresh CLI session starts in Default with the resumed selection,
            # not the previous UI's active Plan mask. Pending Turns separately
            # retain their admitted mode/instructions in checkpoint snapshots.
            values["collaboration_mode"] = "default"
            values["collaboration_instructions"] = None
            values["model"] = resume_model_settings.model
            values["personality"] = resume_model_settings.personality
            # None is intentional: don't inherit the newly selected profile's effort.
            values["reasoning_effort"] = resume_model_settings.reasoning_effort
        else:
            if model is not None:
                values["model"] = model
            if reasoning_effort is not None:
                values["reasoning_effort"] = reasoning_effort
        values["execution_permissions"] = parse_execution_permissions(
            document.get("execution"),
            working_directory,
            configuration=document,
            project_trust=trust,
        )
        return cls(working_directory=working_directory, **values)

    def _validate(self) -> None:
        if self.tool_output_token_limit is not None and (
            type(self.tool_output_token_limit) is not int or self.tool_output_token_limit < 0
        ):
            raise ValueError("tools.output_token_limit must be a non-negative integer")
        if self.token_budget is not None and not isinstance(self.token_budget, TokenBudgetConfig):
            raise ValueError("token_budget must be a TokenBudgetConfig")
        if not isinstance(self.token_budget_enabled, bool):
            raise ValueError("features.token_budget must be a boolean")
        object.__setattr__(self, "remote_compaction_v2", False)
        if self.auto_compact_token_limit_scope not in ("total", "body_after_prefix"):
            raise ValueError("auto_compact_token_limit_scope must be total or body_after_prefix")
        """Reject values that would make turn bounds or truncation ineffective."""

        if not isinstance(self.tool_mode, str) or self.tool_mode not in {
            "direct",
            "code_mode",
            "code_mode_only",
        }:
            raise ValueError("tools.mode must be direct, code_mode, or code_mode_only")
        if not isinstance(self.code_mode_disable_fallback, bool):
            raise ValueError("tools.code_mode_disable_fallback must be a boolean")
        if not isinstance(self.agent_interrupt_message_enabled, bool):
            raise ValueError("agents.interrupt_message must be a boolean")
        if not isinstance(self.supports_audio_input, bool):
            raise ValueError("provider.supports_audio_input must be a boolean")
        if not isinstance(self.supports_encrypted_tool_output, bool):
            raise ValueError("provider.supports_encrypted_tool_output must be a boolean")
        if self.supports_encrypted_tool_output:
            raise ValueError("provider.supports_encrypted_tool_output is unsupported")
        for name in (
            "supports_image_input",
            "supports_image_detail_original",
            "unified_image_budget",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"provider.{name} must be a boolean")

        if not isinstance(self.tool_search_mode, str) or self.tool_search_mode not in {
            "disabled",
            "compatible",
            "native",
        }:
            raise ValueError("tools.search_mode must be disabled, compatible, or native")
        if self.tool_search_mode == "native":
            # Old configuration/checkpoints migrate to Harness-managed discovery.
            object.__setattr__(self, "tool_search_mode", "compatible")
        if not isinstance(self.deferred_tool_world_state, bool):
            raise ValueError("tools.deferred_tool_world_state must be a boolean")
        # Legacy settings/checkpoint field cannot re-enable internal tool inventory.
        object.__setattr__(self, "turn_metadata_includes_tool_info", False)
        if type(self.error_on_tool_collisions) is not bool:
            raise ValueError("features.tool_registry.error_on_tool_collisions must be a boolean")
        if not isinstance(self.non_prefixed_mcp_tool_names, bool):
            raise ValueError("tools.non_prefixed_mcp_tool_names must be a boolean")
        servers = self.non_prefixed_mcp_tool_servers
        if servers is not None:
            if not isinstance(servers, (tuple, list)) or any(
                not isinstance(s, str) for s in servers
            ):
                raise ValueError("tools.non_prefixed_mcp_tool_servers must be a list of strings")
            object.__setattr__(self, "non_prefixed_mcp_tool_servers", tuple(servers))
        if not isinstance(self.tool_freeform_mode, str) or self.tool_freeform_mode not in {
            "compatible",
            "native",
        }:
            raise ValueError("tools.freeform_mode must be compatible or native")
        object.__setattr__(self, "tool_freeform_mode", "compatible")
        if self.tool_namespace_mode not in ("compatible", "native"):
            raise ValueError("tools.namespace_mode must be compatible or native")
        object.__setattr__(self, "tool_namespace_mode", "compatible")

        count_limits = {
            name: value
            for name, value in {
                "max_steps": self.max_steps,
                "max_tool_calls": self.max_tool_calls,
            }.items()
            if value is not None
        }
        integer_values = {
            "background_terminal_max_timeout": self.background_terminal_max_timeout,
            **count_limits,
            "context_window_tokens": self.context_window_tokens,
            **(
                {"auto_compact_tokens": self.auto_compact_tokens}
                if self.auto_compact_tokens is not None
                else {}
            ),
            "effective_context_window_percent": self.effective_context_window_percent,
            "tool_output_char_budget": self.tool_output_char_budget,
            "model_max_retries": self.model_max_retries,
            "model_request_max_retries": self.model_request_max_retries,
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
        }
        invalid_types = [
            name
            for name, value in integer_values.items()
            if not isinstance(value, int) or isinstance(value, bool)
        ]
        if invalid_types:
            raise ValueError(f"Corki settings must be integers: {', '.join(invalid_types)}")
        if not 0 <= self.background_terminal_max_timeout <= 2**64 - 1:
            raise ValueError("background_terminal_max_timeout must be an unsigned 64-bit integer")
        numeric_values = {
            **(
                {"command_timeout_seconds": self.command_timeout_seconds}
                if self.command_timeout_seconds is not None
                else {}
            ),
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
            **(
                {"command_timeout_seconds": self.command_timeout_seconds}
                if self.command_timeout_seconds is not None
                else {}
            ),
            "command_yield_seconds": self.command_yield_seconds,
            **count_limits,
            "context_window_tokens": self.context_window_tokens,
            **(
                {"auto_compact_tokens": self.auto_compact_tokens}
                if self.auto_compact_tokens is not None
                else {}
            ),
            "tool_output_char_budget": self.tool_output_char_budget,
            "model_retry_base_seconds": self.model_retry_base_seconds,
            "model_response_char_limit": self.model_response_char_limit,
            "max_realtime_inputs": self.max_realtime_inputs,
            "memories_max_raw_for_consolidation": self.memories_max_raw_for_consolidation,
            "memories_max_thread_age_days": self.memories_max_thread_age_days,
            "memories_max_threads_per_startup": self.memories_max_threads_per_startup,
            "memories_lease_seconds": self.memories_lease_seconds,
            "memories_retry_delay_seconds": self.memories_retry_delay_seconds,
            "memories_summary_token_limit": self.memories_summary_token_limit,
        }
        invalid = [name for name, value in positive_numbers.items() if value <= 0]
        if invalid:
            raise ValueError(f"Corki settings must be positive: {', '.join(invalid)}")
        if self.event_queue_size < 0:
            raise ValueError("runtime.event_queue_size must be zero or greater")
        if self.model_max_retries < 0:
            raise ValueError("provider.max_retries must be zero or greater")
        if self.model_request_max_retries < 0:
            raise ValueError("provider.request_max_retries must be zero or greater")
        object.__setattr__(self, "model_max_retries", min(self.model_max_retries, 100))
        object.__setattr__(
            self, "model_request_max_retries", min(self.model_request_max_retries, 100)
        )
        if not 1 <= self.memories_max_raw_for_consolidation <= 4_096:
            raise ValueError("memories.max_raw_for_consolidation must be between 1 and 4096")
        if not 1 <= self.memories_max_threads_per_startup <= 128:
            raise ValueError("memories.max_threads_per_startup must be between 1 and 128")
        if not 0 <= self.memories_max_unused_days <= 365:
            raise ValueError("memories.max_unused_days must be between 0 and 365")
        if self.memories_min_thread_idle_hours < 0:
            raise ValueError("memories.min_thread_idle_hours must be zero or greater")
        ContextLimits(
            self.context_window_tokens,
            self.auto_compact_tokens,
            self.effective_context_window_percent,
        )
        # Reject an unusable resolved main window before composing resources.
        _ = self.main_context_limits
        if not isinstance(self.api_mode, str) or self.api_mode not in {
            "chat_completions",
            "responses",
        }:
            raise ValueError(f"unsupported provider api_mode: {self.api_mode}")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("agent.model must be a non-empty string")
        if not isinstance(self.api_base, str) or (
            self.api_base and urlparse(self.api_base).scheme not in {"http", "https"}
        ):
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
        if self.provider_id is not None and (
            not isinstance(self.provider_id, str) or not self.provider_id.strip()
        ):
            raise ValueError("provider.id must be a non-empty string")
        if self.api_key is not None and not isinstance(self.api_key, str):
            raise ValueError("provider.api_key must be a string")
        if type(self.plugins_enabled) is not bool:
            raise ValueError("features.plugins must be a boolean")
        if not isinstance(self.skills_enabled, bool):
            raise ValueError("skills.enabled must be true or false")
        if type(self.skill_mcp_dependency_install) is not bool:
            raise ValueError("features.skill_mcp_dependency_install must be a boolean")
        if not isinstance(self.include_environment_context, bool):
            raise ValueError("agent.include_environment_context must be true or false")
        if not isinstance(self.include_permissions_instructions, bool):
            raise ValueError("include_permissions_instructions must be true or false")
        if not isinstance(self.allow_login_shell, bool):
            raise ValueError("tools.allow_login_shell must be true or false")
        if self.compact_prompt is not None and not isinstance(self.compact_prompt, str):
            raise ValueError("compact_prompt must be a string")
        if not isinstance(self.skills_include_instructions, bool):
            raise ValueError("skills.include_instructions must be true or false")
        if self.skills_max_context_tokens is not None and (
            not isinstance(self.skills_max_context_tokens, int)
            or isinstance(self.skills_max_context_tokens, bool)
            or self.skills_max_context_tokens <= 0
        ):
            raise ValueError("skills.max_context_tokens must be a positive integer")
        if not isinstance(self.realtime_enabled, bool):
            raise ValueError("realtime.enabled must be true or false")
        if not isinstance(self.notifications, bool):
            if not isinstance(self.notifications, (list, tuple)) or any(
                not isinstance(kind, str) for kind in self.notifications
            ):
                raise ValueError("tui.notifications must be a boolean or event-name array")
            object.__setattr__(self, "notifications", tuple(self.notifications))
        if self.notification_method not in ("auto", "osc9", "bel"):
            raise ValueError("tui.notification_method must be auto, osc9 or bel")
        if self.notification_condition not in ("always", "unfocused"):
            raise ValueError("tui.notification_condition must be always or unfocused")
        if not isinstance(self.model_unbounded_connection_retries, bool):
            raise ValueError("provider.unbounded_connection_retries must be true or false")
        memory_booleans = {
            "memories.enabled": self.memories_enabled,
            "memories.generate": self.memories_generate,
            "memories.background_enabled": self.memories_background_enabled,
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
            ("provider.memory_extraction_model", self.provider_memory_extraction_model),
            ("provider.memory_consolidation_model", self.provider_memory_consolidation_model),
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
