"""Entry point launched by Electron with an installed Corki Python environment."""

import argparse
import asyncio
import os
from pathlib import Path

from protocol import Transport


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=os.environ.get("CORKI_HOME", str(Path.home() / ".corki")))
    args = parser.parse_args()
    transport = Transport()
    from service import Service

    service = Service(Path(args.home), transport.send)
    await transport.serve(service)


if __name__ == "__main__":
    asyncio.run(main())
