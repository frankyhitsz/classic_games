"""Optional Flask adapter over the same local store used by pygame."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import BadRequest, HTTPException, RequestEntityTooLarge

from game_service.catalog import VALID_GAME_IDS, public_games
from game_service.store import LocalGameStore, StoreError, default_database_path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = default_database_path()
SUPPORTED_GAMES = public_games()


def init_db(db_path: Path | str | None = None) -> LocalGameStore:
    """Explicit initialization helper for scripts and migrations."""
    return LocalGameStore(db_path or DB_PATH)


def create_app(config: dict | None = None) -> Flask:
    """Create an API adapter without performing work at module import."""
    app = Flask(__name__)
    app.config.from_mapping(
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=64 * 1024,
        DB_PATH=str(DB_PATH),
        INITIALIZE_DB=True,
        LEGACY_DB_PATH=None,
    )
    if config:
        app.config.update(config)

    store = LocalGameStore(
        app.config["DB_PATH"],
        legacy_db_path=app.config.get("LEGACY_DB_PATH"),
        initialize=bool(app.config.get("INITIALIZE_DB", True)),
    )
    app.extensions["game_store"] = store

    def api_error(code: str, message: str, status: int = 400,
                  retryable: bool = False, details: dict | None = None):
        payload = {"ok": False, "code": code, "error": message,
                   "retryable": retryable}
        if details:
            payload["details"] = details
        return jsonify(payload), status

    def parse_limit(default: int):
        raw = request.args.get("limit")
        if raw is None:
            return default, None
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return None, api_error("invalid_limit", "limit must be an integer")
        if not 1 <= limit <= 50:
            return None, api_error(
                "invalid_limit", "limit must be between 1 and 50")
        return limit, None

    def query_dimensions(allowed: set[str], *,
                         allow_limit: bool = False) -> tuple[dict, object | None]:
        accepted = allowed | ({"limit"} if allow_limit else set())
        unknown = sorted(set(request.args) - accepted)
        if unknown:
            return {}, api_error(
                "unsupported_query_parameter",
                f"unsupported query parameters: {', '.join(unknown)}")
        dimensions = {}
        for key in allowed:
            value = request.args.get(key)
            if value is not None:
                dimensions[key] = value
        return dimensions, None

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return api_error("body_too_large", "request body exceeds 64 KiB", 413)

    @app.errorhandler(sqlite3.Error)
    def database_error(exc):
        app.logger.exception("score database operation failed", exc_info=exc)
        return api_error("database_unavailable",
                         "score database is temporarily unavailable", 503,
                         retryable=True)

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        if isinstance(exc, HTTPException):
            code = (exc.name or "http_error").lower().replace(" ", "_")
            return api_error(code, exc.description, exc.code or 500)
        app.logger.exception("unexpected API error", exc_info=exc)
        return api_error("internal_error", "unexpected server error", 500)

    @app.before_request
    def before_request() -> None:
        g.started = time.time()

    @app.after_request
    def after_request(response):
        response.headers["X-Backend-Latency-ms"] = (
            f"{(time.time() - g.started) * 1000:.2f}")
        return response

    @app.route("/api/health")
    def health():
        try:
            status = store.storage_status(outbox_writable=False)
        except sqlite3.Error:
            return api_error("database_unavailable",
                             "score database unavailable", 503,
                             retryable=True)
        response = status.to_dict()
        response.update({"service": "classic-games",
                         "storage": "local-sqlite", "ts": time.time()})
        return jsonify(response), (200 if status.readable else 503)

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
            return api_error("malformed_json",
                             "request body is not valid JSON")
        if not isinstance(body, dict):
            return api_error("invalid_body", "JSON body must be an object")
        allowed = {"game_id", "player", "score", "extra", "replace",
                   "submission_id", "request_id", "attempt_uuid",
                   "revision", "profile_id", "mode", "ruleset_version",
                   "status"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            return api_error("unknown_fields",
                             f"unknown fields: {', '.join(unknown)}")
        try:
            player = body.get("player")
            if player is None:
                player = "anonymous"
            result = store.record_score(
                body.get("game_id", ""), player,
                body.get("score"), extra=body.get("extra"),
                replace=body.get("replace", False),
                submission_id=body.get("submission_id"),
                request_id=body.get("request_id"),
                attempt_uuid=body.get("attempt_uuid"),
                revision=body.get("revision"),
                profile_id=body.get("profile_id"),
                mode=body.get("mode", "classic"),
                ruleset_version=body.get("ruleset_version"),
                status=body.get("status", "completed"),
            )
        except StoreError as exc:
            return api_error(exc.code, exc.message, exc.status, exc.retryable,
                             exc.details)
        result["submitted_from"] = request.remote_addr or "unknown"
        return jsonify(result)

    @app.route("/api/leaderboard/<game_id>")
    def leaderboard(game_id: str):
        if game_id not in VALID_GAME_IDS:
            return api_error("unknown_game", f"unknown game_id: {game_id}", 404)
        limit, error = parse_limit(10)
        if error is not None:
            return error
        dimensions, error = query_dimensions(
            {"mode", "ruleset_version", "status"}, allow_limit=True)
        if error is not None:
            return error
        try:
            rows = store.leaderboard(game_id, limit, **dimensions)
        except StoreError as exc:
            return api_error(exc.code, exc.message, exc.status, exc.retryable,
                             exc.details)
        return jsonify({"game_id": game_id, "leaderboard": rows})

    @app.route("/api/stats/<game_id>")
    def stats(game_id: str):
        dimensions, error = query_dimensions(
            {"profile_id", "mode", "ruleset_version", "status"})
        if error is not None:
            return error
        try:
            return jsonify(store.stats(game_id, **dimensions))
        except StoreError as exc:
            return api_error(exc.code, exc.message, exc.status, exc.retryable,
                             exc.details)

    @app.route("/api/recent")
    def recent():
        limit, error = parse_limit(20)
        if error is not None:
            return error
        dimensions, error = query_dimensions(
            {"profile_id", "game_id", "mode", "ruleset_version", "status"},
            allow_limit=True)
        if error is not None:
            return error
        try:
            rows = store.recent(limit, **dimensions)
        except StoreError as exc:
            return api_error(exc.code, exc.message, exc.status, exc.retryable,
                             exc.details)
        return jsonify({"recent": rows})

    return app


def main() -> None:
    host = os.environ.get("GAMES_HOST", "127.0.0.1")
    port = int(os.environ.get("GAMES_PORT", "5000"))
    app = create_app()
    print(f"[server] http://{host}:{port}  (db={app.config['DB_PATH']})")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
