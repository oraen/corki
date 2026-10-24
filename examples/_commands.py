"""Explicit shell commands for the disposable demonstration scripts."""

import os
import shlex
import sys
from pathlib import Path


def python_script_command(workspace: Path, name: str, source: str) -> dict[str, object]:
    script = workspace / name
    script.write_text(source, encoding='utf-8')
    if os.name == 'nt':
        # A file avoids PowerShell 5.1 stripping quotes from Python -c source.
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        return {
            'cmd': f'& {quote(sys.executable)} -u {quote(str(script))}; exit $LASTEXITCODE',
            'shell': 'powershell',
            'login': False,
        }
    return {
        'cmd': shlex.join([sys.executable, '-u', str(script)]),
        'shell': '/bin/sh',
        'login': False,
    }
