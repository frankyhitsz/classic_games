#!/usr/bin/env bash
# Default desktop launch: local SQLite is used directly, without Flask.
set -euo pipefail
cd "$(dirname "$0")"

if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate games_env || {
        echo "[run] conda env 'games_env' not found. Creating..."
        conda env create -f environment.yml
        conda activate games_env
    }
fi

exec python -m client.launcher
