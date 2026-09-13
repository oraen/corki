"""Pinned regex-lite computation with no filesystem/network access in the guest."""

from functools import lru_cache
from importlib.resources import files

import wasmtime

_FUEL = 100_000_000
_MEMORY_BYTES = 256 * 1024 * 1024


@lru_cache(maxsize=1)
def _engine() -> tuple[wasmtime.Engine, wasmtime.Module]:
    config = wasmtime.Config()
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    binary = files("corki").joinpath("_native/mcp_regex.wasm").read_bytes()
    module = wasmtime.Module(engine, binary)
    if module.imports:
        raise ValueError("MCP regex module must not import host capabilities")
    return engine, module


def _call(operation: str, *values: str) -> int:
    try:
        engine, module = _engine()
        with wasmtime.Store(engine) as store:
            store.set_fuel(_FUEL)
            store.set_limits(memory_size=_MEMORY_BYTES, instances=1, memories=1)
            exports = wasmtime.Instance(store, module, []).exports(store)
            arguments = []
            for value in values:
                data = value.encode("utf-8")
                if len(data) > _MEMORY_BYTES:
                    raise ValueError("MCP regex input exceeds memory bound")
                pointer = exports["allocate"](store, len(data))
                exports["memory"].write(store, data, pointer)
                arguments.extend((pointer, len(data)))
            return exports[operation](store, *arguments)
    except (wasmtime.Trap, wasmtime.WasmtimeError, OSError) as exc:
        raise ValueError("MCP regex engine unavailable or resource limit exceeded") from exc


def validate(expression: str) -> None:
    """Validate the original and anchored wrapper before publishing managed policy."""
    status = _call("validate", expression)
    if status:
        raise ValueError(
            "invalid MCP regex"
            if status == 1
            else "MCP regex cannot be used for full-value matching"
        )


def matches(expression: str, candidate: str) -> bool:
    """A failed engine evaluation never authorizes a transport or remote effect."""
    try:
        return _call("matches", expression, candidate) == 1
    except ValueError:
        return False
