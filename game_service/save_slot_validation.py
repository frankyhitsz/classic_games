"""Pure, pygame-free validators for game-owned save-slot payloads."""

from __future__ import annotations

from .mutation import MAX_SCORE
from .store import StoreError


def validate_2048_state(state) -> dict:
    if not isinstance(state, dict) or state.get("version") not in {1, 2, 3, 4, 5}:
        raise StoreError("invalid_2048_slot", "unsupported 2048 save version")
    version = state["version"]
    score = state.get("score")
    grid = state.get("grid")
    game_state = state.get("game_state", "playing")
    if (type(score) is not int or not 0 <= score <= MAX_SCORE
            or not isinstance(grid, list) or len(grid) != 4
            or any(not isinstance(row, list) or len(row) != 4 for row in grid)):
        raise StoreError("invalid_2048_slot", "invalid 2048 board shape")
    flat = [value for row in grid for value in row]
    if (any(type(value) is not int or value < 0 or value == 1
            or value > (1 << 30) or (value and value & (value - 1))
            for value in flat)
            or not any(flat)
            or game_state not in {"playing", "won", "gameover"}
            or (game_state == "won" and not bool(state.get("won")))
            or bool(state.get("won")) != (max(flat, default=0) >= 2048)):
        raise StoreError("invalid_2048_slot", "invalid 2048 board semantics")
    if version >= 2:
        attempt_uuid = state.get("attempt_uuid")
        revision = state.get("attempt_revision", state.get("revision"))
        slot_revision = state.get("slot_revision", 0)
        confirmed_score = state.get("confirmed_score")
        if (not isinstance(attempt_uuid, str)
                or not 16 <= len(attempt_uuid) <= 64
                or any(not char.isascii()
                       or (not char.isalnum() and char not in "-_")
                       for char in attempt_uuid)
                or type(revision) is not int or revision < 0
                or type(slot_revision) is not int
                or not 0 <= slot_revision <= (1 << 63) - 1
                or (confirmed_score is not None
                    and (type(confirmed_score) is not int
                         or not 0 <= confirmed_score <= score))
                or type(state.get("won_announced")) is not bool
                or (state["won_announced"] and not bool(state.get("won")))):
            raise StoreError("invalid_2048_slot", "invalid 2048 attempt state")
    if version >= 4:
        owner = state.get("owner_token")
        status = state.get("owner_status")
        epoch = state.get("owner_epoch", 0)
        if (not isinstance(owner, str) or not 16 <= len(owner) <= 64
                or status not in {"active", "released"}
                or type(epoch) is not int or not 0 <= epoch <= (1 << 63) - 1):
            raise StoreError("invalid_2048_slot", "invalid 2048 owner")
    if version == 5:
        expected_owner = state.get("expected_owner_token")
        expected_epoch = state.get("expected_owner_epoch")
        expected_revision = state.get("expected_slot_revision")
        expected_hash = state.get("expected_value_hash")
        if ((expected_owner is not None
             and (not isinstance(expected_owner, str)
                  or not 16 <= len(expected_owner) <= 64))
                or (expected_epoch is not None
                    and (type(expected_epoch) is not int
                         or not 0 <= expected_epoch <= (1 << 63) - 1))
                or (expected_revision is not None
                    and (type(expected_revision) is not int
                         or not 0 <= expected_revision <= (1 << 63) - 1))
                or (expected_hash is not None
                    and (not isinstance(expected_hash, str)
                         or len(expected_hash) != 64
                         or any(char not in "0123456789abcdef"
                                for char in expected_hash)))):
            raise StoreError("invalid_2048_slot", "invalid 2048 expectation")
    return state


def validate_save_slot_payload(game_id: str, state) -> object:
    if game_id == "2048":
        return validate_2048_state(state)
    return state
