#!/bin/bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${CORKI_GATEWAY_PYTHON:-$script_dir/../.venv/bin/python}"
exec "$python_bin" -m corki_openai_gateway.control stop --home "${CORKI_HOME:-$HOME/.corki}"
