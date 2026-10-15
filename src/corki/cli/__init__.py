"""Corki's command-line adapter.

Only ``main`` is exported publicly. Keeping the remaining modules behind this
package boundary prevents presentation concerns from leaking into the future
LangGraph runtime.
"""

from corki.cli.main import main

__all__ = ["main"]
