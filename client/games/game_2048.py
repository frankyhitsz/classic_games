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

import random
import uuid
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

import pygame

from client.common.ui import (COLORS, SAVE_PENDING, SAVE_SAVING, BaseGame, Button,
                              draw_gradient_bg, draw_text, ease_out_back,
                              ease_out_cubic)
from game_service.service import GameDataService

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
        self._initializing_board = True
        self.reset()
        self._initializing_board = False
        load_slot = getattr(self.backend, "load_slot_async", None)
        if callable(load_slot):
            self._slot_load_future = load_slot(
                self.profile_id, self.game_id, "autosave")

    # ------------------------------------------------------------------
    def reset(self):
        if not getattr(self, "_initializing_board", False):
            self._slot_load_future = None
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
                self._submit_score(extra=self.extra)
            return False

        # Begin animation; spawn + state transition happen when it ends.
        self.anim_t = 0.0
        return True

    # ------------------------------------------------------------------
    def update(self, dt: float) -> None:
        # Animations keep progressing in any state — see ``update_overlay``.
        self._poll_slot_load()
        self._tick_animations(dt)

    def update_overlay(self, dt: float) -> None:
        self._poll_slot_load()
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
                self._save_autosave_slot()

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

    def _save_autosave_slot(self) -> None:
        save_slot = getattr(self.backend, "save_slot_async", None)
        if not callable(save_slot):
            return
        state = {
            "version": 1,
            "score": self.score,
            "won": self.won,
            "grid": [[self.grid[row][col].value
                      if self.grid[row][col] is not None else 0
                      for col in range(GRID)] for row in range(GRID)],
        }
        try:
            self._slot_save_future = save_slot(
                self.profile_id, self.game_id, "autosave", state)
        except Exception:  # noqa: BLE001 - score play remains available
            self._slot_save_future = None

    def _poll_slot_load(self) -> None:
        future = self._slot_load_future
        if future is None or not future.done():
            return
        self._slot_load_future = None
        try:
            saved = future.result()
        except Exception:  # noqa: BLE001 - an invalid save starts a new board
            return
        if not isinstance(saved, dict):
            return
        state = saved.get("state")
        grid = state.get("grid") if isinstance(state, dict) else None
        score = state.get("score") if isinstance(state, dict) else None
        if (saved.get("ruleset_version")
                != self.attempt_context.ruleset_version
                or not isinstance(state, dict) or state.get("version") != 1
                or type(score) is not int or score < 0
                or not isinstance(grid, list) or len(grid) != GRID
                or any(not isinstance(row, list) or len(row) != GRID
                       for row in grid)
                or any(type(value) is not int or value < 0
                       or value == 1
                       or (value and value & (value - 1))
                       for row in grid for value in row)
                or not any(value for row in grid for value in row)):
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
        self._won_announced = self.won
        self.anim_t = 1.0

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

    # ------------------------------------------------------------------
    def handle_event(self, event):
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


def run_game(backend: Optional[GameDataService] = None,
             player: str = "anonymous",
             profile_id: Optional[str] = None) -> None:
    Game2048(backend=backend, player=player, profile_id=profile_id).run()


if __name__ == "__main__":
    run_game()
