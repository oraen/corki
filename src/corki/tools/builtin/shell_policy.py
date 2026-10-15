"""Source-mapped unified-exec yield admission and effective waiting bounds."""

import os

U64_MAX = 2**64 - 1
DEFAULT_BACKGROUND_TERMINAL_MAX_TIMEOUT = 300_000


def milliseconds(value: object) -> int:
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ValueError("yield_time_ms must be an unsigned 64-bit integer")
    return value


def exec_yield_ms(value: object, *, windows: bool | None = None) -> int:
    value = milliseconds(value)
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        value = max(10_000, value)
    return min(30_000, max(250, value))


def stdin_yield_ms(value: object, *, empty: bool, maximum: int) -> int:
    value = milliseconds(value)
    if empty:
        return min(max(5_000, maximum), max(5_000, value))
    return min(30_000, max(250, value))
