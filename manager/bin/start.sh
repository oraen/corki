#!/bin/bash
set -euo pipefail
manager_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${CORKI_MANAGER_PYTHON:-$manager_dir/../.venv/bin/python}"
exec "$python_bin" "$manager_dir/server.py" --workspace "$manager_dir/.." "$@"
