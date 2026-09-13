"""Explicit token-budget preferences for Harness-owned ordinary summaries."""

from dataclasses import dataclass, fields

DEFAULT_REMINDER = (
    "Your context window is nearly exhausted (only {n_remaining} tokens remaining) and will be "
    "automatically summarized soon. After a successful summary, the model-visible history will "
    "be replaced; original history and notes remain available across windows."
)


@dataclass(frozen=True, slots=True)
class TokenBudgetConfig:
    reminder_threshold_tokens: int | None = None
    reminder_message_template: str = DEFAULT_REMINDER
    guidance_message: str | None = None
    auto_compact_fallback_prompt: str | None = None
    auto_compact_fallback_buffer_tokens: int | None = None
    use_history_notes_extension: bool = False

    def __post_init__(self):
        if not isinstance(self.use_history_notes_extension, bool):
            raise ValueError("use_history_notes_extension must be a boolean")
        for name in ("reminder_threshold_tokens", "auto_compact_fallback_buffer_tokens"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 2**63
            ):
                raise ValueError(f"token_budget.{name} must be a positive integer")
        for name in (
            "reminder_message_template",
            "guidance_message",
            "auto_compact_fallback_prompt",
        ):
            value = getattr(self, name)
            if value is None and name != "reminder_message_template":
                continue
            if not isinstance(value, str) or len(value.encode("utf-8")) > 2000:
                raise ValueError(f"token_budget.{name} must be text of at most 2000 UTF-8 bytes")
        if not self.reminder_message_template.strip():
            raise ValueError("token_budget.reminder_message_template must not be empty")
        if (
            self.auto_compact_fallback_prompt is not None
            and self.auto_compact_fallback_buffer_tokens is None
        ):
            raise ValueError("token_budget fallback prompt requires a positive buffer")

    @property
    def fallback_buffer_tokens(self) -> int:
        return (
            (self.auto_compact_fallback_buffer_tokens or 0)
            if self.auto_compact_fallback_prompt is not None
            else 0
        )


def parse_token_budget(value) -> tuple[bool, TokenBudgetConfig | None]:
    if isinstance(value, bool):
        return value, None
    if not isinstance(value, dict):
        raise ValueError("features.token_budget must be a boolean or table")
    options = dict(value)
    allowed = {field.name for field in fields(TokenBudgetConfig)} | {"enabled"}
    if options.keys() - allowed:
        raise ValueError("unknown features.token_budget setting")
    enabled = options.pop("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("features.token_budget.enabled must be a boolean")
    # Match TOML deserialization even for disabled features. Semantic limits
    # (positive/nonblank) are checked only when the feature is active.
    for name, option in options.items():
        if option is None:
            continue
        if name == "use_history_notes_extension":
            valid = isinstance(option, bool)
        elif name in ("reminder_threshold_tokens", "auto_compact_fallback_buffer_tokens"):
            valid = (
                isinstance(option, int)
                and not isinstance(option, bool)
                and -(2**63) <= option < 2**63
            )
        else:
            valid = isinstance(option, str)
        if not valid:
            raise ValueError(f"invalid features.token_budget.{name} type")
    if not enabled:
        return False, None
    if (
        "guidance_message" in options
        and isinstance(options["guidance_message"], str)
        and not options["guidance_message"].strip()
    ):
        options["guidance_message"] = None
    if "auto_compact_fallback_prompt" in options and isinstance(
        options["auto_compact_fallback_prompt"], str
    ):
        options["auto_compact_fallback_prompt"] = (
            options["auto_compact_fallback_prompt"].strip() or None
        )
    return True, TokenBudgetConfig(**options)
