"""Source/packaged entry point. Uses the same installed Corki Python environment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from corki_gateway.control import main

if __name__ == "__main__":
    main()
