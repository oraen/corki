"""Local Python command fixtures for the shell actually selected by Corki."""

import shlex
import sys
from hashlib import sha256

from corki.shell import ShellType, default_user_shell


def python_command(directory, source, *, name=None, isolated=False):
    # A script avoids PowerShell 5's native argv quoting of embedded Python -c
    # strings. Spaces and Unicode in the fixture path remain real test inputs.
    if name is None:
        name = f"hook fixture {sha256(source.encode('utf-8')).hexdigest()[:16]}.py"
    path = directory / name
    path.write_text(source, encoding="utf-8")
    arguments = [sys.executable, *(["-I"] if isolated else []), "-X", "utf8", str(path)]
    if default_user_shell().kind == ShellType.POWERSHELL:
        return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in arguments)
    return shlex.join(arguments)
