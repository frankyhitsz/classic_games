"""Thin HTTP client wrapping the Flask backend.

Used by every game client so we keep a single place for endpoint URLs,
timeouts, and error handling. Falls back gracefully when the server is
down — local play should still work.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

import requests

DEFAULT_BASE = os.environ.get("GAMES_API_URL", "http://127.0.0.1:5000")
TIMEOUT = (0.30, 0.70)  # local connect/read timeouts
FAILURE_BACKOFF_SECS = 2.0


class BackendClient:
    def __init__(self, base_url: str = DEFAULT_BASE, timeout=TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._offline_until = 0.0

    def _request_allowed(self) -> bool:
        return time.monotonic() >= self._offline_until

    def _mark_unavailable(self) -> None:
        self._offline_until = time.monotonic() + FAILURE_BACKOFF_SECS

    def _mark_available(self) -> None:
        self._offline_until = 0.0

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[dict]:
        if not self._request_allowed():
            return None
        try:
            resp = requests.get(
                f"{self.base_url}{path}", params=params, timeout=self.timeout
            )
            if resp.ok:
                self._mark_available()
                return resp.json()
            if resp.status_code >= 500:
                self._mark_unavailable()
        except requests.RequestException:
            self._mark_unavailable()
        return None

    def _post(self, path: str, payload: Dict[str, Any]) -> Optional[dict]:
        if not self._request_allowed():
            return None
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            if resp.ok:
                self._mark_available()
                return resp.json()
            if resp.status_code >= 500:
                self._mark_unavailable()
        except requests.RequestException:
            self._mark_unavailable()
        return None

    # ----- public API -------------------------------------------------------
    def health(self) -> bool:
        r = self._get("/api/health")
        return bool(r and r.get("ok"))

    def list_games(self):
        r = self._get("/api/games")
        return (r or {}).get("games", [])

    def leaderboard(self, game_id: str, limit: int = 10):
        r = self._get(f"/api/leaderboard/{game_id}", params={"limit": limit})
        return (r or {}).get("leaderboard", [])

    def stats(self, game_id: str):
        r = self._get(f"/api/stats/{game_id}")
        return r or {"game_id": game_id, "plays": 0, "best": 0, "avg": 0}

    def submit_score(self, game_id: str, player: str, score: int,
                     extra=None, replace: bool = False,
                     submission_id: Optional[int] = None):
        """Submit a score.

        ``replace=True`` asks the backend to delete this player's
        previous submissions for this game before inserting. Useful
        for games whose score is a running total (e.g. Sokoban's
        cumulative full-run total) where intermediate milestones
        shouldn't clutter the leaderboard.
        """
        payload = {"game_id": game_id, "player": player, "score": score}
        if extra is not None:
            payload["extra"] = extra
        if replace:
            payload["replace"] = True
        if submission_id is not None:
            payload["submission_id"] = submission_id
        return self._post("/api/scores", payload)

    def recent(self, limit: int = 20):
        r = self._get("/api/recent", params={"limit": limit})
        return (r or {}).get("recent", [])
