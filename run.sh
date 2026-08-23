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
BACKEND_READY=false
HEALTH_URL="http://${GAMES_HOST}:${GAMES_PORT}/api/health"
health_ok() {
    python -c 'import json,sys,urllib.request as u; d=json.load(u.urlopen(sys.argv[1], timeout=.3)); sys.exit(0 if d.get("ok") and d.get("service")=="classic-games" else 1)' \
        "${HEALTH_URL}" >/dev/null 2>&1
}
for _ in {1..30}; do
    if health_ok; then
        echo "[run] backend is up"
        BACKEND_READY=true
        break
    fi
    sleep 0.3
done
if [[ "${BACKEND_READY}" != true ]]; then
    echo "[run] backend failed to start; see logs/server.log" >&2
    tail -n 20 logs/server.log >&2 || true
    exit 1
fi

# Launch the hub (foreground)
echo "[run] launching game hub"
python -m client.launcher
