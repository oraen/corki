"""Equality projection of native model-owned activation restrictions.

Policy hashes preserve exact text identity without copying review prompts into
checkpoints. They are NOT an approval decision or a Guardian implementation.
Defaults refer to Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc assets.
"""

import hashlib
import json
from dataclasses import dataclass

_POLICY = "e6b0cf0a2e1c4cabc0a37ac2a0bc424ddd7c89e85d049e32d281a8db6e8d3ce6"
_NODE_POLICY = "cbeac65723cf1476dce0680b5b27484640472ef413436074ca8d8cefbf94c787"
_TEMPLATE = "e6cde2ae2cee133e393e2ff2e6be9c689c47dc9319bd3c1ce3889ef2b87ebfc1"
_RUST_WHITESPACE = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
_SCOPES = ("computer_use", "shell", "code_mode", "file_changes", "mcp", "network", "permissions")
_V2 = (
    "classifier_instructions",
    "review_threshold_basis_points",
    "max_tool_call_lag",
    "reasoning_effort",
    "transcript",
    "max_action_tokens",
    "max_classifier_instruction_tokens",
    "reuse_parent_compaction",
    "max_parent_compaction_tokens",
)
_TRANSCRIPT = (
    "sources",
    "include_images",
    "max_message_entry_tokens",
    "max_tool_entry_tokens",
    "max_message_transcript_tokens",
    "max_tool_transcript_tokens",
    "max_recent_non_user_entries",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_digest(value: object) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _table(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("model authority records must be tables")
    return value


@dataclass(frozen=True, slots=True)
class ModelAuthority:
    """Finite immutable guard inputs; None on ModelInfo means unknown provenance."""

    cyber: bool = False
    computer_use_review_required: bool = False
    node_repl_disabled: bool = False
    guardian: str | None = None
    reviewer: str | None = None
    guardian_v2: str = _json_digest({})
    node_policy: str = _NODE_POLICY
    policy: str = _POLICY
    policy_template: str = _TEMPLATE

    def __post_init__(self) -> None:
        for key in ("cyber", "computer_use_review_required", "node_repl_disabled"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"model authority {key} must be a bool")
        if self.reviewer is not None and not isinstance(self.reviewer, str):
            raise ValueError("model authority reviewer must be a string or None")
        for key in ("guardian", "guardian_v2", "node_policy", "policy", "policy_template"):
            value = getattr(self, key)
            if key == "guardian" and value is None:
                continue
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"model authority {key} must be a SHA256 digest")

    @classmethod
    def from_catalog(cls, fields: dict) -> "ModelAuthority":
        """Project relevant native catalog fields, including absent/empty defaults."""
        guardian = fields.get("guardian")
        review = fields.get("node_repl_auto_review_required", False)
        if type(review) is not bool:
            raise ValueError("node_repl_auto_review_required must be a bool")
        if guardian is not None:
            if not isinstance(guardian, dict):
                raise ValueError("model guardian must be a table")
            if any(
                guardian.get(key) is not None and not isinstance(guardian[key], str)
                for key in _SCOPES
            ):
                raise ValueError("guardian review modes must be strings")
            modes = {
                key: (mode if mode in ("disabled", "synchronous", "adaptive") else "unknown")
                for key in _SCOPES
                if (mode := guardian.get(key)) is not None
            }
            review = modes.get("computer_use", "disabled") != "disabled"
            guardian = _json_digest(modes)
        messages = _table(fields.get("model_messages"))
        parent = _table(messages.get("auto_review"))
        for key in ("policy", "node_repl_policy", "policy_template"):
            if parent.get(key) is not None and not isinstance(parent[key], str):
                raise ValueError(f"model authority {key} must be a string")
        raw_v2 = _table(messages.get("guardian_v2"))
        v2 = {key: raw_v2[key] for key in _V2 if raw_v2.get(key) is not None}
        if "transcript" in v2:
            raw_transcript = _table(v2["transcript"])
            transcript = {
                key: raw_transcript[key]
                for key in _TRANSCRIPT
                if raw_transcript.get(key) is not None
            }
            if transcript:
                v2["transcript"] = transcript
            else:
                del v2["transcript"]
        for record in (v2, v2.get("transcript", {})):
            for key, value in record.items():
                if key == "transcript":
                    continue
                if key in ("include_images", "reuse_parent_compaction"):
                    valid = type(value) is bool
                elif key in ("classifier_instructions", "reasoning_effort"):
                    valid = isinstance(value, str)
                elif key == "sources":
                    valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
                else:
                    maximum = 65535 if key == "review_threshold_basis_points" else 2**64 - 1
                    valid = type(value) is int and 0 <= value <= maximum
                if not valid:
                    raise ValueError(f"invalid Guardian V2 catalog field: {key}")
        return cls(
            cyber=fields.get("model_specialty") == "cyber",
            computer_use_review_required=review,
            node_repl_disabled=fields.get("node_repl_disabled", False),
            guardian=guardian,
            reviewer=fields.get("auto_review_model_override"),
            guardian_v2=_json_digest(v2),
            node_policy=_NODE_POLICY
            if parent.get("node_repl_policy") is None
            else _digest(parent["node_repl_policy"]),
            policy=_POLICY if parent.get("policy") is None else _digest(parent["policy"]),
            policy_template=_TEMPLATE
            if parent.get("policy_template") is None
            else _digest(parent["policy_template"].rstrip(_RUST_WHITESPACE)),
        )
