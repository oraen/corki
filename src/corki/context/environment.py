"""Typed single-local-environment state, separate from model-visible deltas.

Unknown historical metadata is not parsed from prompt text. It causes a full
refresh; disabled sections leave a silent baseline reset in the durable journal.
"""

import json
from dataclasses import asdict, dataclass, replace

from corki.prompting import PromptContribution, PromptRole, PromptSlot, PromptStore
from corki.protocol.items import ContextItem

KEY = "environment.primary"
_TEMPLATE = "context/environment_fragment"
_PROMPTS = PromptStore()


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    cwd: str
    shell: str | None
    current_date: str | None
    timezone: str | None
    filesystem: str | None = None
    network: str | None = None

    def encode(self) -> str:
        return json.dumps({"version": 2, "environment": asdict(self)}, sort_keys=True)

    def contribution(self) -> PromptContribution:
        return PromptContribution(
            key=KEY,
            content_kind="environments.environment_context",
            template_name=_TEMPLATE,
            role=PromptRole.USER,
            slot=PromptSlot.ENVIRONMENT,
            variables={"body": self.body(include_environment=True)},
            snapshot_state=self.encode(),
        )

    def body(self, *, include_environment: bool) -> str:
        fields = asdict(self)
        if not include_environment:
            fields.pop("cwd")
            fields.pop("shell")
        filesystem = fields.pop("filesystem")
        network = fields.pop("network")
        return "".join(
            f"  <{name}>{_escape(value)}</{name}>\n"
            for name, value in fields.items()
            if value is not None
        ) + "".join(f"  {fragment}\n" for fragment in (network, filesystem) if fragment is not None)


def decode_snapshot(state: str | None) -> EnvironmentSnapshot | None:
    """Accept only this host's known typed snapshot, never arbitrary legacy XML."""
    try:
        value = json.loads(state) if state is not None else None
    except (ValueError, TypeError, RecursionError):
        return None
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] not in (1, 2)
    ):
        return None
    fields = value.get("environment")
    if not isinstance(fields, dict) or not isinstance(fields.get("cwd"), str):
        return None
    names = ("shell", "current_date", "timezone", "filesystem", "network")
    if any(fields.get(name) is not None and not isinstance(fields[name], str) for name in names):
        return None
    return EnvironmentSnapshot(fields["cwd"], *(fields.get(name) for name in names))


def render_update(item: ContextItem, previous: ContextItem | None) -> ContextItem:
    current = decode_snapshot(item.snapshot_state)
    if current is None:
        if item.content:
            # Preserve old/custom contributor payloads without inventing a typed diff.
            return item
        # The reference iterates current sections only when rendering. Disabling
        # this section silently clears the baseline; it does not revoke old text.
        return replace(item, snapshot_content="", snapshot_state='{"version":1,"environment":null}')
    old = decode_snapshot(previous.snapshot_state) if previous is not None else None
    if previous is None:
        return item
    environment_changed = (
        old is None
        or (
            previous.snapshot_state is not None
            and json.loads(previous.snapshot_state)["version"] == 1
        )
        or current.cwd != old.cwd
        or (current.shell is not None and old.shell is not None and current.shell != old.shell)
    )
    changed = (
        environment_changed
        or old is None
        or current.current_date != old.current_date
        or current.timezone != old.timezone
        or current.filesystem != old.filesystem
        or current.network != old.network
    )
    content = (
        _PROMPTS.render(_TEMPLATE, body=current.body(include_environment=environment_changed))
        if changed
        else ""
    )
    # Even a silent unknown↔known shell transition advances the durable baseline.
    return replace(item, content=content, snapshot_content=item.content)


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
