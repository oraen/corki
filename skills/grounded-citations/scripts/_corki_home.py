"""Resolve ``CORKI_HOME`` for standalone bundled-skill scripts.

The helper deliberately has no Corki package import so it also works when a
skill script is launched with a system Python outside Corki's virtualenv.
"""

from __future__ import annotations

import os
from pathlib import Path

def get_corki_home() -> Path:
    """Return Corki's home directory (default: ``~/.corki``)."""

    value = os.environ.get("CORKI_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".corki"
