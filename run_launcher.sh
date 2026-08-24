#!/usr/bin/env bash
# Launch the pygame hub with its in-process local score store.
set -euo pipefail
cd "$(dirname "$0")"

if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate games_env
fi

exec python -m client.launcher
