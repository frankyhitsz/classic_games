"""Validated, game-specific policies for persistent campaign progress."""

from __future__ import annotations

from copy import deepcopy

from .mutation import MAX_SCORE

SOKOBAN_LEVELS = 16
ZUMA_LEVELS = 5


class ProgressPolicyError(ValueError):
    pass


def _bounded_int(value, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProgressPolicyError(
            f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _level_set(value, field: str, level_count: int) -> list[int]:
    if (not isinstance(value, list)
            or any(type(item) is not int or not 0 <= item < level_count
                   for item in value)):
        raise ProgressPolicyError(
            f"{field} must be a list of valid zero-based level indexes")
    return sorted(set(value))


def _level_metric(value, field: str, level_count: int,
                  maximum: int = MAX_SCORE) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ProgressPolicyError(f"{field} must be an object")
    result = {}
    for raw_key, raw_value in value.items():
        if (not isinstance(raw_key, str) or not raw_key.isdigit()
                or not 0 <= int(raw_key) < level_count):
            raise ProgressPolicyError(f"{field} has an invalid level key")
        result[str(int(raw_key))] = _bounded_int(
            raw_value, f"{field}.{raw_key}", 0, maximum)
    return result


def validate_progress(game_id: str, key: str, value) -> dict:
    if not isinstance(value, dict):
        raise ProgressPolicyError("progress must be an object")
    if game_id == "sokoban":
        if key not in {"campaign", "practice"}:
            raise ProgressPolicyError("unknown Sokoban progress key")
        allowed = {
            "unlocked_level", "completed_levels", "level_scores",
            "best_moves", "best_pushes",
        }
        if not set(value) <= allowed:
            raise ProgressPolicyError("Sokoban progress has unknown fields")
        result = {}
        if "unlocked_level" in value:
            result["unlocked_level"] = _bounded_int(
                value["unlocked_level"], "unlocked_level", 1, SOKOBAN_LEVELS)
        if "completed_levels" in value:
            result["completed_levels"] = _level_set(
                value["completed_levels"], "completed_levels", SOKOBAN_LEVELS)
        if "level_scores" in value:
            result["level_scores"] = _level_metric(
                value["level_scores"], "level_scores", SOKOBAN_LEVELS)
        for field in ("best_moves", "best_pushes"):
            if field in value:
                result[field] = _level_metric(
                    value[field], field, SOKOBAN_LEVELS)
        return result
    if game_id == "zuma":
        if key != "campaign":
            raise ProgressPolicyError("unknown Zuma progress key")
        allowed = {"unlocked_level", "highest_score", "completed_all"}
        if not set(value) <= allowed:
            raise ProgressPolicyError("Zuma progress has unknown fields")
        result = {}
        if "unlocked_level" in value:
            result["unlocked_level"] = _bounded_int(
                value["unlocked_level"], "unlocked_level", 1, ZUMA_LEVELS)
        if "highest_score" in value:
            result["highest_score"] = _bounded_int(
                value["highest_score"], "highest_score", 0, MAX_SCORE)
        if "completed_all" in value:
            if type(value["completed_all"]) is not bool:
                raise ProgressPolicyError("completed_all must be boolean")
            result["completed_all"] = value["completed_all"]
        return result
    raise ProgressPolicyError(f"progress is not supported for game: {game_id}")


def merge_progress(game_id: str, key: str, existing, incoming) -> dict:
    old = validate_progress(game_id, key, existing or {})
    new = validate_progress(game_id, key, incoming)
    result = deepcopy(old)
    if game_id == "sokoban":
        if "unlocked_level" in new:
            result["unlocked_level"] = max(
                result.get("unlocked_level", 1), new["unlocked_level"])
        if "completed_levels" in new:
            result["completed_levels"] = sorted(
                set(result.get("completed_levels", []))
                | set(new["completed_levels"]))
        for field in ("level_scores", "best_moves", "best_pushes"):
            if field not in new:
                continue
            merged = dict(result.get(field, {}))
            for level, metric in new[field].items():
                if field in {"best_moves", "best_pushes"}:
                    merged[level] = min(merged.get(level, metric), metric)
                else:
                    merged[level] = max(merged.get(level, 0), metric)
            result[field] = merged
        return validate_progress(game_id, key, result)
    if "unlocked_level" in new:
        result["unlocked_level"] = max(
            result.get("unlocked_level", 1), new["unlocked_level"])
    if "highest_score" in new:
        result["highest_score"] = max(
            result.get("highest_score", 0), new["highest_score"])
    if "completed_all" in new:
        result["completed_all"] = bool(
            result.get("completed_all", False) or new["completed_all"])
    return validate_progress(game_id, key, result)
