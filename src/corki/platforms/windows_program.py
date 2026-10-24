"""Resolve Windows configured programs; keep batch quoting in the native helper."""

import ntpath
import os
from pathlib import Path


def resolve_program(program: str, env: dict[str, str] | None, cwd: Path | None) -> str:
    """Search the child's PATH/PATHEXT, not the Python parent's PATH."""
    environment = {
        key.upper(): value for key, value in (os.environ if env is None else env).items()
    }
    directory = Path.cwd() if cwd is None else Path(cwd).absolute()
    extensions = [
        value for value in environment.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        if value.startswith(".") and len(value) > 1
    ]
    extension = ntpath.splitext(program)[1]
    suffixes = [""] if extension else []
    # As in the reference which resolver, a dot in the program name is not
    # necessarily an executable suffix: tool.v1 may resolve to tool.v1.cmd.
    if extension.casefold() not in {value.casefold() for value in extensions}:
        suffixes.extend(extensions)
    if ntpath.dirname(program):
        bases = [directory / program]
    else:
        bases = [
            directory / entry.strip('"') / program
            for entry in environment.get("PATH", "").split(os.pathsep)
            if entry
        ]
    for base in bases:
        for suffix in suffixes:
            candidate = Path(str(base) + suffix)
            if candidate.is_file():
                return str(candidate.absolute())
    # Preserve OS diagnostics and built-in executable search when resolution fails.
    return program


def prepare_command(args, *, env=None, cwd=None):
    from corki.execution.bundled import bundled_compiler

    program = resolve_program(os.fsdecode(args[0]), env, cwd)
    if ntpath.splitext(program)[1].lower() not in {".cmd", ".bat"}:
        return (program, *args[1:])
    # Never hand .cmd/.bat to Python's implicit shell launch: list2cmdline is a
    # C runtime quoting algorithm, not the Windows batch quoting algorithm.
    compiler = bundled_compiler()
    if compiler is None:
        raise OSError("Windows batch tools require the bundled Corki process helper")
    absolute = (Path.cwd() if cwd is None else Path(cwd)) / program
    return (str(compiler), "--corki-windows-program", str(absolute.absolute()), *args[1:])
