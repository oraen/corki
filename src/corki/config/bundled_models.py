"""Window, output-policy and reasoning projection of the pinned model catalog.

Source: codex-rs/models-manager/models.json at
ddf04ad26789d040f9ef6a96736f76602e35a6cc. Missing effective percentages
deserialize as 95 upstream. These are reference defaults, not live capacity
claims; explicit catalogs are authoritative. See THIRD_PARTY_NOTICES.md.
"""

from corki.protocol.context import ModelContextInfo
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.permission_messages import ModelPermissionMessages
from corki.protocol.truncation import TruncationPolicy

_TOKEN_POLICY = TruncationPolicy("tokens", 10_000)
_FOUR = ("low", "medium", "high", "xhigh")
_FIVE = (*_FOUR, "max")
_SIX = (*_FIVE, "ultra")
_ASTRA_AUTO_REVIEW = (
    "\n`approvals_reviewer` is `auto_review`: Sandbox escalations with require_escalated "
    "will be reviewed for compliance with the policy.\n"
    "If a rejection happens, you can continue with a safer alternative, or carry out checks "
    "to prove that the action is authorized or low risk before trying again. Complete "
    "unaffected work without asking for confirmation. Report anything that remains blocked, "
    "clarify why it was blocked by auto-review, inform the user of the risk and ask for approval."
)


def _model(
    name: str,
    window: int,
    maximum: int,
    levels: tuple[str, ...],
    default: str,
    *,
    preferred: str | None = None,
    policy: TruncationPolicy = _TOKEN_POLICY,
    summary: str = "none",
    tiers: tuple[str, ...] = ("priority",),
    tool_mode: str | None = "code_mode_only",
) -> ModelContextInfo:
    return ModelContextInfo(
        name,
        window,
        maximum,
        truncation_policy=policy,
        supported_reasoning_levels=levels,
        default_reasoning_level=default,
        multi_agent_reasoning_effort=preferred,
        default_reasoning_summary=summary,
        service_tiers=tiers,
        supports_search_tool=True,
        tool_mode=tool_mode,
        permission_messages=(
            ModelPermissionMessages(on_request_auto_review=_ASTRA_AUTO_REVIEW)
            if name == "gpt-6-astra"
            else ModelPermissionMessages(
                danger_full_access="", workspace_write="", read_only="", never=""
            )
            if name == "codex-auto-review"
            else None
        ),
        activation_authority=ModelAuthority(
            cyber=name in ("gpt-daybreak-blue-latest", "gpt-daybreak-red-latest"),
            computer_use_review_required=name == "gpt-6-astra",
            guardian_v2="9f06d2aa96c7d5e2ce97013e08e22b11f32d43fda437695b1349feef20c3bf07"
            if name == "gpt-6-astra"
            else ModelAuthority().guardian_v2,
            node_policy="c7d95a841bc65105179ea90c81b13b17ff0c997fa85789768f2cf1988615ceee"
            if name in ("gpt-5.6-luna", "codex-auto-review")
            else ModelAuthority().node_policy,
        ),
    )


BUNDLED_MODEL_CONTEXTS = (
    _model("gpt-6-astra", 272_000, 872_000, _SIX, "low", preferred="xhigh"),
    _model("gpt-5.6-sol", 272_000, 872_000, _SIX, "low", tiers=("priority", "ultrafast")),
    _model("gpt-5.6-terra", 272_000, 872_000, _SIX, "medium"),
    _model("gpt-5.6-luna", 272_000, 872_000, _FIVE, "medium"),
    _model("gpt-daybreak-blue-latest", 272_000, 872_000, _SIX, "low", tiers=()),
    _model("gpt-daybreak-red-latest", 372_000, 372_000, _SIX, "medium", tiers=()),
    _model("gpt-5.5", 272_000, 272_000, _FOUR, "medium", tool_mode=None),
    _model("gpt-5.4", 272_000, 1_000_000, _FOUR, "medium", tool_mode=None),
    _model("gpt-5.4-mini", 272_000, 272_000, _FOUR, "medium", tiers=(), tool_mode=None),
    _model(
        "gpt-5.2",
        272_000,
        272_000,
        _FOUR,
        "medium",
        policy=TruncationPolicy(),
        summary="auto",
        tiers=(),
        tool_mode=None,
    ),
    _model("codex-auto-review", 272_000, 872_000, _FIVE, "medium"),
)
