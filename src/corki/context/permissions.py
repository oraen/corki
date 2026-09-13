"""Effective execution authority as bounded, append-only model context."""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from xml.etree import ElementTree

from corki.config.permissions import ExecutionPermissions
from corki.context.tokens import estimate_text_tokens
from corki.execution.owned_process import run_owned
from corki.execution.response import decode_helper_response
from corki.execution.rules import ExecutionRuleUpdates
from corki.prompting import PromptContribution, PromptRole, PromptSlot, PromptStore
from corki.protocol.items import ContextItem
from corki.protocol.permission_messages import ModelPermissionMessages

KEY = "permissions"
COMPACT_KEY = "approved_command_prefixes"
KIND = "permissions.instructions"
NOTICE_KIND = "permissions.approved_command_prefix_saved"
_PROMPTS = PromptStore()
_MAX_BYTES = 30_000


@dataclass(frozen=True, slots=True)
class PermissionsSnapshot:
    text: str
    without_prefixes: str
    prefixes: tuple[tuple[str, ...], ...]
    warnings: tuple[str, ...] = ()
    filesystem: str | None = None
    network: str | None = None

    def contribution(self, *, compact: bool) -> PromptContribution:
        return PromptContribution(
            key=COMPACT_KEY if compact else KEY,
            content_kind=NOTICE_KIND if compact else KIND,
            template_name=None if compact else "permissions/fragment",
            role=PromptRole.DEVELOPER,
            slot=PromptSlot.PERMISSIONS,
            variables={} if compact else {"text": self.text},
            snapshot_state=json.dumps(
                {
                    "version": 1,
                    "base": hashlib.sha256(self.without_prefixes.encode()).hexdigest(),
                    "prefixes": self.prefixes,
                },
                sort_keys=True,
            ),
            warnings=self.warnings,
        )


class PermissionContext:
    """One cached native view; live rule ownership remains with the process manager."""

    def __init__(self, rules: ExecutionRuleUpdates | None = None):
        self.rules = rules
        self._key = None
        self._snapshot: PermissionsSnapshot | None = None

    async def snapshot(
        self,
        permissions: ExecutionPermissions | None,
        cwd: Path,
        *,
        honor_allow_rules: bool,
        messages: ModelPermissionMessages | None = None,
    ) -> PermissionsSnapshot:
        prefixes = self.rules.prefixes if self.rules is not None else ()
        key = (permissions, cwd, honor_allow_rules, prefixes, messages)
        if key == self._key and self._snapshot is not None:
            return self._snapshot
        if permissions is None:
            # Legacy Corki has no sandbox or approval mechanism. This is the
            # fixed native Disabled/Never text, not an inferred managed profile.
            text = _legacy_text(messages)
            snapshot = PermissionsSnapshot(
                text,
                text,
                (),
                filesystem=(
                    '<filesystem><permission_profile type="disabled">'
                    '<file_system type="unrestricted" /></permission_profile></filesystem>'
                ),
            )
        else:
            loaded = permissions.exec_policy_snapshot
            sources = (
                loaded.sources
                if loaded is not None and loaded.declared_sources == permissions.exec_policy_sources
                else permissions.exec_policy_sources
            )
            request = {
                "corki_metadata": True,
                "profile": json.loads(permissions.profile_json),
                "cwd": str(cwd),
                "policy_cwd": str(permissions.policy_cwd),
                "workspace_roots": [str(root) for root in permissions.profile_workspace_roots],
                "approval_policy": json.loads(permissions.approval_policy_json),
                "sources": [asdict(source) for source in sources],
                "requirements": [
                    {
                        "source": layer.source,
                        "base_dir": str(layer.base_dir) if layer.base_dir is not None else None,
                        "value": json.loads(layer.value_json),
                    }
                    for layer in permissions.requirements
                ],
                "options": {
                    "honor_allow_prefix_rules": honor_allow_rules,
                    "approved_prefixes": prefixes,
                },
            }
            if messages is not None:
                request.update(messages.native_fields())
            output = await run_owned(
                [str(permissions.compiler)],
                (json.dumps({"permission_context": request}) + "\n").encode(),
                cwd=permissions.policy_cwd,
                output_limit=4_000_000,
            )
            value = decode_helper_response(
                output, expected=dict, error_prefix="sandbox permission context failed"
            )
            if not isinstance(value, dict) or value.get("permission_context") is not True:
                raise ValueError("sandbox compiler did not acknowledge permission context support")
            if messages is not None and value.get("model_permission_messages") is not True:
                raise ValueError("sandbox compiler did not acknowledge model permission messages")
            texts = [value.get(name) for name in ("text", "without_prefixes")]
            rules, warnings = value.get("prefixes"), value.get("warnings")
            if (
                any(
                    not isinstance(text, str)
                    or len(text.encode()) > _MAX_BYTES
                    or estimate_text_tokens(text) > 10_000
                    for text in texts
                )
                or not isinstance(rules, list)
                or any(
                    not isinstance(rule, list)
                    or not rule
                    or any(not isinstance(word, str) for word in rule)
                    for rule in rules
                )
                or not isinstance(warnings, list)
                or any(not isinstance(warning, str) for warning in warnings)
            ):
                raise ValueError("invalid or oversized permission context from sandbox compiler")
            if len(json.dumps(rules).encode()) > _MAX_BYTES:
                raise ValueError("approved prefix context exceeds its bounded allocation")
            environment = value.get("environment")
            if (
                not isinstance(environment, dict)
                or type(environment.get("version")) is not int
                or environment["version"] != 1
                or not isinstance(environment.get("filesystem"), str)
            ):
                raise ValueError(
                    "sandbox compiler lacks effective environment metadata; rebuild required"
                )
            fragments = []
            for name in ("filesystem", "network"):
                fragment = environment.get(name)
                if fragment is not None:
                    if (
                        not isinstance(fragment, str)
                        or len(fragment.encode()) > _MAX_BYTES
                        or estimate_text_tokens(fragment) > 10_000
                        or "<!" in fragment
                    ):
                        raise ValueError("invalid or oversized effective environment metadata")
                    try:
                        root = ElementTree.fromstring(fragment)
                    except ElementTree.ParseError as exc:
                        raise ValueError("invalid effective environment XML") from exc
                    if root.tag != name:
                        raise ValueError("invalid effective environment section")
                fragments.append(fragment)
            combined = "\n".join(fragment for fragment in fragments if fragment is not None)
            if len(combined.encode()) > _MAX_BYTES or estimate_text_tokens(combined) > 9_000:
                raise ValueError("effective environment context exceeds its bounded allocation")
            snapshot = PermissionsSnapshot(
                texts[0],
                texts[1],
                tuple(sorted({tuple(rule) for rule in rules})),
                tuple(warnings),
                *fragments,
            )
        if len(snapshot.text.encode()) > _MAX_BYTES or estimate_text_tokens(snapshot.text) > 10_000:
            raise ValueError("model permission context exceeds its bounded allocation")
        self._key, self._snapshot = key, snapshot
        return snapshot


def _legacy_text(messages: ModelPermissionMessages | None) -> str:
    if messages is None:
        return _PROMPTS.render("permissions/legacy_unrestricted").rstrip()
    sandbox = (
        _PROMPTS.render("permissions/sandbox_unrestricted").rstrip()
        if messages.danger_full_access is None
        else messages.danger_full_access.replace("{{ network_access }}", "enabled")
    )
    approval = (
        _PROMPTS.render("permissions/approval_never") if messages.never is None else messages.never
    )
    # Exact native append_section whitespace; empty approval still contributes
    # the trailing newline, while empty sandbox omits that section entirely.
    body = ""
    for section in ([sandbox] if sandbox else []) + [approval]:
        if not body.endswith("\n"):
            body += "\n"
        body += section
    if not body.endswith("\n"):
        body += "\n"
    return "<permissions instructions>" + body + "</permissions instructions>"


def _decode(item: ContextItem | None) -> dict | None:
    try:
        value = json.loads(item.snapshot_state) if item is not None else None
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 1
        or not isinstance(value.get("base"), str)
        or not isinstance(value.get("prefixes"), list)
        or any(
            not isinstance(rule, list) or any(not isinstance(word, str) for word in rule)
            for rule in value["prefixes"]
        )
    ):
        return None
    return value


def render_update(item: ContextItem, previous: ContextItem | None) -> ContextItem:
    current, old = _decode(item), _decode(previous)
    compact = item.key == COMPACT_KEY
    if current is None:
        # Disabling a native section resets its baseline silently.
        return replace(
            item,
            snapshot_content=item.content,
            snapshot_state=item.snapshot_state if item.content else '{"version":1,"disabled":true}',
        )
    content, kind = item.content, item.content_kind
    if compact:
        content = ""
    if old is not None:
        before = {tuple(rule) for rule in old["prefixes"]}
        after = {tuple(rule) for rule in current["prefixes"]}
        if compact or (old["base"] == current["base"] and before <= after):
            added = after - before
            content = (
                "Approved command prefix saved:\n"
                + "\n".join("- " + json.dumps(rule, ensure_ascii=False) for rule in sorted(added))
                if added
                else ""
            )
            kind = NOTICE_KIND
    return replace(item, content=content, content_kind=kind, snapshot_content=item.content)
