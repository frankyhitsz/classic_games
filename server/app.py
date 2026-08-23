"""Flask backend server for classic games.

Provides REST APIs for:
- Listing supported games
- Submitting game scores
- Fetching per-game leaderboards
- Fetching player statistics

Run directly with `python -m server.app` or via `flask run`.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, g, jsonify, request

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


@contextmanager
def db_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player TEXT NOT NULL,
                score INTEGER NOT NULL,
                extra TEXT,
                created_at REAL NOT NULL
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
            """
        )


def get_remote_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")


@app.before_request
def _before() -> None:
    g.started = time.time()


@app.after_request
def _after(resp):
    resp.headers["X-Backend-Latency-ms"] = f"{(time.time() - g.started) * 1000:.2f}"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "classic-games", "ts": time.time()})


@app.route("/api/games")
def games():
    return jsonify({"games": SUPPORTED_GAMES})


@app.route("/api/scores", methods=["POST"])
def submit_score():
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "JSON body must be an object"}), 400

    raw_game_id = body.get("game_id") or ""
    if not isinstance(raw_game_id, str):
        return jsonify({"ok": False, "error": "game_id must be a string"}), 400
    game_id = raw_game_id.strip()

    raw_player = body.get("player")
    if raw_player is None:
        raw_player = "anonymous"
    if not isinstance(raw_player, str):
        return jsonify({"ok": False, "error": "player must be a string"}), 400
    player = raw_player.strip()[:32] or "anonymous"

    raw_score = body.get("score", 0)
    if isinstance(raw_score, bool):
        return jsonify({"ok": False, "error": "score must be int"}), 400
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "score must be int"}), 400
    if score < 0 or score > 2_147_483_647:
        return jsonify({"ok": False,
                        "error": "score must be between 0 and 2147483647"}), 400
    extra = body.get("extra")
    # ``replace=True`` deletes this player's previous submissions for
    # this game before inserting. Used by games whose score is a running
    # total that should overwrite earlier milestones (e.g. Sokoban's
    # cumulative 4-level total).
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        return jsonify({"ok": False, "error": "replace must be boolean"}), 400
    submission_id = body.get("submission_id")
    if submission_id is not None:
        try:
            if isinstance(submission_id, bool):
                raise ValueError
            submission_id = int(submission_id)
            if submission_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "submission_id must be a positive int"}), 400

    valid_ids = {g["id"] for g in SUPPORTED_GAMES}
    if game_id not in valid_ids:
        return jsonify({"ok": False, "error": f"unknown game_id: {game_id}"}), 400

    with db_conn() as conn:
        deleted = 0
        updated = False
        preserved = False
        row_id = None
        stored_score = score
        if submission_id is not None:
            cur = conn.execute(
                "UPDATE scores SET score=?, extra=?, created_at=? "
                "WHERE id=? AND game_id=? AND player=?",
                (score, str(extra) if extra is not None else None, time.time(),
                 submission_id, game_id, player),
            )
            updated = cur.rowcount > 0
            if updated:
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
                "INSERT INTO scores (game_id, player, score, extra, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (game_id, player, score,
                 str(extra) if extra is not None else None, time.time()),
            )
            row_id = cur.lastrowid
        # Compute rank
        rank = conn.execute(
            "SELECT COUNT(*) + 1 FROM scores WHERE game_id=? AND score > ?",
            (game_id, stored_score),
        ).fetchone()[0]

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
    try:
        limit = max(1, min(50, int(request.args.get("limit", 10))))
    except ValueError:
        limit = 10

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
    with db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(score) AS best, AVG(score) AS avg "
            "FROM scores WHERE game_id=?",
            (game_id,),
        ).fetchone()
    return jsonify(
        {
            "game_id": game_id,
            "plays": row["n"],
            "best": row["best"] if row["best"] is not None else 0,
            "avg": round(row["avg"], 2) if row["avg"] is not None else 0,
        }
    )


@app.route("/api/recent")
def recent():
    try:
        limit = max(1, min(50, int(request.args.get("limit", 20))))
    except ValueError:
        limit = 20
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT game_id, player, score, created_at FROM scores "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return jsonify(
        {
            "recent": [
                {"game_id": r["game_id"], "player": r["player"], "score": r["score"],
                 "ts": r["created_at"]}
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
