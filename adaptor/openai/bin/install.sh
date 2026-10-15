#!/bin/bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  echo 'Install uv first, then run this script again.' >&2
  exit 1
fi
# Always use the gateway environment, even from an activated Corki environment.
UV_PROJECT_ENVIRONMENT="$project_dir/.venv" uv sync --project "$project_dir" --frozen --no-dev
echo "Gateway installed in $project_dir/.venv"
