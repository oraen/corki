"""Optional agents/openai.yaml policy; malformed metadata fails open like Codex."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.skills import yaml_fields as yf
from corki.skills.models import SkillToolDependency

_LOG = logging.getLogger(__name__)
_WHITESPACE = re.compile(f"[{re.escape(RUST_WHITESPACE)}]+")


def _resolve_dependency_string(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    value = " ".join(part for part in _WHITESPACE.split(value) if part)
    limit = 64 if field in ("type", "transport") else 1024
    if not value or len(value) > limit:
        _LOG.warning("ignoring dependencies.tools.%s: empty or exceeds %s characters", field, limit)
        return None
    return value


def metadata_path(skill_path: Path) -> Path:
    return skill_path.resolve().parent / "agents" / "openai.yaml"


@dataclass(frozen=True, slots=True)
class SkillPolicyMetadata:
    allow_implicit_invocation: bool = True
    dependencies: tuple[SkillToolDependency, ...] = ()


def _parse_metadata(document) -> SkillPolicyMetadata:
    document = yf.fields(document, ("interface", "dependencies", "policy"))
    interface = yf.fields(
        document.get("interface"),
        (
            "display_name",
            "short_description",
            "icon_small",
            "icon_large",
            "brand_color",
            "default_prompt",
        ),
        optional=True,
    )
    for node in interface.values():
        yf.string(node)
    dependencies = yf.fields(document.get("dependencies"), ("tools",), optional=True)
    tools = yf.sequence(dependencies["tools"]) if "tools" in dependencies else ()
    parsed_tools = []
    for tool in tools:
        fields = yf.fields(
            tool, ("type", "value", "description", "transport", "command", "url", "oauth")
        )
        for key, node in fields.items():
            if key != "oauth":
                yf.string(node)
        oauth = yf.fields(fields.get("oauth"), ("callbackPort", "callback_port"), optional=True)
        if len(oauth) > 1:
            raise ValueError("duplicate oauth callback port")
        for node in oauth.values():
            yf.u16(node)
        values = {
            key: _resolve_dependency_string(yf.string(fields.get(key)), key)
            for key in ("type", "value", "description", "transport", "command", "url")
        }
        if values["type"] is None or values["value"] is None:
            # Well-typed but incomplete dependencies do not discard siblings or policy.
            continue
        parsed_tools.append(
            SkillToolDependency(
                **values, oauth_callback_port=next((yf.u16(node) for node in oauth.values()), None)
            )
        )
    policy = yf.fields(
        document.get("policy"), ("allow_implicit_invocation", "products"), optional=True
    )
    allowed = yf.boolean(policy.get("allow_implicit_invocation"))
    products = yf.sequence(policy["products"]) if "products" in policy else ()
    if any(
        yf.string(product) not in ("codex", "CODEX", "chatgpt", "CHATGPT", "atlas", "ATLAS")
        for product in products
    ):
        raise ValueError("policy.products contains an invalid product")
    # Codex parses products but does not enforce product gating in selection/injection.
    return SkillPolicyMetadata(True if allowed is None else allowed, tuple(parsed_tools))


def _implicit_policy(document) -> bool:
    return _parse_metadata(document).allow_implicit_invocation


def load_metadata(skill_path: Path) -> SkillPolicyMetadata:
    path = metadata_path(skill_path)
    try:
        if not path.is_file():
            return SkillPolicyMetadata()
        return _parse_metadata(yf.parse(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        _LOG.warning("ignoring optional skill metadata at %s: %s", path, exc)
        return SkillPolicyMetadata()


def allows_implicit_invocation(skill_path: Path) -> bool:
    return load_metadata(skill_path).allow_implicit_invocation
