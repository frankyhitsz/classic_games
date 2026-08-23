#!/usr/bin/env bash
# One-command launch: starts the Flask backend in the background,
# waits for it to come up, then launches the pygame hub.
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

mkdir -p data logs

# Start backend in background
GAMES_HOST="${GAMES_HOST:-127.0.0.1}"
GAMES_PORT="${GAMES_PORT:-5000}"
export GAMES_HOST GAMES_PORT

echo "[run] starting backend on http://${GAMES_HOST}:${GAMES_PORT}"
python -m server.app > logs/server.log 2>&1 &
SERVER_PID=$!

cleanup() {
    echo "[run] shutting down backend (pid=${SERVER_PID})"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for backend
for i in $(seq 1 30); do
    if curl -s "http://${GAMES_HOST}:${GAMES_PORT}/api/health" >/dev/null 2>&1; then
        echo "[run] backend is up"
        break
    fi
    sleep 0.3
done

# Launch the hub (foreground)
echo "[run] launching game hub"
python -m client.launcher
