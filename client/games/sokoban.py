"""Sokoban — push all boxes onto the target spots.

Symbol legend in level strings:
  #  wall
  .  floor
  $  box
  *  box on target
  @  player
  +  player on target
  T  target (empty)
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import pygame

from game_service.catalog import GAME_BY_ID
from game_service.service import GameDataService, SaveState
from client.common.ui import (COLORS, BaseGame, Button, draw_gradient_bg,
                              draw_panel, draw_text)

CELL = 40

# Curated levels. Each is a list of strings of equal length. Levels 5-16
# were produced from solved layouts using legal reverse pulls, then every
# finished map was independently verified by the forward push-state solver in
# ``tests/regression.py``.
LEVELS: List[List[str]] = [
    # Level 1: two boxes, gentle warm-up (~10 moves).
    [
        "#######",
        "#.....#",
        "#.$.$.T",
        "#.@...#",
        "#..T..#",
        "#######",
    ],
    # Level 2: three boxes in a row, push them onto the target row (~16 moves).
    [
        "########",
        "#......#",
        "#.$$$..#",
        "#.@....#",
        "#.TTT..#",
        "#......#",
        "########",
    ],
    # Level 3: three scattered boxes / targets (~20 moves, BFS verified).
    [
        "########",
        "#..T...#",
        "#......#",
        "#.$.$..#",
        "#..@...#",
        "#.$....#",
        "#......#",
        "#.T..T.#",
        "########",
    ],
    # Level 4: four boxes ↔ four targets in a symmetric room (~21 moves).
    [
        "#########",
        "#.......#",
        "#.T...T.#",
        "#.......#",
        "#.$.@.$.#",
        "#.......#",
        "#.$...$.#",
        "#.......#",
        "#.T...T.#",
        "#########",
    ],
    # Levels 5-7: four-box layouts. No box starts on a target.
    [
        "#########",
        "#.....#.#",
        "#.T$@.T.#",
        "#.....$T#",
        "#.$.....#",
        "#..T$...#",
        "#..#....#",
        "#########",
    ],
    # Level 6
    [
        "#########",
        "#.......#",
        "#T$.#.#.#",
        "#.T.....#",
        "#.T#...T#",
        "#$$$....#",
        "#.@.....#",
        "#########",
    ],
    # Level 7
    [
        "##########",
        "#........#",
        "#......$.#",
        "#T$@.$...#",
        "#..T...$.#",
        "#.T#.#T.##",
        "##.......#",
        "##########",
    ],
    # Levels 8-10: five-box layouts.
    [
        "##########",
        "#..T.#...#",
        "#.$$$T.#.#",
        "#........#",
        "#...T#...#",
        "#T...T...#",
        "#....$@$.#",
        "#........#",
        "##########",
    ],
    # Level 9
    [
        "##########",
        "#.#......#",
        "#.@$T.$..#",
        "#..$.T...#",
        "#.$.#....#",
        "#.TT....T#",
        "#.#..$.#.#",
        "#.....#..#",
        "##########",
    ],
    # Level 10
    [
        "###########",
        "#..##.....#",
        "#.$..T#.#.#",
        "#.$...#TT.#",
        "#.$.......#",
        "#.....T...#",
        "#.$...$...#",
        "#.T...@#..#",
        "###########",
    ],
    # Levels 11-16: six-box layouts with progressively denser walls.
    [
        "###########",
        "#.........#",
        "#.#TT....##",
        "#..T$.$...#",
        "#.#T@$....#",
        "#.$T.....T#",
        "#.$.$.....#",
        "#....#....#",
        "###########",
    ],
    # Level 12
    [
        "###########",
        "#..#......#",
        "#.$...T.$.#",
        "#.........#",
        "#.T.$.....#",
        "#...#.....#",
        "#...TT..#.#",
        "#.$...$T###",
        "#.#.T$@...#",
        "###########",
    ],
    # Level 13
    [
        "############",
        "#...#.#..T.#",
        "#.@$..T.TT.#",
        "#.....$$...#",
        "##...$.....#",
        "#..........#",
        "#.#......$.#",
        "#.....#T.$.#",
        "#..#.T...#.#",
        "############",
    ],
    # Level 14
    [
        "############",
        "#.......#.##",
        "#..$..$..T.#",
        "#T$...T....#",
        "#..#$...$#.#",
        "#.......@#.#",
        "#T..T......#",
        "#.....#.T$.#",
        "####....#..#",
        "############",
    ],
    # Level 15
    [
        "############",
        "#.T..T..T..#",
        "#..........#",
        "#.$..$..$..#",
        "#..........#",
        "#....@.....#",
        "#..$..$..$.#",
        "#..........#",
        "#..T..T..T.#",
        "############",
    ],
    # Level 16
    [
        "############",
        "#.T..T..T..#",
        "#.........##",
        "#.$..$..$..#",
        "#.........##",
        "#....@.....#",
        "#..$..$..$.#",
        "##.........#",
        "#..T..T..T.#",
        "############",
    ],
]


def parse_level(text: List[str]):
    """Returns (walls, targets, boxes, player, floors).

    ``floors`` is the set of every designed non-wall cell explicitly written
    in the level string. It is a map boundary, not a reachability proof. We
    use it to (a) prevent the player from walking off the map and
    (b) only render floor tiles inside the designed level area.
    """
    if not text or not text[0]:
        raise ValueError("level must not be empty")
    width = len(text[0])
    if any(len(row) != width for row in text):
        raise ValueError("level rows must have equal width")
    allowed = {"#", ".", "$", "*", "@", "+", "T", " "}
    walls, targets, boxes, floors = set(), set(), set(), set()
    players = []
    for y, row in enumerate(text):
        for x, ch in enumerate(row):
            if ch not in allowed:
                raise ValueError(f"unknown level symbol {ch!r} at ({x}, {y})")
            if ch == "#":
                walls.add((x, y))
                continue
            if ch == "T":
                targets.add((x, y))
            elif ch == "$":
                boxes.add((x, y))
            elif ch == "*":
                boxes.add((x, y))
                targets.add((x, y))
            elif ch == "@":
                players.append((x, y))
            elif ch == "+":
                players.append((x, y))
                targets.add((x, y))
            # Any non-wall, non-space cell is a valid floor.
            if ch != " ":
                floors.add((x, y))
    if len(players) != 1:
        raise ValueError(f"level must contain exactly one player, got {len(players)}")
    if len(boxes) != len(targets):
        raise ValueError("level must contain the same number of boxes and targets")
    if not boxes:
        raise ValueError("level must contain at least one box and target")
    return walls, targets, boxes, players[0], floors


def level_bounds(level: List[str]) -> Tuple[int, int]:
    h = len(level)
    w = max(len(r) for r in level)
    return w, h


class Sokoban(BaseGame):
    game_id = "sokoban"
    title = "推箱子"
    # Sokoban submits only a complete, unassisted run. Higher totals produced
    # by replaying a level update that run's attempt instead of adding one.
    submit_replaces_existing = True

    def __init__(self, backend: Optional[GameDataService] = None,
                 player: str = "anonymous",
                 profile_id: Optional[str] = None):
        self.level_idx = 0
        self.total_score = 0
        self.level_scores: dict[int, int] = {}
        self.completed_levels: set[int] = set()
        self.practice_mode = False
        self.unlocked_level = 1
        self.saved_completed_levels: set[int] = set()
        self._progress_generation = 0
        self._progress_future = None
        self._progress_write_future = None
        self._progress_status_key = "campaign"
        self.progress_save_message = ""
        self._confirmed_total: Optional[int] = None
        self._pending_total: Optional[int] = None
        w, h = level_bounds(LEVELS[0])
        super().__init__(max(500, w * CELL + 40), max(380, h * CELL + 132),
                         fps=60, backend=backend, player=player,
                         profile_id=profile_id)
        self.load_level(0)
        load_progress = getattr(self.backend, "get_progress_async", None)
        if callable(load_progress):
            self._progress_future = (
                load_progress(
                    self.profile_id, self.game_id, "campaign", {}),
                self._progress_generation)

    def _poll_progress(self) -> None:
        write = self._progress_write_future
        if write is not None and write[0].done():
            self._progress_write_future = None
            try:
                result = write[0].result()
            except Exception:  # noqa: BLE001
                self.progress_save_message = "进度暂时未保存"
            else:
                if isinstance(result, dict) and result.get("ok") is False:
                    self.progress_save_message = (
                        "进度已进入待写入队列" if result.get("durable_pending")
                        else "进度暂时未保存")
                elif isinstance(result, dict):
                    self._apply_progress(result, write[1])
                    self.progress_save_message = ""
        if self.progress_save_message:
            getter = getattr(self.backend, "get_local_state_status", None)
            if callable(getter):
                ruleset = GAME_BY_ID[self.game_id].ruleset_version
                event = getter(
                    f"progress:{self.profile_id}:{self.game_id}:"
                    f"{ruleset}:{self._progress_status_key}")
                if getattr(event, "state", None) == SaveState.COMMITTED:
                    self.progress_save_message = ""
        pending = self._progress_future
        if pending is None or not pending[0].done():
            return
        self._progress_future = None
        try:
            value = pending[0].result()
        except Exception:  # noqa: BLE001 - a new campaign remains playable
            return
        self._apply_progress(value, pending[1])

    def _apply_progress(self, value, generation: int) -> None:
        if generation != self._progress_generation:
            return
        if not isinstance(value, dict):
            return
        if isinstance(value.get("value"), dict):
            value = value["value"]
        unlocked = value.get("unlocked_level", 1)
        completed = value.get("completed_levels", [])
        if type(unlocked) is int:
            self.unlocked_level = min(len(LEVELS), max(1, unlocked))
        if (isinstance(completed, list)
                and all(type(item) is int and 0 <= item < len(LEVELS)
                        for item in completed)):
            self.saved_completed_levels = set(completed)

    def load_level(self, idx: int):
        self.level_idx = idx
        level = LEVELS[idx]
        # NOTE: parse_level returns the player POSITION as a tuple. We
        # must NOT assign it to ``self.player`` — that field is the
        # player NAME (a string) set by BaseGame.__init__ and used by
        # ``on_win`` to submit scores. Use ``self.player_pos`` instead.
        (self.walls, self.targets, self.boxes, self.player_pos,
         self.floors) = parse_level(level)
        self.moves = 0
        self.pushes = 0
        self.history: List[tuple] = []
        self.score = 0
        self.state = "playing"
        self.overlay_buttons = []
        # Loading level 0 starts a new run. Other reloads preserve the run's
        # per-level ledger so replaying a level can improve its score without
        # adding the same level twice.
        if idx == 0:
            self.begin_score_session()
            self.total_score = 0
            self.level_scores = {}
            self.completed_levels = set()
            self.practice_mode = False
            self._confirmed_total = None
            self._pending_total = None
        w, h = level_bounds(level)
        # Recreate window to fit
        new_w = max(500, w * CELL + 40)
        new_h = max(380, h * CELL + 132)
        if (new_w, new_h) != (self.width, self.height):
            self.width, self.height = new_w, new_h
            self.screen = pygame.display.set_mode((new_w, new_h))
        self.offset_x = (self.width - w * CELL) // 2
        self.offset_y = 66

    def update(self, dt: float):
        self._poll_progress()

    def handle_event(self, event):
        # Let BaseGame handle QUIT, ESC (return to launcher), P (pause),
        # and overlay-button clicks first.
        if super().handle_event(event):
            return
        if event.type != pygame.KEYDOWN:
            return
        # R (reset current level) works in any state.
        if event.key == pygame.K_r:
            self.request_destructive_action(
                "reset", lambda: self.load_level(self.level_idx))
            return
        # N advances normally from a win overlay. During active play it is an
        # explicit practice/skip action and cannot create a ranked clear.
        if event.key == pygame.K_n:
            if (self.state == "won"
                    and self.level_idx in self.completed_levels):
                self.request_destructive_action(
                    "advance", self._advance_after_win)
                return
            next_idx = (self.level_idx + 1) % len(LEVELS)
            self.load_level(next_idx)
            if next_idx != 0:
                self.practice_mode = True
            return
        if event.key == pygame.K_k and self.unlocked_level > 1:
            self.load_level(self.unlocked_level - 1)
            self.practice_mode = True
            return
        if event.key in (pygame.K_u, pygame.K_BACKSPACE):
            if self.state == "playing":
                self._undo()
            return
        # Arrow / WASD movement only when actively playing.
        if self.state != "playing":
            return
        d = None
        if event.key in (pygame.K_LEFT, pygame.K_a):
            d = (-1, 0)
        elif event.key in (pygame.K_RIGHT, pygame.K_d):
            d = (1, 0)
        elif event.key in (pygame.K_UP, pygame.K_w):
            d = (0, -1)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            d = (0, 1)
        if d:
            self._try_move(d)

    def _try_move(self, d):
        px, py = self.player_pos
        nx, ny = px + d[0], py + d[1]
        # Reject anything outside the designed floor area (walls + floors).
        if (nx, ny) not in self.floors:
            return
        old_state = (self.player_pos, set(self.boxes), self.moves, self.pushes)
        if (nx, ny) in self.boxes:
            bx, by = nx + d[0], ny + d[1]
            # Box destination must also be a valid floor cell.
            if (bx, by) not in self.floors or (bx, by) in self.boxes:
                return
            self.boxes.remove((nx, ny))
            self.boxes.add((bx, by))
            self.pushes += 1
        self.history.append(old_state)
        self.player_pos = (nx, ny)
        self.moves += 1
        self._check_win()

    def _undo(self) -> None:
        """Undo one successful walk/push without affecting earlier levels."""
        if not self.history:
            return
        (self.player_pos, boxes, self.moves, self.pushes) = self.history.pop()
        self.boxes = set(boxes)

    def _check_win(self):
        if self.boxes == self.targets:
            level_score = max(0, 1000 - self.moves)
            # Keep only this run's best score for each level. Replaying a
            # solved level can improve the total, never duplicate it.
            previous = self.level_scores.get(self.level_idx, 0)
            self.level_scores[self.level_idx] = max(previous, level_score)
            self.completed_levels.add(self.level_idx)
            self.total_score = sum(self.level_scores.values())
            self.score = self.total_score  # display the running total
            completed_all = (len(self.completed_levels) == len(LEVELS)
                             and not self.practice_mode)
            result = {"level": self.level_idx + 1,
                      "level_index": self.level_idx,
                      "level_score": level_score,
                      "counted_level_score": self.level_scores[self.level_idx],
                      "total_score": self.total_score,
                      "moves": self.moves,
                      "pushes": self.pushes,
                      "won": True,
                      "completed_levels": len(self.completed_levels),
                      "practice": self.practice_mode,
                      "completed_all": completed_all}
            save_progress = getattr(self.backend, "merge_progress_async", None)
            if callable(save_progress):
                try:
                    self._progress_generation += 1
                    generation = self._progress_generation
                    progress_value = {
                        "unlocked_level": min(
                            len(LEVELS), max(self.completed_levels) + 2),
                        "completed_levels": sorted(self.completed_levels),
                        "level_scores": {
                            str(key): value
                            for key, value in self.level_scores.items()}}
                    self.unlocked_level = max(
                        self.unlocked_level,
                        progress_value["unlocked_level"])
                    self.saved_completed_levels.update(
                        progress_value["completed_levels"])
                    progress_key = (
                        "practice" if self.practice_mode else "campaign")
                    self._progress_status_key = progress_key
                    self._progress_write_future = (save_progress(
                        self.profile_id, self.game_id,
                        progress_key, progress_value), generation, progress_key)
                except Exception:  # noqa: BLE001 - progress is non-critical
                    self.progress_save_message = "进度暂时未保存"
            if (completed_all
                    and (self._confirmed_total is None
                         or self.total_score > self._confirmed_total)
                    and self.total_score != self._pending_total):
                self._pending_total = self.total_score
                self.on_win(self.total_score, extra=result)
            else:
                # Intermediate/practice clears get the same result overlay,
                # but only a legitimate all-level run reaches the leaderboard.
                self.extra = result
                self.state = "won"
                self.invalidate_overlay_leaderboard()

    def on_score_save_succeeded(self, result: dict, payload: dict) -> None:
        self._confirmed_total = (
            payload["score"] if self._confirmed_total is None
            else max(self._confirmed_total, payload["score"]))
        self._pending_total = None

    def on_score_save_failed(self, payload: dict,
                             error: Optional[str]) -> None:
        self._pending_total = payload["score"]

    def _advance_after_win(self) -> None:
        next_idx = (self.level_idx + 1) % len(LEVELS)
        self.load_level(next_idx)

    def draw(self):
        self._poll_progress()
        draw_gradient_bg(self.screen, top=(252, 253, 255),
                         bottom=(221, 232, 255))
        header = pygame.Rect(12, 8, self.width - 24, 48)
        draw_panel(self.screen, header, fill=COLORS["panel"],
                   border=COLORS["game_sokoban"], radius=8, shadow=False)
        # HUD — title on the left, stats right-aligned to the window
        # edge so they never overlap the level grid (which is centered
        # and can be narrow on small levels like level 1).
        from client.common.ui import font as _font
        draw_text(self.screen, f"推箱子 · 关卡 {self.level_idx + 1}/{len(LEVELS)}",
                  (header.x + 12, header.y + 7), size=20,
                  color=COLORS["accent2"], bold=True)
        # Right-align step/push stats by their measured width.
        stats_line = f"步数 {self.moves}  推动 {self.pushes}  累计 {getattr(self, 'total_score', 0)}"
        sw = _font(15, bold=True).size(stats_line)[0]
        draw_text(self.screen, stats_line,
                  (header.right - sw - 12, header.y + 9), size=15,
                  color=COLORS["text"], bold=True)
        hint = (f"已解锁 {self.unlocked_level}/{len(LEVELS)} · "
                "K 前往最高关 · N 练习跳关 · Esc 返回")
        if self.progress_save_message:
            hint = f"{self.progress_save_message} · {hint}"
        hw = _font(11).size(hint)[0]
        draw_text(self.screen, hint,
                  (header.right - hw - 12, header.y + 29), size=11,
                  color=COLORS["text_dim"])

        # Floor — only render cells that are actually inside the level
        # (anything in ``self.floors``). This avoids painting floor tiles
        # outside the designed map.
        for (x, y) in self.floors:
            px = self.offset_x + x * CELL
            py = self.offset_y + y * CELL
            tile = pygame.Rect(px, py, CELL, CELL)
            fill = ((234, 241, 255) if (x + y) % 2 == 0
                    else (225, 235, 252))
            pygame.draw.rect(self.screen, fill, tile)
            pygame.draw.line(self.screen, (250, 252, 255),
                             tile.topleft, (tile.right, tile.top), 1)
            pygame.draw.line(self.screen, (188, 205, 231),
                             (tile.left, tile.bottom - 1),
                             (tile.right, tile.bottom - 1), 1)

        # Walls
        for wx, wy in self.walls:
            self._draw_wall(wx, wy)
        # targets
        for tx, ty in self.targets:
            self._draw_target(tx, ty)
        # boxes
        for bx, by in self.boxes:
            self._draw_box(bx, by, on_target=(bx, by) in self.targets)
        # player
        self._draw_player(*self.player_pos)

        # Footer
        draw_text(self.screen,
                  "方向键/WASD 移动 · U/退格 撤销 · R 重置 · N 跳关(练习) · Esc 退出",
                  (self.width // 2, self.height - 18),
                  size=12, color=COLORS["text_dim"], center=True)

        if self.state == "paused":
            self.draw_paused_overlay()
        elif self.state == "won":
            last = self.level_idx == len(LEVELS) - 1
            next_label = "返回第 1 关" if last else "下一关 (N)"
            btns = [
                Button(pygame.Rect(0, 0, 150, 36), next_label,
                       lambda: self.request_destructive_action(
                           "advance", self._advance_after_win), primary=True),
                Button(pygame.Rect(0, 0, 140, 36), "重玩本关 (R)",
                       lambda: self.request_destructive_action(
                           "reset", lambda: self.load_level(self.level_idx))),
                Button(pygame.Rect(0, 0, 140, 36), "返回菜单 (Esc)",
                       self.request_exit),
            ]
            completed_all = bool(self.extra
                                 and self.extra.get("completed_all"))
            if completed_all:
                msg = f"全部通关！总分 {self.total_score}"
            elif self.practice_mode:
                msg = f"练习完成 · 关卡 {self.level_idx + 1}"
            else:
                msg = f"通过 关卡 {self.level_idx + 1}"
            detail = (f"本关 +{max(0, 1000 - self.moves)}  ·  "
                      f"累计 {self.total_score}  ·  "
                      f"步数 {self.moves}  ·  推动 {self.pushes}")
            # Overlay now auto-sizes panel & positions buttons; we no
            # longer hand-position them here.
            self.draw_gameover_overlay(msg, buttons=btns, detail=detail)

    # ---------- sprites -------------------------------------------------
    def _draw_wall(self, x, y):
        px = self.offset_x + x * CELL
        py = self.offset_y + y * CELL
        rect = pygame.Rect(px, py, CELL, CELL)
        pygame.draw.rect(self.screen, (154, 174, 215), rect)
        stone = rect.inflate(-4, -4)
        pygame.draw.rect(self.screen, (118, 148, 211), stone, border_radius=5)
        pygame.draw.rect(self.screen, (187, 208, 246),
                         pygame.Rect(stone.x + 2, stone.y + 2,
                                     stone.w - 4, 5), border_radius=3)
        pygame.draw.rect(self.screen, (79, 107, 168), stone, 2,
                         border_radius=5)
        # Offset mortar line gives the wall a hand-built toy-block character.
        pygame.draw.line(self.screen, (91, 119, 179),
                         (stone.x, stone.centery),
                         (stone.right, stone.centery), 1)
        split = stone.centerx + (7 if y % 2 else -7)
        pygame.draw.line(self.screen, (91, 119, 179),
                         (split, stone.y), (split, stone.centery), 1)

    def _draw_target(self, x, y):
        px = self.offset_x + x * CELL + CELL // 2
        py = self.offset_y + y * CELL + CELL // 2
        pygame.draw.circle(self.screen, (245, 248, 255), (px, py), 14)
        pygame.draw.circle(self.screen, COLORS["accent2"], (px, py), 11, 2)
        pygame.draw.circle(self.screen, COLORS["danger"], (px, py), 5)
        for dx, dy in ((0, -15), (15, 0), (0, 15), (-15, 0)):
            pygame.draw.circle(self.screen, COLORS["accent2"],
                               (px + dx, py + dy), 2)

    def _draw_box(self, x, y, on_target=False):
        px = self.offset_x + x * CELL
        py = self.offset_y + y * CELL
        inset = 3
        color = COLORS["ok"] if on_target else (211, 147, 71)
        dark = (32, 103, 72) if on_target else (118, 68, 36)
        rect = pygame.Rect(px + inset, py + inset, CELL - 2 * inset,
                           CELL - 2 * inset)
        pygame.draw.rect(self.screen, color, rect, border_radius=4)
        pygame.draw.rect(self.screen, dark, rect, 2, border_radius=4)
        pygame.draw.rect(self.screen, tuple(min(255, c + 35) for c in color),
                         pygame.Rect(rect.x + 4, rect.y + 4,
                                     rect.w - 8, 4), border_radius=2)
        # Reinforced planks + brass center pin.
        pygame.draw.line(self.screen, dark, rect.topleft, rect.bottomright, 3)
        pygame.draw.line(self.screen, dark,
                         (rect.right, rect.y), (rect.x, rect.bottom), 3)
        pygame.draw.circle(self.screen, COLORS["accent2"], rect.center, 3)

    def _draw_player(self, x, y):
        px = self.offset_x + x * CELL + CELL // 2
        py = self.offset_y + y * CELL + CELL // 2
        pygame.draw.ellipse(self.screen, (144, 161, 190),
                            pygame.Rect(px - 15, py + 10, 30, 8))
        pygame.draw.circle(self.screen, COLORS["accent"], (px, py + 2), 15)
        pygame.draw.circle(self.screen, (111, 226, 209), (px, py - 5), 12)
        # Worker cap and face.
        pygame.draw.arc(self.screen, COLORS["accent2"],
                        pygame.Rect(px - 13, py - 17, 26, 17), 0, math.pi, 4)
        pygame.draw.line(self.screen, COLORS["accent2"],
                         (px - 14, py - 7), (px + 14, py - 7), 3)
        pygame.draw.circle(self.screen, (10, 28, 35), (px - 4, py - 2), 2)
        pygame.draw.circle(self.screen, (10, 28, 35), (px + 4, py - 2), 2)


def run_game(backend: Optional[GameDataService] = None,
             player: str = "anonymous",
             profile_id: Optional[str] = None) -> None:
    Sokoban(backend=backend, player=player, profile_id=profile_id).run()


if __name__ == "__main__":
    run_game()
