"""Find Node/npm when launched from Finder or Explorer without shell startup files."""

import os
import shutil
import sys
from pathlib import Path


def find(name):
    found = shutil.which(name)
    if found:
        return found
    candidates = []
    if sys.platform == "darwin":
        candidates = [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    elif os.name == "nt":
        candidates = [
            Path(value) / suffix
            for key, suffix in (("ProgramFiles", "nodejs"), ("LOCALAPPDATA", "Programs/nodejs"))
            if (value := os.environ.get(key))
        ]
        name += ".cmd" if name == "npm" else ".exe"
    return next((str(folder / name) for folder in candidates if (folder / name).is_file()), None)


def environment():
    env = os.environ.copy()
    node = find("node")
    if node:
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    return env
