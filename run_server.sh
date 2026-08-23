#!/usr/bin/env bash
# Launch the Flask backend only.
set -euo pipefail
cd "$(dirname "$0")"

# Use the games_env conda environment.
if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate games_env
fi

export GAMES_HOST="${GAMES_HOST:-127.0.0.1}"
export GAMES_PORT="${GAMES_PORT:-5000}"

exec python -m server.app
