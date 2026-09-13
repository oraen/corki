"""Host-owned session provenance, distinct from execution and historical identity."""

import json
from dataclasses import asdict, dataclass
from enum import StrEnum

from corki.protocol.ids import ThreadId

_WHITESPACE = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


class SessionSourceKind(StrEnum):
    CLI = "cli"
    VSCODE = "vscode"
    EXEC = "exec"
    MCP = "mcp"
    CUSTOM = "custom"
    INTERNAL = "internal"
    SUBAGENT = "subagent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ThreadSpawnSource:
    """Provenance supplied by a spawning host, not inferred from a display name."""

    parent_thread_id: ThreadId
    depth: int
    agent_path: str | None = None
    agent_nickname: str | None = None
    agent_role: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.parent_thread_id, str) or not self.parent_thread_id:
            raise ValueError("spawn source requires a parent thread id")
        if type(self.depth) is not int or not -(2**31) <= self.depth < 2**31:
            raise ValueError("spawn source depth must be an i32")
        if any(
            value is not None and not isinstance(value, str)
            for value in (self.agent_path, self.agent_nickname, self.agent_role)
        ):
            raise ValueError("spawn source labels must be strings or None")


@dataclass(frozen=True, slots=True)
class SubAgentSource:
    """Typed subagent variant; only thread_spawn carries parent metadata."""

    variant: str
    value: str | ThreadSpawnSource | None = None

    def __post_init__(self) -> None:
        if self.variant == "thread_spawn":
            valid = isinstance(self.value, ThreadSpawnSource)
        elif self.variant == "other":
            valid = isinstance(self.value, str)
        else:
            valid = (
                self.variant in {"review", "compact", "memory_consolidation"} and self.value is None
            )
        if not valid:
            raise ValueError("invalid subagent source variant or payload")


@dataclass(frozen=True, slots=True)
class SessionSource:
    """Canonical source tag; Custom prefix lookalikes never acquire non-root authority."""

    kind: SessionSourceKind = SessionSourceKind.VSCODE
    value: str | SubAgentSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SessionSourceKind):
            raise ValueError("session source kind must be typed")
        if self.kind == SessionSourceKind.CUSTOM:
            valid = isinstance(self.value, str)
        elif self.kind == SessionSourceKind.INTERNAL:
            valid = isinstance(self.value, str) and self.value in {
                "memory_consolidation",
                "guardian",
            }
        elif self.kind == SessionSourceKind.SUBAGENT:
            valid = isinstance(self.value, SubAgentSource)
        else:
            valid = self.value is None
        if not valid:
            raise ValueError("invalid session source payload")

    @classmethod
    def from_startup_arg(cls, value: str) -> "SessionSource":
        """Normalize external startup labels with Rust trim and ASCII lowercase."""
        if not isinstance(value, str) or not (value := value.strip(_WHITESPACE)):
            raise ValueError("session source must not be empty")
        value = value.translate(
            str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
        )
        if value in {"appserver", "app-server", "app_server"}:
            value = "mcp"
        if value in {"cli", "vscode", "exec", "mcp", "unknown"}:
            return cls(SessionSourceKind(value))
        return cls(SessionSourceKind.CUSTOM, value)

    @classmethod
    def internal(cls, variant: str) -> "SessionSource":
        """Identify an internal host without interpreting external startup strings."""
        return cls(SessionSourceKind.INTERNAL, variant)

    @classmethod
    def subagent(cls, source: SubAgentSource) -> "SessionSource":
        """Identify a subagent host with its validated provenance."""
        return cls(SessionSourceKind.SUBAGENT, source)

    @property
    def is_non_root_agent(self) -> bool:
        return self.kind in {SessionSourceKind.INTERNAL, SessionSourceKind.SUBAGENT}

    @property
    def is_basic_guardian(self) -> bool:
        """Recognize native internal and legacy guardian hosts, not custom labels."""
        return (self.kind == SessionSourceKind.INTERNAL and self.value == "guardian") or (
            self.kind == SessionSourceKind.SUBAGENT
            and self.value == SubAgentSource("other", "guardian")
        )

    @property
    def storage_value(self) -> str:
        """Match native enum_to_string, not SessionSource's display representation."""
        if self.value is None:
            return self.kind.value
        value = self.value
        if isinstance(value, SubAgentSource):
            payload = value.value
            if isinstance(payload, ThreadSpawnSource):
                payload = asdict(payload)
            value = value.variant if payload is None else {value.variant: payload}
        return json.dumps(
            {self.kind.value: value}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )

    @classmethod
    def from_storage(cls, value: str) -> "SessionSource":
        """Read legacy wire tags; unrecognized or malformed metadata is Unknown."""
        try:
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = value
            if isinstance(decoded, str):
                if decoded in {"cli", "vscode", "exec", "mcp", "unknown"}:
                    return cls(SessionSourceKind(decoded))
                return cls(SessionSourceKind.UNKNOWN)
            if not isinstance(decoded, dict) or len(decoded) != 1:
                raise ValueError("invalid source tag")
            kind, payload = next(iter(decoded.items()))
            if kind == "subagent":
                if isinstance(payload, str):
                    return cls.subagent(SubAgentSource(payload))
                if not isinstance(payload, dict) or len(payload) != 1:
                    raise ValueError("invalid subagent tag")
                variant, data = next(iter(payload.items()))
                if variant == "thread_spawn":
                    if not isinstance(data, dict):
                        raise ValueError("invalid spawn source")
                    data = ThreadSpawnSource(
                        data["parent_thread_id"],
                        data["depth"],
                        data.get("agent_path"),
                        data.get("agent_nickname"),
                        data.get("agent_role", data.get("agent_type")),
                    )
                return cls.subagent(SubAgentSource(variant, data))
            return cls(SessionSourceKind(kind), payload)
        except (TypeError, ValueError, KeyError):
            return cls(SessionSourceKind.UNKNOWN)


DEFAULT_SESSION_SOURCE = SessionSource()
