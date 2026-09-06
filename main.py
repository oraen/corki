"""Convenience entry point for running Corki from a source checkout.

The installed command is ``corki`` (configured in ``pyproject.toml``). Keeping
this tiny module is useful for IDE run configurations without duplicating any
CLI behaviour here.
"""

from corki.cli import main

if __name__ == "__main__":
    main()
