"""Optional agents/openai.yaml policy; malformed metadata fails open like Codex."""

import logging
from pathlib import Path

import yaml

from corki.skills import yaml_fields as yf

_LOG = logging.getLogger(__name__)


def metadata_path(skill_path: Path) -> Path:
    return skill_path.resolve().parent / "agents" / "openai.yaml"


def _implicit_policy(document) -> bool:
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
    return True if allowed is None else allowed


def allows_implicit_invocation(skill_path: Path) -> bool:
    path = metadata_path(skill_path)
    try:
        if not path.is_file():
            return True
        return _implicit_policy(yf.parse(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        _LOG.warning("ignoring optional skill metadata at %s: %s", path, exc)
        return True
