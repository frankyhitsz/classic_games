"""Flask backend server for classic games.

Provides REST APIs for:
- Listing supported games
- Submitting game scores
- Fetching per-game leaderboards
- Fetching player statistics

Run directly with `python -m server.app` or via `flask --app server.app run`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("GAMES_DB", BASE_DIR / "data" / "scores.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SUPPORTED_GAMES = [
    {"id": "tetris", "name": "俄罗斯方块", "description": "经典下落方块消除游戏"},
    {"id": "snake", "name": "贪吃蛇", "description": "控制蛇吃食物变长，别撞墙"},
    {"id": "2048", "name": "2048", "description": "滑动合并相同数字方块"},
    {"id": "sokoban", "name": "推箱子", "description": "把所有箱子推到目标点"},
    {"id": "zuma", "name": "祖玛", "description": "发射彩球，3+ 同色消除"},
]

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

VALID_GAME_IDS = frozenset(game["id"] for game in SUPPORTED_GAMES)
MAX_EXTRA_BYTES = 8 * 1024


@contextmanager
def db_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player TEXT NOT NULL,
                score INTEGER NOT NULL,
                extra TEXT,
                created_at REAL NOT NULL,
                updated_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_scores_game_score
                ON scores(game_id, score DESC);
            CREATE INDEX IF NOT EXISTS idx_scores_created
                ON scores(created_at DESC);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS score_requests (
                request_id TEXT PRIMARY KEY,
                score_id INTEGER NOT NULL,
                game_id TEXT NOT NULL,
                player TEXT NOT NULL,
                requested_score INTEGER NOT NULL,
                response_score INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        columns = {row["name"] for row in
                   conn.execute("PRAGMA table_info(scores)").fetchall()}
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE scores ADD COLUMN updated_at REAL")


# Flask's CLI imports the module without calling ``main``. Initialize here so
# every supported entry point sees the same ready database.
init_db()


def get_remote_ip() -> str:
    # This service binds to localhost by default and has no configured trusted
    # proxy. Accepting X-Forwarded-For here would only let callers spoof it.
    return request.remote_addr or "unknown"


def api_error(code: str, message: str, status: int = 400):
    return jsonify({"ok": False, "code": code, "error": message}), status


def parse_limit(default: int) -> tuple[int | None, object | None]:
    raw = request.args.get("limit")
    if raw is None:
        return default, None
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return None, api_error("invalid_limit", "limit must be an integer")
    if not 1 <= limit <= 50:
        return None, api_error("invalid_limit", "limit must be between 1 and 50")
    return limit, None


@app.errorhandler(RequestEntityTooLarge)
def _too_large(_exc):
    return api_error("body_too_large", "request body exceeds 64 KiB", 413)


@app.errorhandler(sqlite3.Error)
def _database_error(exc):
    app.logger.exception("score database operation failed", exc_info=exc)
    return api_error("database_unavailable",
                     "score database is temporarily unavailable", 503)


@app.before_request
def _before() -> None:
    g.started = time.time()


@app.after_request
def _after(resp):
    resp.headers["X-Backend-Latency-ms"] = f"{(time.time() - g.started) * 1000:.2f}"
    return resp


@app.route("/api/health")
def health():
    try:
        with db_conn() as conn:
            conn.execute("SELECT 1 FROM scores LIMIT 1").fetchone()
    except sqlite3.Error:
        return api_error("database_unavailable", "score database unavailable", 503)
    return jsonify({"ok": True, "service": "classic-games", "ts": time.time()})


@app.route("/api/games")
def games():
    return jsonify({"games": SUPPORTED_GAMES})


@app.route("/api/scores", methods=["POST"])
def submit_score():
    if not request.is_json:
        return api_error("invalid_content_type",
                         "Content-Type must be application/json")
    try:
        body = request.get_json(silent=False)
    except BadRequest:
        return api_error("malformed_json", "request body is not valid JSON")
    if not isinstance(body, dict):
        return api_error("invalid_body", "JSON body must be an object")
    allowed_fields = {"game_id", "player", "score", "extra", "replace",
                      "submission_id", "request_id"}
    unknown_fields = sorted(set(body) - allowed_fields)
    if unknown_fields:
        return api_error("unknown_fields",
                         f"unknown fields: {', '.join(unknown_fields)}")

    raw_game_id = body.get("game_id") or ""
    if not isinstance(raw_game_id, str):
        return api_error("invalid_game_id", "game_id must be a string")
    game_id = raw_game_id.strip()

    raw_player = body.get("player")
    if raw_player is None:
        raw_player = "anonymous"
    if not isinstance(raw_player, str):
        return api_error("invalid_player", "player must be a string")
    player = unicodedata.normalize("NFC", raw_player).strip() or "anonymous"
    if len(player) > 32:
        return api_error("invalid_player", "player must be at most 32 characters")
    if any(unicodedata.category(ch).startswith("C") for ch in player):
        return api_error("invalid_player", "player contains control characters")

    if "score" not in body:
        return api_error("invalid_score", "score is required")
    raw_score = body["score"]
    if type(raw_score) is not int:
        return api_error("invalid_score", "score must be an integer")
    score = raw_score
    if score < 0 or score > 2_147_483_647:
        return api_error("invalid_score",
                         "score must be between 0 and 2147483647")
    extra = body.get("extra")
    if extra is not None and not isinstance(extra, dict):
        return api_error("invalid_extra", "extra must be an object or null")
    try:
        extra_json = (json.dumps(extra, ensure_ascii=False,
                                 separators=(",", ":"), sort_keys=True,
                                 allow_nan=False)
                      if extra is not None else None)
    except (TypeError, ValueError):
        return api_error("invalid_extra", "extra must contain valid JSON values")
    if extra_json is not None and len(extra_json.encode("utf-8")) > MAX_EXTRA_BYTES:
        return api_error("extra_too_large", "extra exceeds 8 KiB")
    # ``replace=True`` deletes this player's previous submissions for
    # this game before inserting. Used by games whose score is a running
    # total that should overwrite an older completed run (e.g. Sokoban's
    # cumulative all-level total).
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        return api_error("invalid_replace", "replace must be boolean")
    submission_id = body.get("submission_id")
    if submission_id is not None:
        if type(submission_id) is not int or submission_id <= 0:
            return api_error("invalid_submission_id",
                             "submission_id must be a positive integer")
    request_id = body.get("request_id")
    if request_id is not None:
        if (not isinstance(request_id, str)
                or not 16 <= len(request_id) <= 64
                or not all(ch.isascii() and (ch.isalnum() or ch in "-_")
                           for ch in request_id)):
            return api_error(
                "invalid_request_id",
                "request_id must be 16-64 ASCII letters, digits, - or _")

    if game_id not in VALID_GAME_IDS:
        return api_error("unknown_game", f"unknown game_id: {game_id}")

    with db_conn() as conn:
        if request_id is not None:
            prior_request = conn.execute(
                "SELECT score_id, game_id, player, requested_score, "
                "response_score FROM score_requests "
                "WHERE request_id=?", (request_id,),
            ).fetchone()
            if prior_request is not None:
                if (prior_request["game_id"] != game_id
                        or prior_request["player"] != player
                        or prior_request["requested_score"] != score):
                    return api_error(
                        "request_id_conflict",
                        "request_id was already used for another score", 409)
                prior_rank = conn.execute(
                    "SELECT COUNT(*) + 1 FROM scores "
                    "WHERE game_id=? AND score > ?",
                    (game_id, prior_request["response_score"]),
                ).fetchone()[0]
                return jsonify({
                    "ok": True,
                    "id": prior_request["score_id"],
                    "rank": prior_rank,
                    "replaced": 0,
                    "updated": False,
                    "preserved": False,
                    "score": prior_request["response_score"],
                    "duplicate_request": True,
                    "submitted_from": get_remote_ip(),
                })
        deleted = 0
        updated = False
        preserved = False
        row_id = None
        stored_score = score
        if submission_id is not None:
            existing_update = conn.execute(
                "SELECT score FROM scores "
                "WHERE id=? AND game_id=? AND player=?",
                (submission_id, game_id, player),
            ).fetchone()
            if existing_update is not None:
                stored_score = max(existing_update["score"], score)
                if score < existing_update["score"]:
                    conn.execute(
                        "UPDATE scores SET score=?, updated_at=? WHERE id=?",
                        (stored_score, time.time(), submission_id),
                    )
                else:
                    conn.execute(
                        "UPDATE scores SET score=?, extra=?, updated_at=? "
                        "WHERE id=?",
                        (stored_score, extra_json, time.time(), submission_id),
                    )
                updated = True
                row_id = submission_id
        if not updated and replace:
            # Running-total games submit intermediate milestones. Preserve a
            # previous higher completed run instead of erasing it when the
            # same player starts a new, lower partial run.
            existing = conn.execute(
                "SELECT id, score FROM scores WHERE game_id=? AND player=? "
                "ORDER BY score DESC LIMIT 1",
                (game_id, player),
            ).fetchone()
            if existing is not None and existing["score"] >= score:
                preserved = True
                row_id = existing["id"]
                stored_score = existing["score"]
            else:
                cur = conn.execute(
                    "DELETE FROM scores WHERE game_id=? AND player=?",
                    (game_id, player),
                )
                deleted = cur.rowcount
        if not updated and not preserved:
            cur = conn.execute(
                "INSERT INTO scores "
                "(game_id, player, score, extra, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, player, score, extra_json, time.time(), time.time()),
            )
            row_id = cur.lastrowid
        # Compute rank
        rank = conn.execute(
            "SELECT COUNT(*) + 1 FROM scores WHERE game_id=? AND score > ?",
            (game_id, stored_score),
        ).fetchone()[0]
        if request_id is not None:
            conn.execute(
                "INSERT INTO score_requests "
                "(request_id, score_id, game_id, player, requested_score, "
                "response_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, row_id, game_id, player, score, stored_score,
                 time.time()),
            )

    return jsonify(
        {
            "ok": True,
            "id": row_id,
            "rank": rank,
            "replaced": deleted,
            "updated": updated,
            "preserved": preserved,
            "score": stored_score,
            "submitted_from": get_remote_ip(),
        }
    )


@app.route("/api/leaderboard/<game_id>")
def leaderboard(game_id: str):
    if game_id not in VALID_GAME_IDS:
        return api_error("unknown_game", f"unknown game_id: {game_id}", 404)
    limit, error = parse_limit(10)
    if error is not None:
        return error

    with db_conn() as conn:
        rows = conn.execute(
            "SELECT player, score, created_at FROM scores "
            "WHERE game_id=? ORDER BY score DESC, created_at ASC LIMIT ?",
            (game_id, limit),
        ).fetchall()

    entries = []
    rank = 0
    previous_score = None
    for i, row in enumerate(rows):
        if row["score"] != previous_score:
            rank = i + 1
            previous_score = row["score"]
        entries.append({"rank": rank, "player": row["player"],
                        "score": row["score"], "ts": row["created_at"]})
    return jsonify({"game_id": game_id, "leaderboard": entries})


@app.route("/api/stats/<game_id>")
def stats(game_id: str):
    if game_id not in VALID_GAME_IDS:
        return api_error("unknown_game", f"unknown game_id: {game_id}", 404)
    with db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(score) AS best, AVG(score) AS avg "
            "FROM scores WHERE game_id=?",
            (game_id,),
        ).fetchone()
    return jsonify(
        {
            "game_id": game_id,
            # These rows are retained score records, not actual launch/play
            # attempts: 2048 updates one row and Sokoban replaces older runs.
            "records": row["n"],
            "best": row["best"] if row["best"] is not None else 0,
            "avg": round(row["avg"], 2) if row["avg"] is not None else 0,
        }
    )


@app.route("/api/recent")
def recent():
    limit, error = parse_limit(20)
    if error is not None:
        return error
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT game_id, player, score, "
            "COALESCE(updated_at, created_at) AS activity_at FROM scores "
            "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return jsonify(
        {
            "recent": [
                {"game_id": r["game_id"], "player": r["player"], "score": r["score"],
                 "ts": r["activity_at"]}
                for r in rows
            ]
        }
    )


def main():
    init_db()
    host = os.environ.get("GAMES_HOST", "127.0.0.1")
    port = int(os.environ.get("GAMES_PORT", "5000"))
    print(f"[server] http://{host}:{port}  (db={DB_PATH})")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
