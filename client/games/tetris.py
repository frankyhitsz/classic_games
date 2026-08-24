"""Tetris — classic falling-block puzzle.

Rotation system: an SRS-inspired assisted rotation model. Each piece has
4 rotation INDEX states (0=spawn, R=CW90,
2=CW180, L=CW270). For pieces with rotational symmetry (I, S, Z), some
of these states are visually identical.

The standard SRS kick tables are the base, with extra floor assistance and
alignment recovery requested for this collection. It is deliberately not a
claim of strict Tetris Guideline conformance.

Controls:
  ←/→     move
  ↑ / X   rotate clockwise
  Z       rotate counter-clockwise
  ↓       soft drop
  SPACE   hard drop
  P       pause
  R       restart after game over
  Esc     quit back to launcher
"""
from __future__ import annotations

import random
from typing import List, Optional, Tuple

import pygame

from client.common.network import BackendClient
from client.common.ui import (COLORS, BaseGame, Button, draw_gradient_bg,
                              draw_panel, draw_text)

CELL = 30
COLS, ROWS = 10, 20
BOARD_W, BOARD_H = COLS * CELL, ROWS * CELL
PANEL_W = 220
WIDTH = BOARD_W + PANEL_W + 40
HEIGHT = BOARD_H + 40
BOARD_X = 20
BOARD_Y = 20
HORIZONTAL_DAS = 0.16  # delay before held left/right starts repeating
HORIZONTAL_ARR = 0.045  # repeat interval after DAS
SOFT_DROP_DAS = 0.12
SOFT_DROP_ARR = 0.045
MAX_FRAME_DT = 0.25
TETRIS_LINES_PER_LEVEL = 10
TETRIS_INITIAL_DROP_INTERVAL = 0.8
TETRIS_MIN_DROP_INTERVAL = 0.08
TETRIS_SPEEDUP_PER_LEVEL = 0.07


def tetris_drop_interval(level: int) -> float:
    """Return the automatic drop interval for a displayed level."""
    return max(TETRIS_MIN_DROP_INTERVAL,
               TETRIS_INITIAL_DROP_INTERVAL
               - max(0, level - 1) * TETRIS_SPEEDUP_PER_LEVEL)

# Each tetromino: list of rotations, each a list of (x, y) occupied cells
# where x,y range over the 4x4 bounding box.
SHAPES: dict = {
    "I": [[(0, 1), (1, 1), (2, 1), (3, 1)],
          [(2, 0), (2, 1), (2, 2), (2, 3)],
          [(0, 2), (1, 2), (2, 2), (3, 2)],
          [(1, 0), (1, 1), (1, 2), (1, 3)]],
    "O": [[(1, 0), (2, 0), (1, 1), (2, 1)]] * 4,
    "T": [[(1, 0), (0, 1), (1, 1), (2, 1)],
          [(1, 0), (1, 1), (2, 1), (1, 2)],
          [(0, 1), (1, 1), (2, 1), (1, 2)],
          [(1, 0), (0, 1), (1, 1), (1, 2)]],
    "S": [[(1, 0), (2, 0), (0, 1), (1, 1)],
          [(1, 0), (1, 1), (2, 1), (2, 2)],
          [(1, 1), (2, 1), (0, 2), (1, 2)],
          [(0, 0), (0, 1), (1, 1), (1, 2)]],
    "Z": [[(0, 0), (1, 0), (1, 1), (2, 1)],
          [(2, 0), (1, 1), (2, 1), (1, 2)],
          [(0, 1), (1, 1), (1, 2), (2, 2)],
          [(1, 0), (0, 1), (1, 1), (0, 2)]],
    "J": [[(0, 0), (0, 1), (1, 1), (2, 1)],
          [(1, 0), (2, 0), (1, 1), (1, 2)],
          [(0, 1), (1, 1), (2, 1), (2, 2)],
          [(1, 0), (1, 1), (0, 2), (1, 2)]],
    "L": [[(2, 0), (0, 1), (1, 1), (2, 1)],
          [(1, 0), (1, 1), (1, 2), (2, 2)],
          [(0, 1), (1, 1), (2, 1), (0, 2)],
          [(0, 0), (1, 0), (1, 1), (1, 2)]],
}
SHAPE_KEYS = list(SHAPES.keys())


# ---------------------------------------------------------------------------
# Super Rotation System wall-kick tables.
#
# SRS uses (x, y) where +y is UP (away from floor). Kicks are listed
# per rotation transition. For JLSTZ pieces one table; for I another.
# Source: https://tetris.wiki/Super_Rotation_System
#
# We store them in (x, y) with our GAME's y-DOWN convention (so +y means
# "toward the floor", -y means "up away from the floor"). The original
# SRS y-up values have been sign-flipped on the y axis.
# ---------------------------------------------------------------------------
# Transition keys: "0>R", "R>0", "R>2", "2>R", "2>L", "L>2", "L>0", "0>L"
SRS_JLSTZ_KICKS = {
    "0>R": [(0, 0), (-1, 0), (-1, -1), (0, +2), (-1, +2)],
    "R>0": [(0, 0), (+1, 0), (+1, +1), (0, -2), (+1, -2)],
    "R>2": [(0, 0), (+1, 0), (+1, +1), (0, -2), (+1, -2)],
    "2>R": [(0, 0), (-1, 0), (-1, -1), (0, +2), (-1, +2)],
    "2>L": [(0, 0), (+1, 0), (+1, -1), (0, +2), (+1, +2)],
    "L>2": [(0, 0), (-1, 0), (-1, +1), (0, -2), (-1, -2)],
    "L>0": [(0, 0), (-1, 0), (-1, +1), (0, -2), (-1, -2)],
    "0>L": [(0, 0), (+1, 0), (+1, -1), (0, +2), (+1, +2)],
}

SRS_I_KICKS = {
    # Standard SRS I-piece kicks (y-down convention) plus two extra
    # "vertical-only" floor kicks (0, -2) and (0, -3) appended to the
    # 0>R / 2>L / L>2 / 0>L transitions. Standard SRS rejects an
    # I-rotation when the piece is sitting flat on the floor (vertical
    # I needs 4 rows of headroom but only 1-2 remain). The extra kicks
    # let the piece snap up to fit, which feels more "complete" to
    # players who expect every rotation input to register.
    "0>R": [(0, 0), (-2, 0), (+1, 0), (-2, -1), (+1, +2),
            (0, -2), (0, -3)],
    "R>0": [(0, 0), (+2, 0), (-1, 0), (+2, +1), (-1, -2)],
    "R>2": [(0, 0), (-1, 0), (+2, 0), (-1, +2), (+2, -1)],
    "2>R": [(0, 0), (+1, 0), (-2, 0), (+1, -2), (-2, +1)],
    "2>L": [(0, 0), (+2, 0), (-1, 0), (+2, +1), (-1, -2),
            (0, -2), (0, -3)],
    "L>2": [(0, 0), (-2, 0), (+1, 0), (-2, -1), (+1, +2),
            (0, -2), (0, -3)],
    "L>0": [(0, 0), (+1, 0), (-2, 0), (+1, -2), (-2, +1)],
    "0>L": [(0, 0), (-1, 0), (+2, 0), (-1, +2), (+2, -1),
            (0, -2), (0, -3)],
}

# State index naming: 0=spawn, 1=R, 2=180, 3=L
_ROT_NAMES = ["0", "R", "2", "L"]


def _kick_key(from_rot: int, direction: int) -> str:
    """Return the SRS transition key for ``from_rot`` rotated by
    ``direction`` (+1 CW, -1 CCW)."""
    n = 4
    to_rot = (from_rot + direction) % n
    return f"{_ROT_NAMES[from_rot]}>{_ROT_NAMES[to_rot]}"


class Piece:
    def __init__(self, kind: str):
        self.kind = kind
        self.rot = 0
        self.x = COLS // 2 - 2
        self.y = -1

    def cells(self, dx: int = 0, dy: int = 0, rot: Optional[int] = None) -> List[Tuple[int, int]]:
        r = self.rot if rot is None else rot
        return [(self.x + x + dx, self.y + y + dy)
                for x, y in SHAPES[self.kind][r % len(SHAPES[self.kind])]]


class Tetris(BaseGame):
    game_id = "tetris"
    title = "俄罗斯方块"

    def __init__(self, backend: Optional[BackendClient] = None,
                 player: str = "anonymous"):
        super().__init__(WIDTH, HEIGHT, fps=60, backend=backend, player=player)
        self.reset()

    # ------------------------------------------------------------------
    def reset(self):
        self.begin_score_session()
        self.board: List[List[Optional[str]]] = [
            [None] * COLS for _ in range(ROWS)]
        self.piece: Optional[Piece] = None
        self.next_kind = random.choice(SHAPE_KEYS)
        self.score = 0
        self.lines = 0
        self.level = 1
        self.drop_timer = 0.0
        self.drop_interval = tetris_drop_interval(self.level)
        self.horizontal_hold = 0
        self.pressed_keys: set[int] = set()
        self._horizontal_press_order: List[int] = []
        self.horizontal_repeat_timer = 0.0
        self.soft_drop_held = False
        self.soft_drop_repeat_timer = 0.0
        self.state = "playing"
        self.overlay_buttons = []
        # Total successful rotations this game (for stats display).
        self._rot_count = 0
        # Upward floor kicks are remembered until a later orientation can
        # safely settle by the same amount. This makes a four-rotation cycle
        # of J/L/T return to the original aligned cell instead of drifting
        # one row up (and often one column left) along the floor.
        self._rotation_lift_debt = 0
        self.piece_generation = 0
        self._spawn()

    def _spawn(self):
        self.piece_generation += 1
        self._rotation_lift_debt = 0
        self.piece = Piece(self.next_kind)
        self.next_kind = random.choice(SHAPE_KEYS)
        if self._collides(self.piece.cells()):
            self.state = "gameover"
            self.on_game_over(self.score, extra={"lines": self.lines,
                                                  "level": self.level})

    def _collides(self, cells, board=None) -> bool:
        if board is None:
            board = self.board
        for x, y in cells:
            if x < 0 or x >= COLS or y >= ROWS:
                return True
            if y >= 0 and board[y][x] is not None:
                return True
        return False

    def _lock(self):
        # A newly spawned piece receives a full gravity interval. Without
        # this reset, hard/soft locking near the end of an interval can make
        # the next piece fall immediately.
        self.drop_timer = 0.0
        cells = self.piece.cells()
        topped_out = any(y < 0 for _, y in cells)
        for x, y in cells:
            if 0 <= y < ROWS:
                self.board[y][x] = self.piece.kind
        # A piece that locks while any cell is still above the visible board
        # is a top-out.  Previously those hidden cells were silently dropped
        # and a differently-shaped next piece could let play continue.
        if topped_out:
            self.on_game_over(self.score,
                              extra={"lines": self.lines,
                                     "level": self.level,
                                     "top_out": True})
            return
        self._clear_lines()
        self._spawn()

    def _clear_lines(self):
        cleared = 0
        kept = []
        for row in self.board:
            if all(c is not None for c in row):
                cleared += 1
            else:
                kept.append(row)
        for _ in range(cleared):
            kept.insert(0, [None] * COLS)
        self.board = kept
        if cleared:
            # Quadratic scaling per the user's request:
            #   1 line = 1*1*100, 2 = 4*100, 3 = 9*100, 4 = 16*100.
            # Multiplied by current level so late-game clears are worth more.
            pts = cleared * cleared * 100 * self.level
            self.score += pts
            self.lines += cleared
            self.level = 1 + self.lines // TETRIS_LINES_PER_LEVEL
            self.drop_interval = tetris_drop_interval(self.level)

    def _move(self, dx: int):
        if self._collides(self.piece.cells(dx=dx)):
            return
        self.piece.x += dx

    def _rotate(self, direction: int):
        """Rotate the current piece by ``direction`` (+1 CW, -1 CCW).

        Per the user's request, rotation is REJECTED (not wall-kicked)
        when the rotation's no-kick target position overlaps an EXISTING
        LOCKED BLOCK on the board. This matches the "real Tetris" feel:
        pieces cannot jump over blocks. Wall-kicks are still tried when
        the collision is purely with the FLOOR or WALLS (out-of-bounds),
        which is the case SRS wall-kicks were designed for.
        """
        kind = self.piece.kind
        new_rot = (self.piece.rot + direction) % len(SHAPES[kind])

        # First check: at the no-kick position, is there a collision
        # with an EXISTING BLOCK? If yes, the rotation is rejected
        # outright — no kicks tried. This prevents the piece from
        # "jumping up" over blocks (the bug the user reported).
        no_kick_cells = self.piece.cells(rot=new_rot)
        block_collision = False
        for x, y in no_kick_cells:
            if 0 <= x < COLS and 0 <= y < ROWS and self.board[y][x] is not None:
                block_collision = True
                break
        if block_collision:
            return  # rotation rejected

        # No block collision at no-kick position — accept it immediately
        # if it's fully in-bounds.
        if not self._collides(no_kick_cells):
            self.piece.rot = new_rot
            self._rot_count += 1
            self._settle_rotation_lift()
            return

        # Otherwise the collision is purely with floor/walls. Try SRS
        # wall-kicks. Kicks that would land the piece on existing blocks
        # are rejected by ``_collides`` as usual.
        if kind == "I":
            kicks = SRS_I_KICKS[_kick_key(self.piece.rot, direction)]
        elif kind == "O":
            kicks = [(0, 0)]
        else:  # J, L, S, T, Z
            kicks = SRS_JLSTZ_KICKS[_kick_key(self.piece.rot, direction)]
            # Prefer a straight upward floor correction before SRS's
            # diagonal kick. The old order made J/L/T jump left at the floor,
            # which is the visible "rotation not aligned" problem.
            kicks = [(0, -1), (0, -2)] + kicks
        for kx, ky in kicks:
            if not self._collides(self.piece.cells(dx=kx, dy=ky, rot=new_rot)):
                self.piece.x += kx
                self.piece.y += ky
                self.piece.rot = new_rot
                self._rot_count += 1
                if ky < 0:
                    self._rotation_lift_debt += -ky
                elif ky > 0:
                    self._rotation_lift_debt = max(
                        0, self._rotation_lift_debt - ky)
                self._settle_rotation_lift()
                return

    def _settle_rotation_lift(self) -> None:
        """Restore cells borrowed by an earlier upward floor kick.

        The restore only happens when the current orientation genuinely fits
        lower, so it cannot move through the floor or locked blocks.
        """
        while (self._rotation_lift_debt > 0
               and not self._collides(self.piece.cells(dy=1))):
            self.piece.y += 1
            self._rotation_lift_debt -= 1

    def _soft_drop(self):
        if not self._collides(self.piece.cells(dy=1)):
            self.piece.y += 1
            self._rotation_lift_debt = max(0,
                                           self._rotation_lift_debt - 1)
            self.score += 1
        else:
            self._lock()

    def _hard_drop(self):
        d = 0
        while not self._collides(self.piece.cells(dy=d + 1)):
            d += 1
        self.piece.y += d
        self._rotation_lift_debt = 0
        # Hard-drop bonus was 2*d, which felt too generous. Drop to 1*d
        # so a 20-row drop is +20 instead of +40.
        self.score += d
        self._lock()

    # ------------------------------------------------------------------
    def update(self, dt: float):
        if self.state != "playing":
            return
        dt = max(0.0, min(dt, MAX_FRAME_DT))
        if self.horizontal_hold:
            self.horizontal_repeat_timer -= dt
            repeats = 0
            while self.horizontal_repeat_timer <= 0.0 and repeats < COLS:
                self._move(self.horizontal_hold)
                self.horizontal_repeat_timer += HORIZONTAL_ARR
                repeats += 1
        if self.soft_drop_held:
            generation = self.piece_generation
            self.soft_drop_repeat_timer -= dt
            repeats = 0
            while (self.soft_drop_repeat_timer <= 0.0
                   and repeats < ROWS and self.state == "playing"):
                self._soft_drop()
                if self.piece_generation != generation:
                    # The remaining accumulator belongs to the locked piece,
                    # never to the newly spawned one.
                    self.soft_drop_repeat_timer = 0.0
                    self.drop_timer = 0.0
                    return
                self.soft_drop_repeat_timer += SOFT_DROP_ARR
                repeats += 1
        self.drop_timer += dt
        gravity_steps = 0
        while (self.drop_timer >= self.drop_interval
               and gravity_steps < ROWS and self.state == "playing"):
            self.drop_timer -= self.drop_interval
            if not self._collides(self.piece.cells(dy=1)):
                self.piece.y += 1
                self._rotation_lift_debt = max(
                    0, self._rotation_lift_debt - 1)
            else:
                generation = self.piece_generation
                self._lock()
                if self.piece_generation != generation:
                    self.drop_timer = 0.0
                    break
            gravity_steps += 1

    @staticmethod
    def _horizontal_direction_for_key(key: int) -> int:
        if key in (pygame.K_LEFT, pygame.K_a):
            return -1
        if key in (pygame.K_RIGHT, pygame.K_d):
            return 1
        return 0

    def _refresh_held_actions(self) -> None:
        active_order = [key for key in self._horizontal_press_order
                        if key in self.pressed_keys]
        self._horizontal_press_order = active_order
        self.horizontal_hold = (
            self._horizontal_direction_for_key(active_order[-1])
            if active_order else 0)
        self.soft_drop_held = bool(
            self.pressed_keys.intersection((pygame.K_DOWN, pygame.K_s)))

    def handle_event(self, event):
        if (event.type == getattr(pygame, "WINDOWFOCUSLOST", -1)
                or (event.type == pygame.KEYDOWN
                    and event.key == pygame.K_p)):
            self.horizontal_hold = 0
            self.pressed_keys.clear()
            self._horizontal_press_order = []
            self.soft_drop_held = False
            self.horizontal_repeat_timer = 0.0
            self.soft_drop_repeat_timer = 0.0
            self.drop_timer = 0.0
        if event.type == pygame.KEYUP:
            was_horizontal = bool(
                self._horizontal_direction_for_key(event.key))
            self.pressed_keys.discard(event.key)
            self._horizontal_press_order = [
                key for key in self._horizontal_press_order
                if key != event.key]
            self._refresh_held_actions()
            if was_horizontal:
                if self.horizontal_hold:
                    self.horizontal_repeat_timer = HORIZONTAL_ARR
        if super().handle_event(event):
            return
        if event.type == pygame.KEYDOWN and self.state == "playing":
            if event.key in (pygame.K_LEFT, pygame.K_a):
                is_new = event.key not in self.pressed_keys
                self.pressed_keys.add(event.key)
                if is_new:
                    self._horizontal_press_order.append(event.key)
                    self._move(-1)
                self._refresh_held_actions()
                if is_new:
                    self.horizontal_repeat_timer = HORIZONTAL_DAS
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                is_new = event.key not in self.pressed_keys
                self.pressed_keys.add(event.key)
                if is_new:
                    self._horizontal_press_order.append(event.key)
                    self._move(1)
                self._refresh_held_actions()
                if is_new:
                    self.horizontal_repeat_timer = HORIZONTAL_DAS
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                is_new = event.key not in self.pressed_keys
                self.pressed_keys.add(event.key)
                self._refresh_held_actions()
                if is_new:
                    self._soft_drop()
                    self.soft_drop_repeat_timer = SOFT_DROP_DAS
            elif event.key in (pygame.K_UP, pygame.K_x):
                self._rotate(1)
            elif event.key == pygame.K_z:
                self._rotate(-1)
            elif event.key == pygame.K_SPACE:
                self._hard_drop()
        elif event.type == pygame.KEYDOWN and self.state == "gameover":
            if event.key == pygame.K_r:
                self.reset()

    # ------------------------------------------------------------------
    def draw(self):
        draw_gradient_bg(self.screen)

        # Board background
        board_rect = pygame.Rect(BOARD_X, BOARD_Y, BOARD_W, BOARD_H)
        draw_panel(self.screen, board_rect, radius=6)

        # Grid
        for x in range(COLS + 1):
            pygame.draw.line(self.screen, (45, 50, 70),
                             (BOARD_X + x * CELL, BOARD_Y),
                             (BOARD_X + x * CELL, BOARD_Y + BOARD_H))
        for y in range(ROWS + 1):
            pygame.draw.line(self.screen, (45, 50, 70),
                             (BOARD_X, BOARD_Y + y * CELL),
                             (BOARD_X + BOARD_W, BOARD_Y + y * CELL))

        # Locked cells
        for y, row in enumerate(self.board):
            for x, c in enumerate(row):
                if c:
                    self._draw_cell(BOARD_X + x * CELL, BOARD_Y + y * CELL, c)

        # Active piece
        if self.piece and self.state == "playing":
            for x, y in self.piece.cells():
                if y >= 0:
                    self._draw_cell(BOARD_X + x * CELL, BOARD_Y + y * CELL,
                                    self.piece.kind)

        # Side panel
        self._draw_side_panel()

        if self.state == "paused":
            self.draw_paused_overlay()
        elif self.state == "gameover":
            btns = [
                Button(pygame.Rect(0, 0, 130, 36), "重新开始 (R)",
                       self.reset, primary=True),
                Button(pygame.Rect(0, 0, 130, 36), "返回菜单 (Esc)",
                       lambda: setattr(self, "running", False)),
            ]
            # Overlay auto-sizes panel + positions buttons.
            self.draw_gameover_overlay("游戏结束", buttons=btns)

    def _draw_cell(self, x, y, kind):
        from client.common.ui import GAME_COLORS
        idx = "IOTSZJL".index(kind)
        color = GAME_COLORS["tetris"][idx]
        rect = pygame.Rect(x + 1, y + 1, CELL - 2, CELL - 2)
        pygame.draw.rect(self.screen, color, rect)
        pygame.draw.rect(self.screen, tuple(min(255, c + 40) for c in color),
                         rect, 1)
        # inner highlight
        hl = pygame.Rect(x + 3, y + 3, CELL - 8, 3)
        pygame.draw.rect(self.screen, tuple(min(255, c + 70) for c in color), hl)

    def _draw_side_panel(self):
        px = BOARD_X + BOARD_W + 20
        py = BOARD_Y
        pw = PANEL_W
        # Next box
        rect = pygame.Rect(px, py, pw, 110)
        pygame.draw.rect(self.screen, COLORS["panel"], rect, border_radius=8)
        pygame.draw.rect(self.screen, COLORS["border"], rect, 1, border_radius=8)
        draw_text(self.screen, "下一个", (rect.centerx, rect.y + 16),
                  size=14, color=COLORS["text_dim"], center=True)
        # draw next shape
        cells = SHAPES[self.next_kind][0]
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        minx, maxx = min(xs), max(xs)
        miny = min(ys)
        w = (maxx - minx + 1) * 22
        ox = rect.centerx - w // 2 - minx * 22
        oy = rect.y + 40 - miny * 22
        from client.common.ui import GAME_COLORS
        idx = "IOTSZJL".index(self.next_kind)
        color = GAME_COLORS["tetris"][idx]
        for cx, cy in cells:
            pygame.draw.rect(self.screen, color,
                             pygame.Rect(ox + cx * 22, oy + cy * 22, 20, 20))

        # Score / level / lines
        rect2 = pygame.Rect(px, py + 124, pw, 200)
        pygame.draw.rect(self.screen, COLORS["panel"], rect2, border_radius=8)
        pygame.draw.rect(self.screen, COLORS["border"], rect2, 1, border_radius=8)
        draw_text(self.screen, "得分", (rect2.x + 16, rect2.y + 14), size=14,
                  color=COLORS["text_dim"])
        draw_text(self.screen, str(self.score), (rect2.x + 16, rect2.y + 32),
                  size=26, color=COLORS["accent2"], bold=True)
        draw_text(self.screen, f"行数: {self.lines}",
                  (rect2.x + 16, rect2.y + 82), size=15, color=COLORS["text"])
        draw_text(self.screen, f"等级: {self.level}",
                  (rect2.x + 16, rect2.y + 108), size=15, color=COLORS["text"])
        # Current piece + SRS rotation-state indicator (0/R/2/L).
        if self.piece:
            rot_label = ["0 (spawn)", "R (CW 90°)", "2 (CW 180°)", "L (CW 270°)"][
                self.piece.rot % 4]
            draw_text(self.screen, f"方块: {self.piece.kind} · 旋态: {rot_label}",
                      (rect2.x + 16, rect2.y + 132), size=11,
                      color=COLORS["accent"])
        # Controls
        draw_text(self.screen, "←→移动 ↑旋 ↓软降",
                  (rect2.x + 16, rect2.y + 150), size=12,
                  color=COLORS["text_dim"])
        draw_text(self.screen, "空格硬降 P暂停 Esc退出",
                  (rect2.x + 16, rect2.y + 168), size=12,
                  color=COLORS["text_dim"])


def run_game(backend: Optional[BackendClient] = None,
             player: str = "anonymous") -> None:
    Tetris(backend=backend, player=player).run()


if __name__ == "__main__":
    run_game()
