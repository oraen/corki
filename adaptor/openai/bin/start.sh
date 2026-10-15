#!/bin/bash
# Start the per-user macOS service; never put an upstream token in this script.
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
  echo 'These service scripts require macOS launchd; use python -m corki_openai_gateway --serve elsewhere.' >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
python_bin="${CORKI_GATEWAY_PYTHON:-$project_dir/.venv/bin/python}"
plist="$HOME/Library/LaunchAgents/local.corki.codex-gateway.plist"
service="gui/$(id -u)/local.corki.codex-gateway"

if launchctl print "$service" >/dev/null 2>&1; then
  launchctl kickstart "$service"
  echo 'Gateway service is already loaded; ensured it is started.'
  exit 0
fi

if [[ ! -x "$python_bin" ]]; then
  echo 'Python environment missing. Run adaptor/openai/bin/install.sh first or set CORKI_GATEWAY_PYTHON.' >&2
  exit 1
fi

# Existing deployments retain their port, key file, and credential source.
# For a new deployment, use file credentials so Codex can rotate tokens live.
"$python_bin" - "$project_dir" "$plist" <<'PY'
import os
import plistlib
import sys
from pathlib import Path

root, plist = map(Path, sys.argv[1:])
# Fail before changing deployment if the selected interpreter lacks the package.
import corki_openai_gateway.gateway

if plist.exists():
    document = plistlib.loads(plist.read_bytes())
    arguments = document["ProgramArguments"]
    if "-m" not in arguments:
        raise SystemExit("Existing service has an unsupported entry point; configuration unchanged.")
    offset = arguments.index("-m")
    if arguments[offset + 1] not in (
        "corki.codex_gateway", "adaptor.openai", "corki_openai_gateway"
    ):
        raise SystemExit("Existing service is not this gateway; configuration unchanged.")
    document["ProgramArguments"] = [
        sys.executable, "-m", "corki_openai_gateway", *arguments[offset + 2:]
    ]
    document["WorkingDirectory"] = str(root)
    with plist.open("wb") as stream:
        os.chmod(plist, 0o600)
        plistlib.dump(document, stream)
    raise SystemExit(0)

home = Path(os.environ.get("CORKI_HOME", str(Path.home() / ".corki"))).expanduser()
logs = home / "gateway"
key = Path(os.environ.get("CORKI_GATEWAY_KEY_FILE", str(logs / "local.key"))).expanduser()
auth = Path(os.environ.get(
    "CORKI_GATEWAY_AUTH_FILE",
    str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"),
)).expanduser()
if not key.is_file():
    raise SystemExit("Local key file missing; set CORKI_GATEWAY_KEY_FILE to your gateway key file.")
if not auth.is_file():
    raise SystemExit("Codex auth file missing; sign in or set CORKI_GATEWAY_AUTH_FILE.")
port = int(os.environ.get("CORKI_GATEWAY_PORT", "17001"))
if not 1 <= port <= 65535:
    raise SystemExit("CORKI_GATEWAY_PORT must be between 1 and 65535.")
logs.mkdir(parents=True, exist_ok=True)
plist.parent.mkdir(parents=True, exist_ok=True)
document = {
    "Label": "local.corki.codex-gateway",
    "ProgramArguments": [sys.executable, "-m", "corki_openai_gateway", "--serve",
                         "--port", str(port), "--key-file", str(key), "--auth-file", str(auth)],
    "WorkingDirectory": str(root),
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": str(logs / "stdout.log"),
    "StandardErrorPath": str(logs / "stderr.log"),
}
with plist.open("wb") as stream:
    os.chmod(plist, 0o600)
    plistlib.dump(document, stream)
PY

launchctl bootstrap "gui/$(id -u)" "$plist"
echo "Gateway service loaded. Configuration: $plist"
