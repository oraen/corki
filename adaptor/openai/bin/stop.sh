#!/bin/bash
# Unload instead of killing the process: KeepAlive must not restart it.
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
  echo 'These service scripts require macOS launchd.' >&2
  exit 1
fi

service="gui/$(id -u)/local.corki.codex-gateway"
if ! launchctl print "$service" >/dev/null 2>&1; then
  echo 'Gateway service is already stopped.'
  exit 0
fi
launchctl bootout "$service"
echo 'Gateway service stopped; configuration and credentials retained.'
