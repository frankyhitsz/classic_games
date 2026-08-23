#!/usr/bin/env bash
# Launch the pygame launcher only (assumes server is already running,
# but works in offline mode if not).
set -euo pipefail
cd "$(dirname "$0")"

if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate games_env
fi

exec python -m client.launcher
