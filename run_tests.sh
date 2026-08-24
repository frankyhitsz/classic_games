#!/usr/bin/env bash
# Run the headless regression suite.
set -euo pipefail
cd "$(dirname "$0")"

if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate games_env
fi

python -c 'import flask, pygame, requests' 2>/dev/null || {
    echo "[tests] missing development dependencies; run: pip install -e '.[dev]'" >&2
    exit 1
}

# Always use an isolated backend so repeated test runs neither depend on nor
# pollute the player's real leaderboard database.
TEST_RUNTIME_DIR=$(mktemp -d "${TMPDIR:-/tmp}/classic-games-tests.XXXXXX")
TEST_PORT=$(python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
TEST_API_URL="http://127.0.0.1:${TEST_PORT}"

default_db_fingerprint() {
    python -c 'import hashlib,pathlib; p=pathlib.Path("data"); print("|".join(f"{x.name}:{hashlib.sha256(x.read_bytes()).hexdigest()}" for x in sorted(p.glob("scores.db*")) if x.is_file()) or "absent")'
}
DEFAULT_DB_BEFORE=$(default_db_fingerprint)

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

TEST_STATUS=0
env GAMES_API_URL="${TEST_API_URL}" \
    GAMES_DB="${TEST_RUNTIME_DIR}/direct-import.db" \
    SDL_VIDEODRIVER=dummy \
    SDL_AUDIODRIVER=dummy python -m tests.regression || TEST_STATUS=$?

if [[ "${TEST_STATUS}" -eq 0 ]]; then
    env GAMES_DB="${TEST_RUNTIME_DIR}/review4-default.db" \
        SDL_VIDEODRIVER=dummy \
        SDL_AUDIODRIVER=dummy python -m unittest discover -s tests \
        -p 'test_storage*.py' \
        || TEST_STATUS=$?
fi

if [[ "${TEST_STATUS}" -eq 0 ]]; then
    env GAMES_DB="${TEST_RUNTIME_DIR}/stress-default.db" \
        SDL_VIDEODRIVER=dummy \
        SDL_AUDIODRIVER=dummy python -m tests.stress || TEST_STATUS=$?
fi

DEFAULT_DB_AFTER=$(default_db_fingerprint)
if [[ "${DEFAULT_DB_BEFORE}" != "${DEFAULT_DB_AFTER}" ]]; then
    echo "tests modified the default score database" >&2
    exit 1
fi
exit "${TEST_STATUS}"
