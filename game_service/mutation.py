"""Canonical score mutation used by SQLite, spool, and HTTP adapters."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Optional

from .catalog import GAME_BY_ID, VALID_GAME_IDS
from .profile import ProfileIdentity, ProfileIdentityError

MAX_EXTRA_BYTES = 8 * 1024
MAX_SCORE = 2_147_483_647
MAX_SQLITE_INTEGER = 2**63 - 1
ATTEMPT_STATUSES = frozenset({"completed", "practice"})


class MutationError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def canonical_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MutationError(
            "invalid_extra", "extra must contain valid JSON values") from exc


def _identifier(value, field: str, *, minimum: int = 1,
                maximum: int = 64, default: Optional[str] = None) -> str:
    if value is None:
        value = default
    if not isinstance(value, str):
        raise MutationError(f"invalid_{field}", f"{field} must be a string")
    value = unicodedata.normalize("NFC", value).strip()
    if not minimum <= len(value) <= maximum:
        raise MutationError(
            f"invalid_{field}",
            f"{field} must contain {minimum}-{maximum} characters")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise MutationError(
            f"invalid_{field}", f"{field} contains control characters")
    return value


def _transport_id(value: Optional[str], field: str) -> tuple[str, bool]:
    provided = value is not None
    if value is None:
        value = uuid.uuid4().hex
    if (not isinstance(value, str)
            or not 16 <= len(value) <= 64
            or not all(ch.isascii() and (ch.isalnum() or ch in "-_")
                       for ch in value)):
        raise MutationError(
            f"invalid_{field}",
            f"{field} must be 16-64 ASCII letters, digits, - or _")
    return value, provided


def _profile_id(value: Optional[str], player: str) -> str:
    try:
        return ProfileIdentity.resolve(value, player).profile_id
    except ProfileIdentityError as exc:
        raise MutationError("invalid_profile_id", str(exc)) from exc


@dataclass(frozen=True)
class ScoreMutation:
    game_id: str
    player: str
    score: int
    extra: Optional[dict]
    extra_json: Optional[str]
    replace: bool
    submission_id: Optional[int]
    request_id: str
    attempt_uuid: str
    attempt_uuid_provided: bool
    revision: int
    revision_provided: bool
    profile_id: str
    mode: str
    ruleset_version: str
    status: str

    def semantic_payload(self) -> dict:
        return {
            "game_id": self.game_id,
            "player": self.player,
            "score": self.score,
            "extra": self.extra,
            "replace": self.replace,
            "submission_id": self.submission_id,
            "attempt_uuid": self.attempt_uuid,
            "revision": self.revision,
            "profile_id": self.profile_id,
            "mode": self.mode,
            "ruleset_version": self.ruleset_version,
            "status": self.status,
        }

    def transport_payload(self) -> dict:
        return {**self.semantic_payload(), "request_id": self.request_id}

    @property
    def payload_hash(self) -> str:
        encoded = canonical_json(self.semantic_payload()).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def normalize_score_mutation(
        game_id: str, player: str, score: int, extra=None,
        replace: bool = False, submission_id: Optional[int] = None,
        request_id: Optional[str] = None,
        attempt_uuid: Optional[str] = None, revision: Optional[int] = None,
        profile_id: Optional[str] = None, mode: str = "classic",
        ruleset_version: Optional[str] = None,
        status: str = "completed") -> ScoreMutation:
    if not isinstance(game_id, str):
        raise MutationError("invalid_game_id", "game_id must be a string")
    game_id = game_id.strip()
    if game_id not in VALID_GAME_IDS:
        raise MutationError("unknown_game", f"unknown game_id: {game_id}", 404)

    if isinstance(player, str) and not player.strip():
        player = "anonymous"
    player = unicodedata.normalize(
        "NFC", _identifier(player, "player", maximum=32, default="anonymous"))
    profile_id = _profile_id(profile_id, player)
    if type(score) is not int or not 0 <= score <= MAX_SCORE:
        raise MutationError(
            "invalid_score", f"score must be an integer between 0 and {MAX_SCORE}")
    if extra is not None and not isinstance(extra, dict):
        raise MutationError("invalid_extra", "extra must be an object or null")
    extra_json = canonical_json(extra) if extra is not None else None
    if extra_json is not None and len(extra_json.encode("utf-8")) > MAX_EXTRA_BYTES:
        raise MutationError("extra_too_large", "extra exceeds 8 KiB")
    normalized_extra = json.loads(extra_json) if extra_json is not None else None
    if not isinstance(replace, bool):
        raise MutationError("invalid_replace", "replace must be boolean")
    if (submission_id is not None
            and (type(submission_id) is not int or submission_id <= 0
                 or submission_id > MAX_SQLITE_INTEGER)):
        raise MutationError(
            "invalid_submission_id",
            "submission_id must be a positive SQLite integer")
    request_id, _ = _transport_id(request_id, "request_id")
    attempt_uuid_provided = attempt_uuid is not None
    if attempt_uuid is None:
        # Older API callers only have a stable request id. Deriving the
        # attempt identity keeps their retries idempotent without weakening
        # the explicit attempt_uuid contract used by current games.
        attempt_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL, f"classic-games-attempt:{request_id}").hex
    attempt_uuid, _ = _transport_id(attempt_uuid, "attempt_uuid")
    revision_provided = revision is not None
    revision = 1 if revision is None else revision
    if (type(revision) is not int or revision <= 0
            or revision > MAX_SQLITE_INTEGER):
        raise MutationError(
            "invalid_revision", "revision must be a positive SQLite integer")
    mode = _identifier(mode, "mode", maximum=32, default="classic")
    ruleset_version = _identifier(
        ruleset_version, "ruleset_version", maximum=32,
        default=GAME_BY_ID[game_id].ruleset_version)
    status = _identifier(status, "status", maximum=16, default="completed")
    if status not in ATTEMPT_STATUSES:
        raise MutationError(
            "invalid_status", f"status must be one of: {', '.join(sorted(ATTEMPT_STATUSES))}")
    if not math.isfinite(float(score)):
        raise MutationError("invalid_score", "score must be finite")

    return ScoreMutation(
        game_id=game_id, player=player, score=score,
        extra=normalized_extra, extra_json=extra_json, replace=replace,
        submission_id=submission_id, request_id=request_id,
        attempt_uuid=attempt_uuid,
        attempt_uuid_provided=attempt_uuid_provided,
        revision=revision, revision_provided=revision_provided,
        profile_id=profile_id, mode=mode,
        ruleset_version=ruleset_version, status=status)
