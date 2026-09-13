"""Console-script entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from corki import __version__
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.config.managed_mcp import load_mcp_requirements
from corki.core import LangGraphRuntime
from corki.core.construction import create_owned
from corki.protocol.session_source import SessionSource
from corki.storage import SQLiteSessionRepository


def build_parser() -> argparse.ArgumentParser:
    """Build a deliberately small parser that can grow with future milestones."""

    parser = argparse.ArgumentParser(
        prog="corki",
        description="Corki interactive coding agent",
    )
    parser.add_argument("--version", action="version", version=f"corki {__version__}")
    parser.add_argument("--model", help="explicit model override, including when resuming")
    parser.add_argument("--provider", help="explicit configured provider profile")
    parser.add_argument("--reasoning-effort", help="explicit reasoning effort override")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        metavar="THREAD_ID",
        help="resume the latest thread for this directory, or an explicit thread id",
    )
    parser.set_defaults(thread_id=None)
    commands = parser.add_subparsers(dest="command")
    resume = commands.add_parser("resume", help="resume a persisted conversation")
    resume.add_argument(
        "thread_id",
        nargs="?",
        help="thread id for `corki resume`; defaults to the latest in this directory",
    )
    # Preserve model overrides after the existing resume command, too.
    for flag in ("--model", "--provider", "--reasoning-effort"):
        resume.add_argument(flag, default=argparse.SUPPRESS)
    mcp = commands.add_parser("mcp", help="manage configured MCP services")
    actions = mcp.add_subparsers(dest="mcp_command", required=True)
    login = actions.add_parser("login", help="authorize a third-party MCP service")
    login.add_argument("name", help="configured MCP server name")
    login.add_argument("--scopes", metavar="SCOPE,SCOPE", help="explicit OAuth scopes")
    return parser


def build_application(
    *,
    resume: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    reasoning_effort: str | None = None,
    _construction: list | None = None,
) -> CorkiApplication:
    """Construct production dependencies at the outermost application boundary."""

    # Capture once before ensure_exists/SQLite. Runtime receives this exact policy,
    # not a second read that could fail after the CLI has already allocated state.
    requirements = load_mcp_requirements()
    paths = CorkiPaths.discover()
    paths.ensure_exists()
    database_path = paths.sessions_dir / "corki.db"
    repository = SQLiteSessionRepository(database_path)
    if _construction is not None:
        _construction.append(repository.close)
    directory = Path.cwd().resolve()
    thread_id = repository.resolve_thread(resume, directory) if resume else None
    settings = CorkiSettings.for_directory(
        directory,
        config_file=paths.config_file,
        resume_model_settings=(
            repository.read_thread_model_settings(thread_id) if thread_id is not None else None
        ),
        model=model,
        provider=provider,
        reasoning_effort=reasoning_effort,
    )
    ui = TerminalUI(settings, paths.input_history_file)
    runtime = LangGraphRuntime.create(
        settings=settings,
        database_path=database_path,
        repository=repository,
        thread_id=thread_id,
        session_source=SessionSource.from_startup_arg("cli"),
        home_path=paths.home,
        compatibility_home=Path.home(),
        mcp_requirements=requirements,
        _construction=_construction,
    )
    if _construction is not None:
        # The fully constructed Runtime now owns all its component lifetimes.
        _construction[:] = [runtime.aclose]
    return CorkiApplication(settings, paths, runtime, ui)


async def build_application_async(**kwargs) -> CorkiApplication:
    """Build on the application's event loop and await failed startup cleanup."""
    return await create_owned(build_application, kwargs)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and enter the interactive terminal application."""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.resume and arguments.command:
        parser.error("use either --resume or `corki resume`, not both")
    if arguments.thread_id and arguments.command != "resume":
        parser.error("THREAD_ID is only valid after `resume`")
    if arguments.command == "mcp":
        from corki.cli.mcp_login import LoginUsageError, run_login

        if any((arguments.model, arguments.provider, arguments.reasoning_effort)):
            parser.error("model options do not apply to MCP login")
        try:
            return asyncio.run(run_login(arguments.name, arguments.scopes))
        except KeyboardInterrupt:
            print("MCP login cancelled.", file=sys.stderr)
            return 130
        except LoginUsageError as exc:
            print(f"MCP login failed: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            # Provider/network exceptions can carry URLs or secret response bodies.
            print(f"MCP login failed ({type(exc).__name__}).", file=sys.stderr)
            return 1
    resume = arguments.resume
    if arguments.command == "resume":
        resume = arguments.thread_id or "latest"

    async def run():
        try:
            application = await build_application_async(
                resume=resume,
                model=arguments.model,
                provider=arguments.provider,
                reasoning_effort=arguments.reasoning_effort,
            )
        except ValueError as exc:
            parser.error(str(exc))
        except (asyncio.CancelledError, KeyboardInterrupt):
            return 130
        return await application.run()

    return asyncio.run(run())
