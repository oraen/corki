#!/bin/bash
# Same per-user lifecycle as the management page, with no embedded login token.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${CORKI_GATEWAY_PYTHON:-$script_dir/../.venv/bin/python}"
gateway_args=(start --home "${CORKI_HOME:-$HOME/.corki}")
if [[ -n "${CORKI_GATEWAY_PORT:-}" ]]; then
  gateway_args+=(--port "$CORKI_GATEWAY_PORT")
fi
exec "$python_bin" -m corki_adaptor.control "${gateway_args[@]}"
