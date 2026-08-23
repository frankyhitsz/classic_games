#!/usr/bin/env bash
# Run the headless regression suite.
set -euo pipefail
cd "$(dirname "$0")"

if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate games_env
fi

# Always use an isolated backend so repeated test runs neither depend on nor
# pollute the player's real leaderboard database.
TEST_RUNTIME_DIR=$(mktemp -d "${TMPDIR:-/tmp}/classic-games-tests.XXXXXX")
TEST_PORT=$(python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
TEST_API_URL="http://127.0.0.1:${TEST_PORT}"

health_ok() {
    python -c 'import json,sys,urllib.request as u; d=json.load(u.urlopen(sys.argv[1], timeout=.3)); sys.exit(0 if d.get("ok") and d.get("service")=="classic-games" else 1)' \
        "${TEST_API_URL}/api/health" >/dev/null 2>&1
}

cleanup() {
    kill "${TEST_SERVER_PID}" 2>/dev/null || true
    wait "${TEST_SERVER_PID}" 2>/dev/null || true
    rm -rf "${TEST_RUNTIME_DIR}"
}

GAMES_DB="${TEST_RUNTIME_DIR}/scores.db" GAMES_PORT="${TEST_PORT}" \
    python -m server.app > "${TEST_RUNTIME_DIR}/server.log" 2>&1 &
TEST_SERVER_PID=$!
trap cleanup EXIT

for _ in {1..30}; do
    health_ok && break
    sleep 0.2
done
if ! health_ok; then
    echo "isolated test backend failed to start" >&2
    exit 1
fi

env GAMES_API_URL="${TEST_API_URL}" SDL_VIDEODRIVER=dummy \
    SDL_AUDIODRIVER=dummy python -m tests.regression
