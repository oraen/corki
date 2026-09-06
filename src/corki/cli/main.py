"""Console-script entry point."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from corki import __version__
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.storage import SQLiteSessionRepository


def build_parser() -> argparse.ArgumentParser:
    """Build a deliberately small parser that can grow with future milestones."""

    parser = argparse.ArgumentParser(
        prog="corki",
        description="Corki interactive coding agent",
    )
    parser.add_argument("--version", action="version", version=f"corki {__version__}")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        metavar="THREAD_ID",
        help="resume the latest thread for this directory, or an explicit thread id",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("resume",),
        help="resume a persisted conversation",
    )
    parser.add_argument(
        "thread_id",
        nargs="?",
        help="thread id for `corki resume`; defaults to the latest in this directory",
    )
    return parser


def build_application(*, resume: str | None = None) -> CorkiApplication:
    """Construct production dependencies at the outermost application boundary."""

    paths = CorkiPaths.discover()
    paths.ensure_exists()
    settings = CorkiSettings.for_directory(config_file=paths.config_file)
    ui = TerminalUI(settings, paths.input_history_file)
    database_path = paths.sessions_dir / "corki.db"
    repository = SQLiteSessionRepository(database_path)
    thread_id = repository.resolve_thread(resume, settings.working_directory) if resume else None
    runtime = LangGraphRuntime.create(
        settings=settings,
        database_path=database_path,
        repository=repository,
        thread_id=thread_id,
        home_path=paths.home,
        compatibility_home=Path.home(),
    )
    return CorkiApplication(settings, paths, runtime, ui)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and enter the interactive terminal application."""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.resume and arguments.command:
        parser.error("use either --resume or `corki resume`, not both")
    if arguments.thread_id and arguments.command != "resume":
        parser.error("THREAD_ID is only valid after `resume`")
    resume = arguments.resume
    if arguments.command == "resume":
        resume = arguments.thread_id or "latest"
    try:
        application = build_application(resume=resume)
    except ValueError as exc:
        parser.error(str(exc))
    return asyncio.run(application.run())
