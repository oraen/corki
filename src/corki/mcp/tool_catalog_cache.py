"""Shared definition snapshots, never shared transport or approval authority."""

import json
import os
import weakref
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import monotonic

from corki.config import MCPEnvVar, MCPServerSettings
from corki.mcp.client import CLIENT_CAPABILITIES, PROTOCOL_VERSION
from corki.mcp.runtime_environment import MCPHTTPEnvironment


@dataclass(frozen=True)
class CatalogSnapshot:
    definitions: tuple[dict, ...]
    instructions: str | None


class CatalogCacheContext:
    """An entry remains owned by its captures even after LRU eviction."""

    def __init__(self, clock: Callable[[], float], ttl: float):
        self._clock, self._ttl = clock, ttl
        self._lock = RLock()
        self._snapshot: CatalogSnapshot | None = None
        self._published_at = 0.0
        self._generation = 0
        self._accepted = 0
        self._disabled = False
        self._deadline: tuple[float, float] | None = None

    def current_tools_or(self, fallback: CatalogSnapshot | None = None) -> CatalogSnapshot | None:
        with self._lock:
            if self._disabled:
                return None
            if self._snapshot is not None and self._clock() - self._published_at <= self._ttl:
                return deepcopy(self._snapshot)
            return deepcopy(fallback)

    def optional_startup_deadline(self, default_deadline: float, grace: float) -> float:
        with self._lock:
            if self._disabled or self.current_tools_or() is not None:
                return default_deadline
            if self._deadline is None or self._deadline[0] != grace:
                self._deadline = grace, default_deadline
            return self._deadline[1]

    def begin_fetch(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def disable(self) -> None:
        with self._lock:
            self._disabled = True
            self._snapshot = None

    def publish_if_newest(self, ticket: int, snapshot: CatalogSnapshot) -> None:
        with self._lock:
            if self._disabled or ticket <= self._accepted:
                return
            definitions = deepcopy(snapshot.definitions)
            for definition in definitions:
                definition.pop("annotations", None)
            self._snapshot = CatalogSnapshot(definitions, snapshot.instructions)
            self._published_at = self._clock()
            self._accepted = ticket
            self._deadline = None


class MCPToolCatalogCache:
    """Host/process-scoped bounded LRU; callers may inject a separate host cache."""

    def __init__(self, *, capacity: int = 32, ttl: float = 1800, clock=monotonic):
        if capacity < 1 or ttl < 0:
            raise ValueError("catalog cache requires positive capacity and nonnegative TTL")
        self._capacity, self._ttl, self._clock = capacity, ttl, clock
        self._lock = RLock()
        self._entries: OrderedDict[tuple, CatalogCacheContext] = OrderedDict()

    def context(
        self,
        settings: MCPServerSettings,
        environment: MCPHTTPEnvironment | None = None,
        *,
        agent_plugin: bool = False,
    ) -> CatalogCacheContext | None:
        if settings.transport == "http":
            if settings.http_headers_helper is not None:
                return None
            names = {value for _, value in settings.env_http_headers or ()}
            if settings.bearer_token_env_var is not None:
                names.add(settings.bearer_token_env_var)
            inputs = (
                settings.url,
                sorted(settings.headers),
                None if settings.http_headers is None else sorted(settings.http_headers),
                None if settings.env_http_headers is None else sorted(settings.env_http_headers),
                settings.bearer_token_env_var,
                agent_plugin,
                PROTOCOL_VERSION,
            )
        else:
            if any(
                isinstance(ref, MCPEnvVar) and ref.source == "remote" for ref in settings.env_vars
            ):
                return None
            names = {ref.name if isinstance(ref, MCPEnvVar) else ref for ref in settings.env_vars}
            references = tuple(
                (ref.name, ref.source) if isinstance(ref, MCPEnvVar) else ref
                for ref in settings.env_vars
            )
            inputs = (
                settings.command,
                settings.args,
                sorted(settings.env),
                references,
                str(settings.cwd) if settings.cwd is not None else None,
                str(Path.cwd()) if settings.cwd is None else None,
            )
        # Surrogate-escaped OS values survive JSON encoding; missing differs from empty.
        fingerprint = sha256(
            json.dumps(
                (
                    settings.transport,
                    settings.environment_id,
                    CLIENT_CAPABILITIES,
                    inputs,
                    [(name, os.environ.get(name)) for name in sorted(names)],
                ),
                ensure_ascii=True,
                sort_keys=True,
            ).encode()
        ).digest()
        identity = (settings.name, fingerprint, weakref.ref(environment) if environment else None)
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                entry = CatalogCacheContext(self._clock, self._ttl)
                self._entries[identity] = entry
            self._entries.move_to_end(identity)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
            return entry


SHARED_TOOL_CATALOG_CACHE = MCPToolCatalogCache()
