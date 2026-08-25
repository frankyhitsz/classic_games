"""2048 — sliding-tile number merger with smooth animations.

Each tile is a ``Tile`` object that owns its current value, its pre-move
``from_row``/``from_col`` (for slide animation), and short-lived state
for spawn / merge-pop animations. Every move runs as:

    _move(direction)
        → compute new logical positions, mark merges, set self.anim_t = 0
    update(dt)
        → advance anim_t; when anim_t reaches 1.0, apply merges (double
          target value, remove dead "source" tiles) and spawn a new tile
        → after the move settles, transition to "won" / "gameover" if
          applicable (so the overlay appears AFTER the slide, not on top
          of a frozen board)

Controls:
  ←↑→↓ / WASD   slide
  R             restart
  P             pause
  Esc           return to launcher
"""
from __future__ import annotations

import hashlib
import random
import uuid
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

import pygame

from client.common.ui import (COLORS, SAVE_PENDING, SAVE_SAVING, BaseGame, Button,
                              draw_gradient_bg, draw_text, ease_out_back,
                              ease_out_cubic)
from game_service.mutation import MAX_SCORE, canonical_json
from game_service.save_slot_validation import validate_2048_state
from game_service.store import StoreError
from game_service.service import (GameDataService, SaveState, SlotLoadResult,
                                  SlotLoadStatus)

GRID = 4
TILE = 90
GAP = 12
BOARD_SIZE = GRID * TILE + (GRID + 1) * GAP
WIDTH = BOARD_SIZE + 40
HEIGHT = BOARD_SIZE + 120
BOARD_X = 20
BOARD_Y = 100
BOARD_COLOR = (211, 223, 242)
EMPTY_CELL_COLOR = (240, 245, 252)

ANIM_DURATION = 0.13   # slide
SPAWN_DURATION = 0.18  # scale-up for newly spawned tile
MERGE_DURATION = 0.22  # pop after a merge
SLOT_LOAD_TIMEOUT_SECONDS = 8.0
AUTOSAVE_DEBOUNCE_MS = 150
AUTOSAVE_MAX_DIRTY_MS = 1500


def tile_color(value: int):
    """Returns (bg, fg) for a tile value."""
    table = {
        0: (EMPTY_CELL_COLOR, COLORS["text"]),
        2: ((235, 228, 215), (60, 58, 50)),
        4: ((237, 218, 178), (60, 58, 50)),
        8: ((242, 167, 100), (250, 248, 240)),
        16: ((245, 130, 70), (250, 248, 240)),
        32: ((243, 100, 75), (250, 248, 240)),
        64: ((240, 75, 55), (250, 248, 240)),
        128: ((237, 200, 90), (250, 248, 240)),
        256: ((237, 190, 70), (250, 248, 240)),
        512: ((237, 175, 55), (250, 248, 240)),
        1024: ((225, 130, 50), (250, 248, 240)),
        2048: ((180, 60, 50), (250, 248, 240)),
        4096: ((100, 50, 150), (250, 248, 240)),
        8192: ((50, 60, 150), (250, 248, 240)),
    }
    return table.get(value, ((30, 30, 30), (250, 248, 240)))


@dataclass
class Tile:
    value: int                       # currently displayed value
    target_value: int = 0            # value to apply after slide (post-merge)
    row: int = 0                     # logical position (post-move)
    col: int = 0
    from_row: int = 0                # slide start
    from_col: int = 0
    spawn_progress: float = 1.0      # 1.0 = fully grown
    merge_pop: float = 0.0           # 1.0 → 0.0 decaying pulse
    dead: bool = False               # marked for removal after slide

    def __post_init__(self):
        if self.target_value == 0:
            self.target_value = self.value
        self.from_row = self.row
        self.from_col = self.col


@dataclass
class _LineSlot:
    """Intermediate result during a single line's slide computation."""
    tile: Tile                       # surviving tile
    source: Optional[Tile] = None   # tile that merges into ``tile`` (and dies)


class Game2048(BaseGame):
    game_id = "2048"
    title = "2048"
    submit_replaces_existing = True

    def __init__(self, backend: Optional[GameDataService] = None,
                 player: str = "anonymous",
                 profile_id: Optional[str] = None):
        super().__init__(WIDTH, HEIGHT, fps=60, backend=backend, player=player,
                         profile_id=profile_id)
        self._slot_load_future = None
        self._slot_save_future = None
        self._slot_quarantine_future = None
        self.slot_load_state = "ready"
        self.slot_load_error: Optional[str] = None
        self.slot_save_error: Optional[str] = None
        self._slot_save_pending = False
        self._slot_revision = 0
        self._slot_owner_token = uuid.uuid4().hex
        self._slot_owner_epoch = 0
        self._slot_expected_owner_token: Optional[str] = None
        self._slot_expected_owner_epoch: Optional[int] = None
        self._slot_expected_revision: Optional[int] = None
        self._slot_expected_value_hash: Optional[str] = None
        self._slot_claim_revision: Optional[int] = None
        self._slot_claim_value_hash: Optional[str] = None
        self._slot_conflict_saved = None
        self._slot_conflict_owner: Optional[str] = None
        self._takeover_reload_expected = None
        self._allow_slot_takeover = False
        self._new_game_confirm_deadline = 0
        self._slot_load_started_at = 0.0
        self._autosave_queued = False
        self._autosave_due_at = 0
        self._autosave_dirty_since = 0
        self._autosave_release_queued = False
        self._initializing_board = True
        self.reset()
        self._initializing_board = False
        self._begin_slot_load()

    def _begin_slot_load(self) -> None:
        ensure_and_load = getattr(
            self.backend, "ensure_profile_and_load_slot_async", None)
        load_slot = getattr(self.backend, "load_slot_async", None)
        try:
            if callable(ensure_and_load):
                self._slot_load_future = ensure_and_load(
                    self.player, self.profile_id, self.game_id, "autosave")
            elif callable(load_slot):
                self._slot_load_future = load_slot(
                    self.profile_id, self.game_id, "autosave")
            else:
                self._slot_load_future = None
        except Exception as exc:  # noqa: BLE001
            self._slot_load_future = None
            self.slot_load_state = "failed"
            self.slot_load_error = f"自动存档读取失败：{exc}"
            return
        if self._slot_load_future is not None:
            self.slot_load_state = "loading"
            self.slot_load_error = None
            self._slot_load_started_at = pygame.time.get_ticks() / 1000.0

    def _retry_slot_load(self) -> None:
        if self._slot_load_future is None:
            self._begin_slot_load()

    def _confirm_new_game_after_load_failure(self) -> None:
        now = pygame.time.get_ticks()
        if now > self._new_game_confirm_deadline:
            self._new_game_confirm_deadline = now + 4000
            self.slot_load_error = "再按一次 N 确认新开一局；原存档不会被静默覆盖"
            return
        self._new_game_confirm_deadline = 0
        self.slot_load_state = "ready"
        self.slot_load_error = None
        self.reset()

    # ------------------------------------------------------------------
    def reset(self):
        if not getattr(self, "_initializing_board", False):
            self._slot_load_future = None
            self.slot_load_state = "ready"
        self._detach_queued_score_submission()
        self.begin_score_session()
        self.tiles: List[Tile] = []
        self.grid: List[List[Optional[Tile]]] = [
            [None] * GRID for _ in range(GRID)]
        self.score = 0
        self.won = False
        self.score_submitted = False
        self.score_submission_id: Optional[int] = None
        self.submitted_score: Optional[int] = None
        self.submitted_extra = None
        self._queued_score_submission = None
        self.state = "playing"
        self.overlay_buttons = []
        self.anim_t = 1.0  # 1.0 = no animation in progress
        # Retain a short ordered burst during the slide animation.
        self._queued_directions: deque[str] = deque(maxlen=2)
        self._won_announced = False  # only pop the win overlay once
        self._swipe_start: Optional[tuple] = None  # (x, y) on MOUSEBUTTONDOWN
        self._spawn_tile()
        self._spawn_tile()
        if not getattr(self, "_initializing_board", False):
            self._save_autosave_slot()

    # ------------------------------------------------------------------
    def _spawn_tile(self) -> bool:
        empties = [(i, j) for i in range(GRID) for j in range(GRID)
                   if self.grid[i][j] is None]
        if not empties:
            return False
        i, j = random.choice(empties)
        v = 2 if random.random() < 0.9 else 4
        t = Tile(value=v, row=i, col=j)
        t.spawn_progress = 0.0
        self.tiles.append(t)
        self.grid[i][j] = t
        return True

    def _max_tile(self) -> int:
        return max((t.value for t in self.tiles), default=0)

    def _can_move(self) -> bool:
        for r in range(GRID):
            for c in range(GRID):
                if self.grid[r][c] is None:
                    return True
        for r in range(GRID):
            for c in range(GRID):
                t = self.grid[r][c]
                if t is None:
                    continue
                if c + 1 < GRID:
                    n = self.grid[r][c + 1]
                    if n is not None and n.value == t.value:
                        return True
                if r + 1 < GRID:
                    n = self.grid[r + 1][c]
                    if n is not None and n.value == t.value:
                        return True
        return False

    # ------------------------------------------------------------------
    def _process_line(self, line_tiles: List[Tile]) -> List[_LineSlot]:
        """Slide one line of tiles toward index 0. Returns _LineSlots
        in destination order (slot 0 = closest to destination)."""
        slots: List[_LineSlot] = []
        for t in line_tiles:
            if slots and slots[-1].source is None \
                    and slots[-1].tile.value == t.value:
                slots[-1].source = t
            else:
                slots.append(_LineSlot(tile=t))
        return slots

    def _move(self, direction: str) -> bool:
        if self.state not in ("playing",):
            return False
        if direction not in {"left", "right", "up", "down"}:
            raise ValueError(f"unknown 2048 direction: {direction}")
        # Refuse input mid-slide so the animation can finish cleanly.
        if self.anim_t < 1.0:
            if (len(self._queued_directions) < self._queued_directions.maxlen
                    and (not self._queued_directions
                         or self._queued_directions[-1] != direction)):
                self._queued_directions.append(direction)
            return False

        # Reset per-move animation state.
        for t in self.tiles:
            t.from_row = t.row
            t.from_col = t.col
            t.dead = False

        # Build per-line traversal.
        # Each line is (axis, fixed_index, direction, [tiles in slide order]).
        # The tiles list is reordered so the FIRST tile is the one that
        # moves the FARTHEST in the slide direction (i.e. closest to the
        # destination edge). _process_line then packs them into slots
        # 0, 1, 2, ... starting from the destination edge.
        lines = []
        if direction in ("left", "right"):
            for r in range(GRID):
                tiles = [self.grid[r][c] for c in range(GRID)]
                tiles = [t for t in tiles if t is not None]
                if direction == "right":
                    tiles.reverse()
                lines.append(("row", r, direction, tiles))
        else:  # up / down
            for c in range(GRID):
                tiles = [self.grid[r][c] for r in range(GRID)]
                tiles = [t for t in tiles if t is not None]
                if direction == "down":
                    tiles.reverse()
                lines.append(("col", c, direction, tiles))

        # Clear grid; we'll repopulate from computed slots.
        self.grid = [[None] * GRID for _ in range(GRID)]
        moved = False
        for axis, fixed, dirn, tiles in lines:
            slots = self._process_line(tiles)
            for dest_idx, slot in enumerate(slots):
                # Slot 0 = closest to destination edge. Map back to a real
                # grid row/col depending on slide direction. For "left"/"up"
                # dest_idx is also the final grid index. For "right"/"down"
                # the destination edge is the FAR side, so a slot at index 0
                # lands at GRID-1, index 1 at GRID-2, etc.
                if axis == "row":
                    row = fixed
                    col = dest_idx if dirn == "left" else (GRID - 1 - dest_idx)
                else:
                    col = fixed
                    row = dest_idx if dirn == "up" else (GRID - 1 - dest_idx)
                slot.tile.row = row
                slot.tile.col = col
                self.grid[row][col] = slot.tile
                if slot.source is not None:
                    slot.source.row = row
                    slot.source.col = col
                    slot.source.dead = True
                # Merge accounting: target doubles at end of slide.
                if slot.source is not None:
                    slot.tile.target_value = slot.tile.value * 2
                    self.score += slot.tile.target_value
                    if slot.tile.target_value == 2048:
                        self.won = True
            # Detect any movement on this line.
            for t in tiles:
                if (t.row, t.col) != (t.from_row, t.from_col):
                    moved = True

        if not moved:
            # A win takes precedence over game-over so the player can see
            # the 2048 celebration first.  If they then continue on an
            # already-locked board, the first attempted move must still
            # transition to game-over instead of leaving the UI stuck.
            if not self._can_move():
                self.state = "gameover"
                self._queued_directions.clear()
                self.extra = {"max_tile": self._max_tile(), "won": self.won}
                self._save_autosave_slot()
                self._submit_score(extra=self.extra)
            return False

        # Begin animation; spawn + state transition happen when it ends.
        self.anim_t = 0.0
        return True

    # ------------------------------------------------------------------
    def update(self, dt: float) -> None:
        # Animations keep progressing in any state — see ``update_overlay``.
        self._poll_slot_load()
        self._poll_slot_save()
        self._poll_slot_quarantine()
        self._flush_autosave_if_due()
        self._tick_animations(dt)

    def update_overlay(self, dt: float) -> None:
        self._poll_slot_load()
        self._poll_slot_save()
        self._poll_slot_quarantine()
        self._flush_autosave_if_due()
        # A real pause must freeze the in-flight move. Previously the slide
        # finalized behind the pause overlay while tile spawning was skipped
        # because state != "playing", effectively granting a free move.
        if self.state != "paused":
            self._tick_animations(dt)

    def _tick_animations(self, dt: float) -> None:
        self._poll_score_submission()
        prev_t = self.anim_t
        if self.anim_t < 1.0:
            self.anim_t = min(1.0, self.anim_t + dt / ANIM_DURATION)

        # When the slide finishes, finalize merges and spawn a new tile.
        if prev_t < 1.0 and self.anim_t >= 1.0:
            for t in self.tiles[:]:
                if t.dead:
                    if t in self.tiles:
                        self.tiles.remove(t)
                elif t.target_value != t.value:
                    t.value = t.target_value
                    t.merge_pop = 1.0
            # Only spawn after a real move while still playing.
            if self.state == "playing":
                self._spawn_tile()
                self._queue_autosave_slot()

            # Now that the board has settled (post-merge, post-spawn),
            # decide whether this move should trigger a state change.
            pending = None
            if self.state == "playing":
                if self.won and not self._won_announced:
                    pending = "won"
                elif not self._can_move():
                    pending = "gameover"

            if pending == "won":
                self._won_announced = True
                self.state = "won"
                self._queued_directions.clear()
                self._submit_score(extra={"won": True,
                                          "max_tile": self._max_tile()})
            elif pending == "gameover":
                self.state = "gameover"
                self._queued_directions.clear()
                self.extra = {"max_tile": self._max_tile(), "won": self.won}
                self._submit_score(extra=self.extra)

            if pending is not None:
                self._save_autosave_slot()

            # Discard no-op commands immediately. Otherwise a blocked command
            # can leave a later direction queued until an unrelated future
            # move, producing a surprising delayed slide.
            while self._queued_directions and self.state == "playing":
                if self._move(self._queued_directions.popleft()):
                    break

        # Decay pop / grow spawn timers.
        for t in self.tiles:
            if t.spawn_progress < 1.0:
                t.spawn_progress = min(1.0, t.spawn_progress + dt / SPAWN_DURATION)
            if t.merge_pop > 0:
                t.merge_pop = max(0.0, t.merge_pop - dt / MERGE_DURATION)

    def _save_autosave_slot(self, owner_status: str = "active",
                            *, allow_claim: bool = False) -> None:
        if (self.slot_load_state == "claiming" and not allow_claim
                and self._slot_save_future is not None
                and self._slot_save_future.done()):
            had_queued_save = self._autosave_queued
            self._poll_slot_save()
            if had_queued_save or self.slot_load_state != "ready":
                return
        elif self.slot_load_state == "claiming" and not allow_claim:
            self._autosave_queued = True
            self._autosave_release_queued |= owner_status == "released"
            if not self._autosave_dirty_since:
                self._autosave_dirty_since = pygame.time.get_ticks()
            return
        if (self.slot_load_state != "ready"
                and not (allow_claim and self.slot_load_state == "claiming")):
            return
        save_slot = getattr(self.backend, "save_slot_async", None)
        if not callable(save_slot):
            return
        if (self._slot_save_future is not None
                and not self._slot_save_future.done()):
            self._autosave_queued = True
            self._autosave_release_queued |= owner_status == "released"
            if not self._autosave_dirty_since:
                self._autosave_dirty_since = pygame.time.get_ticks()
            return
        if self._slot_save_future is not None:
            self._poll_slot_save()
            if self._slot_save_future is not None:
                self._autosave_queued = True
                self._autosave_release_queued |= owner_status == "released"
                return
        self._autosave_queued = False
        self._autosave_dirty_since = 0
        self._autosave_release_queued = False
        state = self._build_autosave_state(owner_status)
        if self.slot_load_state == "claiming":
            self._slot_claim_revision = state["slot_revision"]
            claimed_value = {
                "state": state,
                "state_version": state["version"],
                "ruleset_version": self.attempt_context.ruleset_version,
            }
            self._slot_claim_value_hash = hashlib.sha256(
                canonical_json(claimed_value).encode("utf-8")).hexdigest()
        try:
            self._slot_save_future = save_slot(
                self.profile_id, self.game_id, "autosave", state,
                self.attempt_context.ruleset_version)
        except Exception:  # noqa: BLE001 - score play remains available
            self._slot_save_future = None
            self.slot_save_error = "自动存档暂时未保存"
            if self.slot_load_state == "claiming":
                self.slot_load_state = "failed"
                self.slot_load_error = "自动存档占用权确认失败，请重试"

    def _build_autosave_state(self, owner_status: str) -> dict:
        self._slot_revision += 1
        return {
            "version": 5,
            "game_state": self.state,
            "score": self.score,
            "won": self.won,
            "won_announced": self._won_announced,
            "attempt_uuid": self._score_attempt_uuid,
            "attempt_revision": self.attempt_context.revision,
            "slot_revision": self._slot_revision,
            "confirmed_score": self.submitted_score,
            "owner_token": self._slot_owner_token,
            "owner_status": owner_status,
            "owner_epoch": self._slot_owner_epoch,
            "expected_owner_token": self._slot_expected_owner_token,
            "expected_owner_epoch": self._slot_expected_owner_epoch,
            "expected_slot_revision": self._slot_expected_revision,
            "expected_value_hash": self._slot_expected_value_hash,
            "grid": [[self.grid[row][col].value
                      if self.grid[row][col] is not None else 0
                      for col in range(GRID)] for row in range(GRID)],
        }

    def _queue_autosave_slot(self) -> None:
        """Coalesce rapid settled moves into one durable latest-value write."""
        now = pygame.time.get_ticks()
        if not self._autosave_queued:
            self._autosave_dirty_since = now
        self._autosave_queued = True
        self._autosave_due_at = now + AUTOSAVE_DEBOUNCE_MS

    def _flush_autosave_if_due(self) -> None:
        now = pygame.time.get_ticks()
        if (self._autosave_queued
                and (now >= self._autosave_due_at
                     or (self._autosave_dirty_since
                         and now - self._autosave_dirty_since
                         >= AUTOSAVE_MAX_DIRTY_MS))):
            self._save_autosave_slot()

    def _clear_slot_claim_expectations(self) -> None:
        self._slot_expected_owner_token = None
        self._slot_expected_owner_epoch = None
        self._slot_expected_revision = None
        self._slot_expected_value_hash = None
        self._slot_claim_revision = None
        self._slot_claim_value_hash = None

    def _claim_result_is_authoritative(self, result) -> bool:
        if (not isinstance(result, dict)
                or result.get("ok") is not True
                or result.get("superseded") is True
                or result.get("state_apply") == "superseded"):
            return False
        value = result.get("value")
        return (isinstance(value, dict)
                and value.get("owner_status") == "active"
                and value.get("owner_token") == self._slot_owner_token
                and value.get("owner_epoch") == self._slot_owner_epoch
                and value.get("slot_revision") == self._slot_claim_revision
                and result.get("value_hash") == self._slot_claim_value_hash)

    def _reload_after_unproven_claim(self, message: str) -> None:
        self._slot_save_pending = False
        self._allow_slot_takeover = False
        self._clear_slot_claim_expectations()
        self._begin_slot_load()
        self.slot_save_error = message

    def _poll_slot_save(self) -> None:
        self._poll_slot_save_status()
        future = self._slot_save_future
        if future is None or not future.done():
            return
        was_claiming = self.slot_load_state == "claiming"
        self._slot_save_future = None
        try:
            result = future.result()
        except Exception:  # noqa: BLE001 - keep the board playable
            self.slot_save_error = "自动存档暂时未保存"
            if was_claiming:
                self.slot_load_state = "failed"
                self.slot_load_error = "自动存档占用权确认失败，请重试"
        else:
            if isinstance(result, dict) and result.get("ok") is False:
                if result.get("durable_pending"):
                    self._slot_save_pending = True
                    self.slot_save_error = "自动存档已进入待写入队列"
                elif result.get("code") == "slot_in_use":
                    # The slot changed between the fresh load and CAS write.
                    # Stop accepting moves and reload the current owner/value
                    # before offering takeover again.
                    self._reload_after_unproven_claim(
                        "自动存档已变化，正在重新读取")
                else:
                    self._slot_save_pending = False
                    self.slot_save_error = str(
                        result.get("error") or "自动存档暂时未保存")
                    if self.slot_load_state == "claiming":
                        self.slot_load_state = "failed"
                        self.slot_load_error = "自动存档占用权确认失败，请重试"
            elif was_claiming and not self._claim_result_is_authoritative(result):
                self._reload_after_unproven_claim(
                    "自动存档占用权未确认，正在读取当前存档")
            else:
                self._slot_save_pending = False
                self.slot_save_error = None
                self._clear_slot_claim_expectations()
                if was_claiming:
                    self.slot_load_state = "ready"
        self._poll_slot_save_status()
        if self._autosave_queued and self.slot_load_state == "ready":
            owner_status = (
                "released" if self._autosave_release_queued else "active")
            self._save_autosave_slot(owner_status=owner_status)

    def _poll_slot_save_status(self) -> None:
        if not self._slot_save_pending:
            return
        getter = getattr(self.backend, "get_local_state_status", None)
        if not callable(getter):
            return
        key = f"slot:{self.profile_id}:{self.game_id}:autosave"
        event = getter(key)
        state = getattr(event, "state", None)
        if state == SaveState.COMMITTED:
            if self.slot_load_state == "claiming":
                result = getattr(event, "result", {})
                if self._claim_result_is_authoritative(result):
                    self._slot_save_pending = False
                    self.slot_save_error = None
                    self._clear_slot_claim_expectations()
                    self.slot_load_state = "ready"
                else:
                    self._reload_after_unproven_claim(
                        "自动存档占用权未确认，正在读取当前存档")
            else:
                self._slot_save_pending = False
                self.slot_save_error = None
        elif state == SaveState.SUPERSEDED:
            if self.slot_load_state == "claiming":
                self._reload_after_unproven_claim(
                    "另一个窗口先取得了自动存档，正在重新读取")
            else:
                self._slot_save_pending = False
                self.slot_save_error = "自动存档已由较新的状态替代"
        elif state == SaveState.DURABLE_PENDING:
            self.slot_save_error = "自动存档已进入待写入队列"
        elif state in (SaveState.NON_DURABLE_PENDING,
                       SaveState.RECOVERY_REQUIRED):
            self._slot_save_pending = False
            self.slot_save_error = "自动存档需要恢复后才能继续"
            if self.slot_load_state == "claiming":
                self.slot_load_state = "failed"
                self.slot_load_error = "占用权尚未安全保存，请重试或返回"
        elif state in (SaveState.PERMANENT_FAILURE, SaveState.QUARANTINED):
            self._slot_save_pending = False
            result = getattr(event, "result", {})
            self.slot_save_error = str(
                result.get("error") or "自动存档无法恢复")
            if self.slot_load_state == "claiming":
                self.slot_load_state = "failed"
                self.slot_load_error = "自动存档占用权确认失败，请重试"

    def _poll_slot_load(self) -> None:
        future = self._slot_load_future
        if future is None:
            return
        elapsed = (pygame.time.get_ticks() / 1000.0
                   - self._slot_load_started_at)
        if not future.done() and elapsed >= SLOT_LOAD_TIMEOUT_SECONDS:
            self._slot_load_future = None
            self.slot_load_state = "failed"
            self.slot_load_error = "自动存档读取超时，请重试或返回菜单"
            return
        if not future.done():
            return
        self._slot_load_future = None
        try:
            saved = future.result()
        except Exception as exc:  # noqa: BLE001
            self.slot_load_state = "failed"
            self.slot_load_error = f"自动存档读取失败：{exc}"
            return
        if isinstance(saved, SlotLoadResult):
            if saved.status == SlotLoadStatus.LOADED:
                saved = saved.slot
            elif saved.status == SlotLoadStatus.NO_SLOT:
                self.slot_load_state = "claiming"
                self.slot_load_error = None
                self._save_autosave_slot(allow_claim=True)
                return
            else:
                self.slot_load_state = "failed"
                self.slot_load_error = (
                    saved.error or "自动存档暂时无法读取，请重试")
                return
        if not isinstance(saved, dict):
            self.slot_load_state = "claiming"
            self.slot_load_error = None
            self._save_autosave_slot(allow_claim=True)
            return
        state = saved.get("state")
        grid = state.get("grid") if isinstance(state, dict) else None
        score = state.get("score") if isinstance(state, dict) else None
        version = state.get("version") if isinstance(state, dict) else None
        game_state = (state.get("game_state", "playing")
                      if isinstance(state, dict) else None)
        owner_token = (state.get("owner_token")
                       if isinstance(state, dict) else None)
        owner_status = (state.get("owner_status")
                        if isinstance(state, dict) else None)
        try:
            validate_2048_state(state)
        except StoreError:
            self._quarantine_bad_slot("invalid_2048_slot_semantics")
            return
        if version in (4, 5):
            owner_epoch = state.get("owner_epoch", 0)
            valid_owner = (
                isinstance(owner_token, str)
                and 16 <= len(owner_token) <= 64
                and owner_status in {"active", "released"}
                and type(owner_epoch) is int
                and 0 <= owner_epoch <= (1 << 63) - 1)
            if not valid_owner:
                self._quarantine_bad_slot("invalid_2048_slot_owner")
                return
            value_hash = saved.get("value_hash")
            if (version == 5
                    and (not isinstance(value_hash, str)
                         or len(value_hash) != 64
                         or any(char not in "0123456789abcdef"
                                for char in value_hash))):
                self._quarantine_bad_slot("invalid_2048_slot_identity")
                return
            if (owner_status == "active"
                    and owner_token != self._slot_owner_token):
                identity = (
                    owner_token, owner_epoch,
                    state.get("slot_revision", 0), value_hash)
                if self._takeover_reload_expected is not None:
                    if identity != self._takeover_reload_expected:
                        self._slot_conflict_saved = saved
                        self._slot_conflict_owner = owner_token
                        self._takeover_reload_expected = None
                        self.slot_load_state = "conflict"
                        self.slot_load_error = (
                            "自动存档在确认期间已变化；请检查后再次按 K 接管")
                        return
                    self._allow_slot_takeover = True
                elif not self._allow_slot_takeover:
                    self._slot_conflict_saved = saved
                    self._slot_conflict_owner = owner_token
                    self.slot_load_state = "conflict"
                    self.slot_load_error = (
                        "该自动存档仍由另一个游戏窗口使用；可接管或返回菜单")
                    return
        flat = ([value for row in grid for value in row]
                if isinstance(grid, list)
                and all(isinstance(row, list) for row in grid) else [])
        if saved.get("ruleset_version") != self.attempt_context.ruleset_version:
            self.slot_load_state = "failed"
            self.slot_load_error = (
                "自动存档来自不兼容的规则版本；可返回菜单或确认新开，"
                "原存档不会被当作损坏数据删除")
            return
        if (not isinstance(state, dict) or version not in (1, 2, 3, 4, 5)
                or type(score) is not int or not 0 <= score <= MAX_SCORE
                or not isinstance(grid, list) or len(grid) != GRID
                or any(not isinstance(row, list) or len(row) != GRID
                       for row in grid)
                or any(type(value) is not int or value < 0
                       or value == 1
                       or value > (1 << 30)
                       or (value and value & (value - 1))
                       for row in grid for value in row)
                or not any(flat)
                or game_state not in {"playing", "won", "gameover"}
                or (game_state == "won" and not bool(state.get("won")))
                or (bool(state.get("won"))
                    != (max(flat, default=0) >= 2048))):
            self._quarantine_bad_slot("invalid_2048_slot_semantics")
            return
        if game_state == "playing":
            movable = any(value == 0 for value in flat)
            if not movable:
                movable = any(
                    grid[row][col] == grid[row][col + 1]
                    for row in range(GRID) for col in range(GRID - 1))
            if not movable:
                movable = any(
                    grid[row][col] == grid[row + 1][col]
                    for row in range(GRID - 1) for col in range(GRID))
            if not movable:
                game_state = "gameover"
        confirmed_score = None
        slot_revision = 0
        if version in (2, 3, 4, 5):
            attempt_uuid = state.get("attempt_uuid")
            revision = state.get(
                "attempt_revision", state.get("revision"))
            slot_revision = state.get("slot_revision", 0)
            confirmed_score = state.get("confirmed_score")
            valid_attempt = (
                isinstance(attempt_uuid, str)
                and 16 <= len(attempt_uuid) <= 64
                and all(char.isascii()
                        and (char.isalnum() or char in "-_")
                        for char in attempt_uuid))
            if (not valid_attempt or type(revision) is not int or revision < 0
                    or type(slot_revision) is not int
                    or not 0 <= slot_revision <= (1 << 63) - 1
                    or (confirmed_score is not None
                        and (type(confirmed_score) is not int
                             or not 0 <= confirmed_score <= score))
                    or type(state.get("won_announced")) is not bool
                    or (state.get("won_announced")
                        and not bool(state.get("won")))):
                self._quarantine_bad_slot("invalid_2048_attempt_state")
                return
        self.tiles = []
        self.grid = [[None] * GRID for _ in range(GRID)]
        for row in range(GRID):
            for col in range(GRID):
                value = grid[row][col]
                if value:
                    tile = Tile(value=value, row=row, col=col)
                    self.tiles.append(tile)
                    self.grid[row][col] = tile
        self.score = score
        self.won = bool(state.get("won"))
        self.state = game_state
        self._won_announced = (
            bool(state.get("won_announced"))
            if version in (2, 3, 4, 5) else self.won)
        if self.state == "playing" and self.won and not self._won_announced:
            # A crash can happen after the 2048 tile is committed but before
            # the overlay/score acknowledgement. Restore that milestone as a
            # terminal UI state instead of silently marking it announced.
            self.state = "won"
            self._won_announced = True
        if version in (2, 3, 4, 5):
            self.attempt_context.attempt_uuid = attempt_uuid
            self.attempt_context.revision = revision
            self._score_attempt_uuid = attempt_uuid
            self._score_attempt_revision = revision
            # v2 stored a process-local SQLite row ID. It is deliberately
            # ignored; attempt_uuid is the stable identity.
            self._score_submission_id = None
            self.score_submission_id = None
            self.submitted_score = confirmed_score
            self.score_submitted = confirmed_score is not None
            self._slot_revision = slot_revision
        current_epoch = (
            state.get("owner_epoch", 0) if version in (4, 5) else 0)
        self._slot_owner_epoch = current_epoch
        self.anim_t = 1.0
        self.slot_load_state = "ready"
        self.slot_load_error = None
        needs_owner_claim = (
            version in (1, 2, 3)
            or (version in (4, 5) and owner_status == "released")
            or (version in (4, 5)
                and owner_token != self._slot_owner_token))
        if needs_owner_claim:
            self.slot_load_state = "claiming"
            self._slot_expected_owner_token = (
                owner_token if version in (4, 5) else None)
            self._slot_expected_owner_epoch = current_epoch
            self._slot_expected_revision = slot_revision
            self._slot_expected_value_hash = saved.get("value_hash")
            self._slot_owner_epoch = current_epoch + 1
        else:
            self._clear_slot_claim_expectations()
        self._allow_slot_takeover = False
        self._takeover_reload_expected = None
        self._slot_conflict_saved = None
        if needs_owner_claim:
            self._save_autosave_slot(allow_claim=True)
        if (self.state in {"won", "gameover"}
                and (confirmed_score is None or confirmed_score < self.score)):
            restored_extra = {
                "max_tile": self._max_tile(), "won": self.won}
            self.extra = restored_extra
            self._submit_score(extra=restored_extra)

    def _take_over_conflicting_slot(self) -> None:
        if self._slot_conflict_saved is None:
            return
        state = self._slot_conflict_saved.get("state", {})
        self._takeover_reload_expected = (
            state.get("owner_token"), state.get("owner_epoch", 0),
            state.get("slot_revision", 0),
            self._slot_conflict_saved.get("value_hash"))
        load_slot = getattr(self.backend, "load_slot_async", None)
        if not callable(load_slot):
            self.slot_load_error = "无法重新读取自动存档，未执行接管"
            return
        try:
            self._slot_load_future = load_slot(
                self.profile_id, self.game_id, "autosave")
        except Exception as exc:  # noqa: BLE001
            self._slot_load_future = None
            self._takeover_reload_expected = None
            self.slot_load_error = f"接管前重新读取失败：{exc}"
            return
        self.slot_load_state = "loading"
        self.slot_load_error = None
        self._slot_load_started_at = pygame.time.get_ticks() / 1000.0

    def _quarantine_bad_slot(self, reason: str) -> None:
        quarantine = getattr(self.backend, "quarantine_slot_async", None)
        if callable(quarantine):
            try:
                self._slot_quarantine_future = quarantine(
                    self.profile_id, self.game_id, "autosave", reason)
            except Exception:  # noqa: BLE001
                self._slot_quarantine_future = None
        if self._slot_quarantine_future is None:
            self.slot_load_state = "quarantine_failed"
            self.slot_load_error = (
                "自动存档内容损坏且未能隔离；不会覆盖原数据，可返回菜单")
        else:
            self.slot_load_state = "quarantining"
            self.slot_load_error = "检测到损坏存档，正在保留原始数据…"

    def _poll_slot_quarantine(self) -> None:
        future = self._slot_quarantine_future
        if future is None or not future.done():
            return
        self._slot_quarantine_future = None
        try:
            quarantined = bool(future.result())
        except Exception:  # noqa: BLE001
            quarantined = False
        if quarantined:
            self.slot_load_state = "failed"
            self.slot_load_error = (
                "自动存档内容损坏，原始数据已隔离；可重试或确认新开")
        else:
            self.slot_load_state = "quarantine_failed"
            self.slot_load_error = (
                "自动存档内容损坏但隔离未确认；不会覆盖原数据，可返回菜单")

    def _submit_score(self, extra=None) -> None:
        # Repeated calls for the same settled score are ignored.  When the
        # player continues past 2048 and later finishes with a higher score,
        # update this session's existing backend row instead of either
        # losing the final score or creating a duplicate leaderboard entry.
        self._poll_score_submission()
        if (self.score_submitted and self.submitted_score == self.score
                and self.submitted_extra == extra):
            return
        if (self._score_submit_future is not None
                or self.score_save_state in (SAVE_SAVING, SAVE_PENDING)):
            # The final score may arrive while the 2048 milestone request is
            # still in flight. Keep only the newest pending value; once the
            # first response supplies an id, the queued value updates it.
            revision = self._next_score_revision()
            self._queued_score_submission = (
                self.score, extra, uuid.uuid4().hex,
                revision)
            return
        self._submit_result_score(self.score, extra)

    def on_score_save_succeeded(self, result: dict, payload: dict) -> None:
        self.score_submission_id = self._score_submission_id
        self.score_submitted = True
        confirmed_score = (result.get("score", payload["score"])
                           if isinstance(result, dict) else payload["score"])
        self.submitted_score = max(
            confirmed_score,
            self.submitted_score if self.submitted_score is not None else 0)
        self.submitted_extra = payload.get("extra")
        self._save_autosave_slot()
        queued = self._queued_score_submission
        self._queued_score_submission = None
        if (queued is not None
                and (queued[0] != self.submitted_score
                     or queued[1] != self.submitted_extra)):
            self._submit_result_score(
                queued[0], queued[1], request_id=queued[2],
                revision=queued[3])

    def _detach_queued_score_submission(self) -> None:
        """Keep a final score alive when reset detaches the game object."""
        queued = getattr(self, "_queued_score_submission", None)
        if not queued or not self.backend:
            return
        submit_async = getattr(
            self.backend, "submit_score_reliable_async", None)
        if callable(submit_async):
            submit_async(self.game_id, self.player, queued[0],
                         extra=queued[1], replace=True,
                         submission_id=self.score_submission_id,
                         request_id=queued[2],
                         attempt_uuid=self._score_attempt_uuid,
                         revision=queued[3],
                         **self.attempt_context.as_submit_kwargs())

    def _continue_after_win(self) -> None:
        self._queued_directions.clear()
        self.state = "playing"
        self.overlay_buttons = []
        if not self._can_move():
            self.state = "gameover"
            self.extra = {"max_tile": self._max_tile(), "won": True}
            self._submit_score(extra=self.extra)
        self._save_autosave_slot()

    # ------------------------------------------------------------------
    def handle_event(self, event):
        if self.slot_load_state != "ready":
            if (event.type == pygame.QUIT
                    or (event.type == pygame.KEYDOWN
                        and event.key == pygame.K_ESCAPE)):
                super().handle_event(event)
            elif (self.slot_load_state in {"failed", "quarantine_failed"}
                  and event.type == pygame.KEYDOWN):
                if event.key == pygame.K_t:
                    self._retry_slot_load()
                elif (event.key == pygame.K_n
                      and self.slot_load_state == "failed"):
                    self._confirm_new_game_after_load_failure()
            elif (self.slot_load_state == "conflict"
                  and event.type == pygame.KEYDOWN
                  and event.key == pygame.K_k):
                self._take_over_conflicting_slot()
            self._swipe_start = None
            self._queued_directions.clear()
            return
        if (event.type == getattr(pygame, "WINDOWFOCUSLOST", -1)
                or (event.type == pygame.KEYDOWN
                    and event.key == pygame.K_p)):
            self._swipe_start = None
            self._queued_directions.clear()
        if super().handle_event(event):
            return
        # Mouse swipe support: press → drag → release triggers a slide
        # in the dominant axis (provided the drag is long enough).
        board_rect = pygame.Rect(BOARD_X, BOARD_Y, BOARD_SIZE, BOARD_SIZE)
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.state == "playing"
                and board_rect.collidepoint(event.pos)):
            self._swipe_start = event.pos
            return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._swipe_start is not None and self.state == "playing":
                self._maybe_swipe(event.pos)
            self._swipe_start = None
            return
        if event.type != pygame.KEYDOWN:
            return
        # R always restarts.
        if event.key == pygame.K_r:
            self.request_reset()
            return
        if self.state == "playing":
            if event.key in (pygame.K_LEFT, pygame.K_a):
                self._move("left")
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self._move("right")
            elif event.key in (pygame.K_UP, pygame.K_w):
                self._move("up")
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self._move("down")
        elif self.state == "won":
            # C = keep playing past 2048. The board is still alive.
            if event.key in (pygame.K_c, pygame.K_RETURN, pygame.K_SPACE):
                self._continue_after_win()

    def _maybe_swipe(self, end_pos) -> None:
        """Translate a mouse drag (start, end) into a slide direction.
        Threshold ~24 px so a stray click doesn't trigger a slide."""
        sx, sy = self._swipe_start
        ex, ey = end_pos
        dx, dy = ex - sx, ey - sy
        threshold = 24
        if abs(dx) < threshold and abs(dy) < threshold:
            return
        if abs(dx) > abs(dy):
            self._move("right" if dx > 0 else "left")
        else:
            self._move("down" if dy > 0 else "up")

    # ------------------------------------------------------------------
    def draw(self):
        draw_gradient_bg(self.screen)

        # Header — score/max-tile are RIGHT-aligned to the board's right
        # edge so a long score (7+ digits) never overflows.
        from client.common.ui import font as _font
        draw_text(self.screen, "2048", (BOARD_X, 30), size=36,
                  color=COLORS["accent2"], bold=True)
        score_line = f"得分: {self.score}"
        sw = _font(18, bold=True).size(score_line)[0]
        draw_text(self.screen, score_line,
                  (BOARD_X + BOARD_SIZE - sw, 30), size=18,
                  color=COLORS["text"], bold=True)
        mt_line = f"最高方块: {self._max_tile()}"
        mw = _font(14).size(mt_line)[0]
        draw_text(self.screen, mt_line,
                  (BOARD_X + BOARD_SIZE - mw, 56), size=14,
                  color=COLORS["text_dim"])

        # Board background + empty cell wells.
        board_rect = pygame.Rect(BOARD_X, BOARD_Y, BOARD_SIZE, BOARD_SIZE)
        pygame.draw.rect(self.screen, BOARD_COLOR, board_rect,
                         border_radius=10)
        for i in range(GRID):
            for j in range(GRID):
                x = BOARD_X + GAP + j * (TILE + GAP)
                y = BOARD_Y + GAP + i * (TILE + GAP)
                pygame.draw.rect(self.screen, EMPTY_CELL_COLOR,
                                 pygame.Rect(x, y, TILE, TILE),
                                 border_radius=6)

        # Tiles (use interpolated positions during slide).
        ease = ease_out_cubic(self.anim_t)
        for t in self.tiles:
            ar = t.from_row + (t.row - t.from_row) * ease
            ac = t.from_col + (t.col - t.from_col) * ease
            x = BOARD_X + GAP + ac * (TILE + GAP)
            y = BOARD_Y + GAP + ar * (TILE + GAP)
            scale = 1.0
            if t.spawn_progress < 1.0:
                scale *= max(0.0, ease_out_back(t.spawn_progress))
            if t.merge_pop > 0:
                scale *= 1.0 + 0.18 * t.merge_pop
            if scale <= 0.01:
                continue
            size = TILE * scale
            offset = (TILE - size) / 2
            rect = pygame.Rect(x + offset, y + offset, size, size)
            bg, fg = tile_color(t.value)
            pygame.draw.rect(self.screen, bg, rect, border_radius=6)
            if t.value:
                font_size = 36 if t.value < 100 else 30 if t.value < 1000 else 24
                font_size = max(10, int(font_size * scale))
                draw_text(self.screen, str(t.value), rect.center,
                          size=font_size, color=fg, bold=True, center=True)

        # Hint
        draw_text(self.screen,
                  "方向键/WASD 滑动 · R 重开 · P 暂停 · Esc 返回菜单",
                  (self.width // 2, self.height - 18),
                  size=12, color=COLORS["text_dim"], center=True)

        if self.slot_load_state in {"loading", "claiming"}:
            veil = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            veil.fill((245, 248, 255, 190))
            self.screen.blit(veil, (0, 0))
            message = ("正在确认自动存档占用权…"
                       if self.slot_load_state == "claiming"
                       else "正在恢复自动存档…")
            draw_text(self.screen, message,
                      (self.width // 2, self.height // 2), size=18,
                      color=COLORS["accent"], bold=True, center=True)
        elif self.slot_load_state == "failed":
            veil = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            veil.fill((245, 248, 255, 225))
            self.screen.blit(veil, (0, 0))
            draw_text(self.screen, "自动存档未能安全恢复",
                      (self.width // 2, self.height // 2 - 34), size=20,
                      color=COLORS["danger"], bold=True, center=True)
            draw_text(self.screen,
                      self.slot_load_error or "请重试读取",
                      (self.width // 2, self.height // 2), size=12,
                      color=COLORS["text"], center=True)
            draw_text(self.screen, "T 重试 · N 新开（需二次确认）· Esc 返回",
                      (self.width // 2, self.height // 2 + 34), size=13,
                      color=COLORS["accent"], bold=True, center=True)
        elif self.slot_load_state == "conflict":
            veil = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            veil.fill((245, 248, 255, 225))
            self.screen.blit(veil, (0, 0))
            draw_text(self.screen, "自动存档正在别处使用",
                      (self.width // 2, self.height // 2 - 28), size=20,
                      color=COLORS["danger"], bold=True, center=True)
            draw_text(self.screen, "K 接管 · Esc 返回菜单",
                      (self.width // 2, self.height // 2 + 18), size=13,
                      color=COLORS["accent"], bold=True, center=True)
        elif self.slot_save_error:
            draw_text(self.screen, self.slot_save_error,
                      (self.width // 2, self.height - 38), size=12,
                      color=COLORS["danger"], center=True)

        if self.state == "paused":
            self.draw_paused_overlay()
        elif self.state == "won":
            btns = [
                Button(pygame.Rect(0, 0, 150, 36), "继续挑战 (C)",
                       self._continue_after_win, primary=True),
                Button(pygame.Rect(0, 0, 150, 36), "重新开始 (R)",
                       self.request_reset),
                Button(pygame.Rect(0, 0, 150, 36), "返回菜单 (Esc)",
                       self.request_exit),
            ]
            self.draw_gameover_overlay("达成 2048！", buttons=btns)
        elif self.state == "gameover":
            btns = [
                Button(pygame.Rect(0, 0, 150, 36), "重新开始 (R)",
                       self.request_reset, primary=True),
                Button(pygame.Rect(0, 0, 150, 36), "返回菜单 (Esc)",
                       self.request_exit),
            ]
            self.draw_gameover_overlay("游戏结束", buttons=btns)

    def before_close(self) -> None:
        # A release with a newer state-journal revision cancels an unfinished
        # claim as well as releasing an acknowledged owner. Waiting for the
        # claim first can leave a stale active owner if the window exits while
        # SQLite is slow and the durable claim replays later.
        if self.slot_load_state not in {"ready", "claiming"}:
            return
        publish_intent = getattr(self.backend, "publish_slot_intent", None)
        if callable(publish_intent):
            try:
                publish_intent(
                    self.profile_id, self.game_id, "autosave",
                    self._build_autosave_state("released"),
                    self.attempt_context.ruleset_version)
                self._slot_save_pending = True
                return
            except Exception:  # noqa: BLE001 - fall back to async pipeline
                self.slot_save_error = "退出状态正在等待后台保存"
        if self.slot_load_state == "claiming":
            future = self._slot_save_future
            if future is not None:
                try:
                    future.result(timeout=0.5)
                except Exception:  # noqa: BLE001
                    pass
                self._poll_slot_save()
            if self.slot_load_state != "ready":
                return
        self._save_autosave_slot(owner_status="released")
        future = self._slot_save_future
        if future is not None:
            try:
                future.result(timeout=0.5)
            except Exception:  # noqa: BLE001 - durable journal may finish later
                pass
            self._poll_slot_save()
        future = self._slot_save_future
        if future is not None:
            try:
                future.result(timeout=0.5)
            except Exception:  # noqa: BLE001 - durable journal may finish later
                pass


def run_game(backend: Optional[GameDataService] = None,
             player: str = "anonymous",
             profile_id: Optional[str] = None) -> None:
    Game2048(backend=backend, player=player, profile_id=profile_id).run()


if __name__ == "__main__":
    run_game()
