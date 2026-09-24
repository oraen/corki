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
        help="thread id; omit to open the local session picker",
    )
    resume.add_argument(
        "--last", action="store_true", help="resume the latest thread in this directory"
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
    fork_from_thread_id=None,
    fork_before_user_message=None,
    working_directory: Path | None = None,
    _construction: list | None = None,
) -> CorkiApplication:
    """Construct production dependencies at the outermost application boundary."""

    directory = (working_directory or Path.cwd()).resolve()
    if not directory.is_dir():
        raise ValueError("Working directory is unavailable.")
    # Capture once before ensure_exists/SQLite. Runtime receives this exact policy,
    # not a second read that could fail after the CLI has already allocated state.
    requirements = load_mcp_requirements()
    paths = CorkiPaths.discover()
    paths.ensure_exists()
    database_path = paths.sessions_dir / "corki.db"
    repository = SQLiteSessionRepository(database_path)
    if _construction is not None:
        _construction.append(repository.close)
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
        fork_from_thread_id=fork_from_thread_id,
        fork_before_user_message=fork_before_user_message,
        session_source=SessionSource.from_startup_arg("cli"),
        home_path=paths.home,
        compatibility_home=Path.home(),
        mcp_requirements=requirements,
        _construction=_construction,
    )
    if _construction is not None:
        # The fully constructed Runtime now owns all its component lifetimes.
        _construction[:] = [runtime.aclose]
    application = CorkiApplication(settings, paths, runtime, ui)

    async def branch(source, index, prompt):
        from corki.cli.backtrack import prompt_input

        target = await build_application_async(
            model=ui._settings.model,
            provider=provider,
            reasoning_effort=ui._settings.reasoning_effort,
            fork_from_thread_id=source,
            fork_before_user_message=index,
            working_directory=ui._settings.working_directory,
        )
        try:
            await target._runtime.load_display_snapshot()  # publish before handing off ownership
            target._ui.restore_queued_inputs((prompt_input(prompt),))
            return target
        except BaseException:
            await target._runtime.aclose()
            raise

    application._branch_factory = branch
    return application


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
    pick_session = False
    if arguments.command == "resume":
        if arguments.thread_id and arguments.last:
            parser.error("use either THREAD_ID or --last")
        pick_session = not arguments.thread_id and not arguments.last
        resume = arguments.thread_id or "latest"

    async def run():
        try:
            selected_resume = resume
            directory_options = {}
            interactive = sys.stdin.isatty() and sys.stdout.isatty()
            if pick_session or (selected_resume is not None and interactive):
                if not interactive:
                    parser.error(
                        "session picker requires a terminal; use resume --last or THREAD_ID"
                    )
                from corki.cli.session_catalog import SessionCatalog
                from corki.cli.session_picker import choose_session

                paths = CorkiPaths.discover()
                paths.ensure_exists()
                database = paths.sessions_dir / "corki.db"
                repository = SQLiteSessionRepository(database)
                try:
                    if not pick_session:
                        selected_resume = str(
                            repository.resolve_thread(selected_resume, Path.cwd())
                        )
                finally:
                    await repository.close()
                catalog = SessionCatalog(database)
                if pick_session:
                    selected_resume = await choose_session(catalog, Path.cwd().resolve())
                if selected_resume is None:
                    return 0
                from corki.cli.resume_directory import choose_resume_directory

                record = await catalog.archive_store.read(selected_resume)
                directory = await choose_resume_directory(
                    Path.cwd(), record.cwd, config_file=paths.config_file
                )
                if directory is None:
                    return 0
                directory_options["working_directory"] = directory
            application = await build_application_async(
                resume=selected_resume,
                model=arguments.model,
                provider=arguments.provider,
                reasoning_effort=arguments.reasoning_effort,
                **directory_options,
            )
        except ValueError as exc:
            parser.error(str(exc))
        except (asyncio.CancelledError, KeyboardInterrupt):
            return 130
        except Exception as exc:  # noqa: BLE001 - startup errors may contain private details
            print(
                f"Corki could not start ({type(exc).__name__}). "
                "Check configuration and local storage permissions.",
                file=sys.stderr,
            )
            return 1
        while True:
            try:
                result = await application.run()
            except BaseException:
                if application.next_application is not None:
                    await application.next_application._runtime.aclose()
                raise
            if application.next_application is None:
                return result
            application = application.next_application

    return asyncio.run(run())
