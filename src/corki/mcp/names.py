"""Whole-catalog MCP callable identities; raw routing names never change.

Adapted from pinned Codex codex-mcp/src/tools.rs. Compatibility wire aliases
are owned separately by protocol.tool_names, not by the remote MCP transport.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from hashlib import sha1


@dataclass(frozen=True, slots=True)
class MCPToolIdentity:
    """Unnormalized model identity, kept separate from the original RPC route."""

    server: str
    remote: str
    namespace: str
    leaf: str
    connector_id: str | None = None

    @property
    def namespace_identity(self) -> str:
        return f"{self.server}\0{self.namespace}\0{self.connector_id or ''}"

    @property
    def raw_identity(self) -> str:
        return f"{self.namespace_identity}\0{self.leaf}\0{self.remote}"


@dataclass(frozen=True, slots=True)
class MCPToolName:
    server: str
    remote: str
    namespace: str
    leaf: str
    identity: MCPToolIdentity | None = None

    @property
    def canonical(self) -> str:
        # "functions" is the provider's default namespace, not a second one.
        return self.leaf if self.namespace == "functions" else f"{self.namespace}::{self.leaf}"

    @property
    def flat(self) -> str:
        # Codex's search/legacy flattening concatenates the canonical parts;
        # its 128-byte allocator independently reserves two delimiter bytes.
        return self.leaf if self.namespace == "functions" else self.namespace + self.leaf

    @property
    def namespace_identity(self) -> str:
        if self.identity is not None:
            return self.identity.namespace_identity
        return f"{self.server}\0{self.server}\0"

    @property
    def raw_identity(self) -> str:
        if self.identity is not None:
            return self.identity.raw_identity
        return f"{self.namespace_identity}\0{self.remote}\0{self.remote}"


def sanitize(name: str) -> str:
    return "".join(c if c.isascii() and (c.isalnum() or c == "_") else "_" for c in name) or "_"


def _suffix(identity: str) -> str:
    return "_" + sha1(identity.encode(), usedforsecurity=False).hexdigest()[:12]


def normalize_tool_names(
    tools: Iterable[tuple[str, str] | MCPToolIdentity],
    *,
    prefix: bool = True,
    non_prefixed_servers: tuple[str, ...] = (),
) -> tuple[MCPToolName, ...]:
    candidates: list[MCPToolName] = []
    seen: set[str] = set()
    for tool in tools:
        identity = tool if isinstance(tool, MCPToolIdentity) else None
        server, remote = (identity.server, identity.remote) if identity is not None else tool
        namespace = sanitize(identity.namespace if identity is not None else server)
        if prefix and server not in non_prefixed_servers and not namespace.startswith("mcp__"):
            namespace = "mcp__" + namespace
        candidate = MCPToolName(
            server,
            remote,
            namespace,
            sanitize(identity.leaf if identity is not None else remote),
            identity,
        )
        if candidate.raw_identity not in seen:
            candidates.append(candidate)
            seen.add(candidate.raw_identity)

    namespaces: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        namespaces[candidate.namespace].add(candidate.namespace_identity)
    candidates = [
        replace(
            candidate,
            namespace=(
                candidate.namespace[:-2] + _suffix(candidate.namespace_identity) + "__"
                if candidate.namespace.endswith("__")
                else candidate.namespace + _suffix(candidate.namespace_identity)
            ),
        )
        if len(namespaces[candidate.namespace]) > 1
        else candidate
        for candidate in candidates
    ]
    leaves: dict[tuple[str, str], set[str]] = defaultdict(set)
    for candidate in candidates:
        leaves[(candidate.namespace, candidate.leaf)].add(candidate.raw_identity)
    candidates = [
        replace(candidate, leaf=candidate.leaf + _suffix(candidate.raw_identity))
        if len(leaves[(candidate.namespace, candidate.leaf)]) > 1
        else candidate
        for candidate in candidates
    ]

    used: set[str] = set()
    output: list[MCPToolName] = []
    for candidate in sorted(candidates, key=lambda candidate: candidate.raw_identity):
        namespace, leaf = candidate.namespace, candidate.leaf
        joined = namespace + leaf
        if len(joined) + 2 > 128 or joined in used:
            attempt = 0
            while True:
                identity = candidate.raw_identity + (f"\0{attempt}" if attempt else "")
                suffix = _suffix(identity)
                room = max(0, 128 - len(candidate.namespace) - 2)
                if room >= len(suffix):
                    namespace = candidate.namespace
                    leaf = candidate.leaf[: room - len(suffix)] + suffix
                else:
                    namespace = candidate.namespace[: 128 - len(suffix) - 2]
                    leaf = suffix
                joined = namespace + leaf
                if joined not in used:
                    break
                attempt += 1
        used.add(joined)
        output.append(replace(candidate, namespace=namespace, leaf=leaf))
    return tuple(output)
