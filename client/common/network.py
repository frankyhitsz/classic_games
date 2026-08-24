"""Optional HTTP client for the Flask adapter.

The desktop launcher uses the in-process local store by default. This client
remains available for API testing and explicit ``GAMES_USE_HTTP=1`` runs.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, Optional

import requests

DEFAULT_BASE = os.environ.get("GAMES_API_URL", "http://127.0.0.1:5000")
TIMEOUT = (0.30, 0.70)  # local connect/read timeouts
FAILURE_BACKOFF_SECS = 2.0
SCORE_SAVE_RETRY_DELAYS = (0.0, 0.15, 0.40)


def parse_score_response(result) -> tuple[Optional[int], Optional[str]]:
    """Validate the score API acknowledgement used by every game."""
    if not isinstance(result, dict):
        return None, "保存服务没有返回有效结果"
    if result.get("ok") is not True:
        return None, str(result.get("error") or result.get("code")
                         or "保存失败")
    row_id = result.get("id")
    if type(row_id) is not int or row_id <= 0:
        return None, "保存服务返回了无效记录编号"
    return row_id, None


class BackendClient:
    pending_saves_are_durable = False

    def __init__(self, base_url: str = DEFAULT_BASE, timeout=TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._offline_until = {"read": 0.0, "write": 0.0}
        self._last_failure_at = {"read": 0.0, "write": 0.0}
        self._thread_local = threading.local()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._pending_futures: set[Future] = set()
        self._sessions: set[requests.Session] = set()
        self._failed_score_submissions: list[dict] = []
        self._confirmed_replace_scores: dict[tuple[str, str], int] = {}
        self._save_sequence = 0
        self._retrying_request_ids: set[str] = set()
        self._closed = False

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            self._thread_local.session = session
            with self._lock:
                self._sessions.add(session)
        return session

    def _run_async(self, method, *args, _completion=None, **kwargs) -> Future:
        """Run a network operation away from pygame's render thread."""
        with self._lock:
            if self._closed:
                raise RuntimeError("BackendClient is closed")
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="games-api")
            future = self._executor.submit(method, *args, **kwargs)
            self._pending_futures.add(future)
            future.add_done_callback(
                lambda completed: self._finish_async(
                    completed, _completion))
            return future

    def _finish_async(self, future: Future, completion) -> None:
        try:
            if completion is not None:
                completion(future)
        finally:
            self._discard_future(future)

    def _discard_future(self, future: Future) -> None:
        with self._condition:
            self._pending_futures.discard(future)
            self._condition.notify_all()

    def drain(self, timeout: Optional[float] = None) -> bool:
        """Wait until work and its completion bookkeeping have finished."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._pending_futures:
                remaining = (None if deadline is None
                             else deadline - time.monotonic())
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self) -> None:
        """Close worker and HTTP resources. Safe to call more than once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        with self._lock:
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            session.close()

    def _request_allowed(self, kind: str) -> bool:
        with self._lock:
            return time.monotonic() >= self._offline_until[kind]

    def _mark_unavailable(self, kind: str) -> None:
        with self._lock:
            self._last_failure_at[kind] = time.monotonic()
            self._offline_until[kind] = max(
                self._offline_until[kind],
                self._last_failure_at[kind] + FAILURE_BACKOFF_SECS)

    def _mark_available(self, kind: str, request_started: float) -> None:
        with self._lock:
            # A request that started before a newer failure must not erase the
            # backoff established by that failure when it completes later.
            if request_started >= self._last_failure_at[kind]:
                self._offline_until[kind] = 0.0

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[dict]:
        if not self._request_allowed("read"):
            return None
        request_started = time.monotonic()
        try:
            resp = self._session().get(
                f"{self.base_url}{path}", params=params, timeout=self.timeout
            )
            if resp.ok:
                payload = resp.json()
                if not isinstance(payload, dict):
                    return None
                self._mark_available("read", request_started)
                return payload
            if resp.status_code >= 500:
                self._mark_unavailable("read")
        except (requests.RequestException, ValueError):
            self._mark_unavailable("read")
        return None

    def _post(self, path: str, payload: Dict[str, Any],
              bypass_backoff: bool = False) -> Optional[dict]:
        if not bypass_backoff and not self._request_allowed("write"):
            return None
        request_started = time.monotonic()
        try:
            resp = self._session().post(
                f"{self.base_url}{path}",
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            try:
                result = resp.json()
            except ValueError:
                result = {"ok": False, "code": "invalid_response",
                          "error": "保存服务返回了无效 JSON"}
            if not isinstance(result, dict):
                result = {"ok": False, "code": "invalid_response",
                          "error": "保存服务返回了无效结果"}
            if resp.ok:
                self._mark_available("write", request_started)
            elif resp.status_code >= 500:
                self._mark_unavailable("write")
                result["_retryable"] = True
            else:
                result["_retryable"] = False
            result["_http_status"] = resp.status_code
            return result
        except requests.RequestException:
            self._mark_unavailable("write")
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
        return r or {"game_id": game_id, "records": 0,
                     "best": 0, "avg": 0}

    def submit_score(self, game_id: str, player: str, score: int,
                     extra=None, replace: bool = False,
                     submission_id: Optional[int] = None,
                     request_id: Optional[str] = None,
                     _bypass_backoff: bool = False):
        """Submit a score.

        ``replace=True`` identifies running-total games. The current store
        keeps every completed attempt and uses ``submission_id`` to update a
        total within the same run.
        """
        payload = {"game_id": game_id, "player": player, "score": score}
        if extra is not None:
            payload["extra"] = extra
        if replace:
            payload["replace"] = True
        if submission_id is not None:
            payload["submission_id"] = submission_id
        if request_id is not None:
            payload["request_id"] = request_id
        return self._post("/api/scores", payload,
                          bypass_backoff=_bypass_backoff)

    def _submit_score_with_retries(
            self, game_id: str, player: str, score: int, extra=None,
            replace: bool = False, submission_id: Optional[int] = None,
            request_id: Optional[str] = None):
        last_result = None
        for delay in SCORE_SAVE_RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            last_result = self.submit_score(
                game_id, player, score, extra=extra, replace=replace,
                submission_id=submission_id, request_id=request_id,
                _bypass_backoff=True)
            row_id, _error = parse_score_response(last_result)
            if row_id is not None:
                return last_result
            if not self._score_failure_retryable(last_result):
                break
        return last_result

    @staticmethod
    def _score_failure_retryable(result) -> bool:
        if result is None:
            return True
        if not isinstance(result, dict):
            return False
        return bool(result.get("retryable", result.get("_retryable", False)))

    def recent(self, limit: int = 20):
        r = self._get("/api/recent", params={"limit": limit})
        return (r or {}).get("recent", [])

    # ----- non-blocking API for pygame code -------------------------------
    def health_async(self) -> Future:
        return self._run_async(self.health)

    def list_games_async(self) -> Future:
        return self._run_async(self.list_games)

    def leaderboard_async(self, game_id: str, limit: int = 10) -> Future:
        return self._run_async(self.leaderboard, game_id, limit)

    def recent_async(self, limit: int = 20) -> Future:
        return self._run_async(self.recent, limit)

    def submit_score_async(self, game_id: str, player: str, score: int,
                           extra=None, replace: bool = False,
                           submission_id: Optional[int] = None) -> Future:
        return self._run_async(
            self.submit_score, game_id, player, score, extra=extra,
            replace=replace, submission_id=submission_id)

    def submit_score_reliable_async(
            self, game_id: str, player: str, score: int,
            extra=None, replace: bool = False,
            submission_id: Optional[int] = None,
            request_id: Optional[str] = None) -> Future:
        request_id = request_id or uuid.uuid4().hex
        payload = {"game_id": game_id, "player": player, "score": score,
                   "extra": extra, "replace": replace,
                   "submission_id": submission_id,
                   "request_id": request_id}
        with self._lock:
            self._save_sequence += 1
            save_record = {"token": self._save_sequence, "payload": payload}
        return self._run_async(
            self._submit_score_with_retries, game_id, player, score,
            extra=extra, replace=replace, submission_id=submission_id,
            request_id=payload["request_id"],
            _completion=lambda completed: self._capture_score_save(
                completed, save_record))

    def _capture_score_save(self, future: Future, save_record: dict) -> None:
        payload = save_record["payload"]
        try:
            result = future.result()
        except Exception:  # noqa: BLE001
            result = None
        row_id, _error = parse_score_response(result)
        with self._lock:
            profile_key = (payload["game_id"], payload["player"])
            request_id = payload.get(
                "request_id", f"legacy-save-{save_record['token']:016d}")
            payload["request_id"] = request_id
            self._retrying_request_ids.discard(request_id)
            self._failed_score_submissions = [
                item for item in self._failed_score_submissions
                if item["payload"]["request_id"] != request_id]
            if row_id is None:
                confirmed = self._confirmed_replace_scores.get(profile_key)
                superseded = (payload["replace"] and confirmed is not None
                              and confirmed >= payload["score"])
                if (not superseded
                        and self._score_failure_retryable(result)):
                    self._failed_score_submissions.append(save_record)
            elif payload["replace"]:
                self._confirmed_replace_scores[profile_key] = max(
                    payload["score"],
                    self._confirmed_replace_scores.get(profile_key, -1))
                confirmed = self._confirmed_replace_scores[profile_key]
                self._failed_score_submissions = [
                    item for item in self._failed_score_submissions
                    if not (item["payload"]["replace"]
                            and (item["payload"]["game_id"],
                                 item["payload"]["player"]) == profile_key
                            and item["payload"]["score"] <= confirmed)]

    def failed_save_count(self) -> int:
        with self._lock:
            return len(self._failed_score_submissions)

    def retry_failed_saves(self) -> int:
        with self._lock:
            failed = [item for item in self._failed_score_submissions
                      if item["payload"]["request_id"]
                      not in self._retrying_request_ids]
            for item in failed:
                self._retrying_request_ids.add(
                    item["payload"]["request_id"])
        scheduled = 0
        for item in failed:
            try:
                self.submit_score_reliable_async(**item["payload"])
            except Exception:  # noqa: BLE001 - retain item for next retry
                with self._lock:
                    self._retrying_request_ids.discard(
                        item["payload"]["request_id"])
            else:
                scheduled += 1
        return scheduled
