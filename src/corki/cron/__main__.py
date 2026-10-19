"""Standalone ticker: python -m corki.cron [--home PATH]."""

import argparse
import os
import time
from pathlib import Path

from .service import CronService


def main():
    parser = argparse.ArgumentParser(description="Corki scheduled task service")
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("CORKI_HOME", "~/.corki")))
    args = parser.parse_args()
    service = CronService(args.home)
    service.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        service.close()


if __name__ == "__main__":
    main()
