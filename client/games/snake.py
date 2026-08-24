"""Snake — classic grid snake game.

Controls:
  ←↑→↓ / WASD   direction
  P             pause
  R             restart after game over
  Esc           quit
"""
from __future__ import annotations

import random
from collections import deque
from typing import Optional, Tuple

import pygame

from client.common.network import BackendClient
from client.common.ui import (COLORS, BaseGame, Button, draw_gradient_bg,
                              draw_text)

CELL = 24
COLS, ROWS = 24, 22
BOARD_W, BOARD_H = COLS * CELL, ROWS * CELL
PANEL_W = 220
WIDTH = BOARD_W + PANEL_W + 40
HEIGHT = BOARD_H + 40
BOARD_X = 20
BOARD_Y = 20
SNAKE_INITIAL_SPEED = 7.0  # grid steps/sec; old game started at 12
SNAKE_MAX_SPEED = 20.0
SNAKE_FOOD_PER_LEVEL = 5
SNAKE_STALL_DT = 0.25
BOARD_COLOR_A = (255, 248, 224)
BOARD_COLOR_B = (248, 239, 207)


def snake_speed_for_level(level: int) -> float:
    """Map each displayed level to a deterministic movement speed."""
    return min(SNAKE_MAX_SPEED, SNAKE_INITIAL_SPEED + max(0, level - 1))


class Snake(BaseGame):
    game_id = "snake"
    title = "贪吃蛇"

    def __init__(self, backend: Optional[BackendClient] = None,
                 player: str = "anonymous"):
        # Render and process input at 60 FPS; movement has its own timer.
        # Tying both to the old 12 FPS movement rate made early input feel
        # abrupt and prevented us from expressing an explicit level curve.
        super().__init__(WIDTH, HEIGHT, fps=60, backend=backend, player=player)
        self.reset()

    def reset(self):
        self.begin_score_session()
        cx, cy = COLS // 2, ROWS // 2
        self.body: deque = deque([(cx, cy), (cx - 1, cy), (cx - 2, cy)])
        self.direction = (1, 0)
        self.turn_queue: deque = deque(maxlen=2)
        # Compatibility/status view: the last direction waiting to run.
        self.pending_direction = (1, 0)
        self.food: Optional[Tuple[int, int]] = self._spawn_food()
        self.score = 0
        self.state = "playing"
        self.level = 1
        self.move_speed = snake_speed_for_level(self.level)
        self.move_timer = 0.0
        self.overlay_buttons = []

    def _spawn_food(self) -> Optional[Tuple[int, int]]:
        occupied = set(self.body)
        empties = [(x, y) for y in range(ROWS) for x in range(COLS)
                   if (x, y) not in occupied]
        return random.choice(empties) if empties else None

    def update(self, dt: float):
        if self.state != "playing":
            return
        interval = 1.0 / self.move_speed
        stalled = dt > SNAKE_STALL_DT
        if stalled:
            # After a visible application stall, advance once and discard the
            # hidden backlog. Several unseen grid steps can otherwise kill the
            # player before the first recovered frame is drawn.
            self.move_timer = interval
        else:
            self.move_timer += dt
        # Cap catch-up work after a long stall, while retaining enough time
        # for ordinary low-frame-rate updates to produce every grid step.
        self.move_timer = min(self.move_timer, 4.0 * interval)
        steps = 0
        while self.state == "playing" and steps < (1 if stalled else 4):
            # Eating can raise move_speed inside _step(); derive the next
            # interval from the new level instead of keeping a stale value for
            # the rest of this catch-up frame.
            interval = 1.0 / self.move_speed
            if self.move_timer < interval:
                break
            self.move_timer -= interval
            self._step()
            steps += 1

    def _step(self) -> None:
        if self.turn_queue:
            self.direction = self.turn_queue.popleft()
        self.pending_direction = (self.turn_queue[-1]
                                  if self.turn_queue else self.direction)
        hx, hy = self.body[0]
        nx, ny = hx + self.direction[0], hy + self.direction[1]
        # Wall collision
        if nx < 0 or nx >= COLS or ny < 0 or ny >= ROWS:
            self.state = "gameover"
            self.on_game_over(self.score, extra={"length": len(self.body),
                                                  "level": self.level,
                                                  "speed": self.move_speed})
            return
        # Self collision
        if (nx, ny) in self.body and (nx, ny) != self.body[-1]:
            self.state = "gameover"
            self.on_game_over(self.score, extra={"length": len(self.body),
                                                  "level": self.level,
                                                  "speed": self.move_speed})
            return
        self.body.appendleft((nx, ny))
        if (nx, ny) == self.food:
            self.score += 10
            self.level = 1 + (self.score // 10) // SNAKE_FOOD_PER_LEVEL
            self.move_speed = snake_speed_for_level(self.level)
            if len(self.body) == COLS * ROWS:
                self.food = None
                self.on_win(self.score, extra={"length": len(self.body),
                                                "filled_board": True,
                                                "level": self.level,
                                                "speed": self.move_speed})
                return
            self.food = self._spawn_food()
        else:
            self.body.pop()

    def handle_event(self, event):
        # A turn is only committed on the next movement tick.  Discard that
        # uncommitted input when pausing or losing focus; otherwise the snake
        # can make a surprising turn immediately after the player resumes.
        if (event.type == getattr(pygame, "WINDOWFOCUSLOST", -1)
                or (event.type == pygame.KEYDOWN
                    and event.key == pygame.K_p)):
            self.pending_direction = self.direction
            self.turn_queue.clear()
        if super().handle_event(event):
            return
        if event.type == pygame.KEYDOWN and self.state == "playing":
            candidate = None
            if event.key in (pygame.K_LEFT, pygame.K_a):
                candidate = (-1, 0)
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                candidate = (1, 0)
            elif event.key in (pygame.K_UP, pygame.K_w):
                candidate = (0, -1)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                candidate = (0, 1)
            # Validate against the newest queued turn. This preserves quick
            # corners such as Right -> Up -> Left instead of dropping Left
            # before the next movement tick.
            base = (self.turn_queue[-1]
                    if self.turn_queue else self.direction)
            if (candidate and candidate != base
                    and candidate != (-base[0], -base[1])
                    and len(self.turn_queue) < self.turn_queue.maxlen):
                self.turn_queue.append(candidate)
                self.pending_direction = candidate
        elif event.type == pygame.KEYDOWN and self.state in ("gameover", "won"):
            if event.key == pygame.K_r:
                self.reset()

    def draw(self):
        draw_gradient_bg(self.screen)
        # Board
        board_rect = pygame.Rect(BOARD_X, BOARD_Y, BOARD_W, BOARD_H)
        pygame.draw.rect(self.screen, BOARD_COLOR_A, board_rect)
        pygame.draw.rect(self.screen, COLORS["game_snake"], board_rect, 2)

        # subtle checker pattern
        for y in range(ROWS):
            for x in range(COLS):
                if (x + y) % 2 == 0:
                    pygame.draw.rect(
                        self.screen, BOARD_COLOR_B,
                        pygame.Rect(BOARD_X + x * CELL, BOARD_Y + y * CELL,
                                    CELL, CELL))

        # Food
        if self.food is not None:
            fx, fy = self.food
            cx = BOARD_X + fx * CELL + CELL // 2
            cy = BOARD_Y + fy * CELL + CELL // 2
            pygame.draw.circle(self.screen, COLORS["danger"], (cx, cy),
                               CELL // 2 - 2)
            pygame.draw.circle(self.screen, (255, 200, 200),
                               (cx - 3, cy - 3), 3)

        # Snake
        for i, (x, y) in enumerate(self.body):
            color = (56, 196, 126) if i == 0 else (37, 164, 99)
            rect = pygame.Rect(BOARD_X + x * CELL + 1,
                               BOARD_Y + y * CELL + 1, CELL - 2, CELL - 2)
            pygame.draw.rect(self.screen, color, rect, border_radius=4)
            if i == 0:
                # Eyes follow the actual direction; the old fixed top pair
                # made the head appear to face upward while moving sideways
                # or down.
                if self.direction == (1, 0):
                    eyes = [(rect.right - 6, rect.y + 6),
                            (rect.right - 6, rect.bottom - 6)]
                elif self.direction == (-1, 0):
                    eyes = [(rect.x + 6, rect.y + 6),
                            (rect.x + 6, rect.bottom - 6)]
                elif self.direction == (0, 1):
                    eyes = [(rect.x + 6, rect.bottom - 6),
                            (rect.right - 6, rect.bottom - 6)]
                else:
                    eyes = [(rect.x + 6, rect.y + 6),
                            (rect.right - 6, rect.y + 6)]
                for eye in eyes:
                    pygame.draw.circle(self.screen, (20, 30, 20), eye, 2)

        # Side panel
        self._draw_side_panel()

        if self.state == "paused":
            self.draw_paused_overlay()
        elif self.state in ("gameover", "won"):
            btns = [
                Button(pygame.Rect(0, 0, 130, 36), "重新开始 (R)",
                       self.reset, primary=True),
                Button(pygame.Rect(0, 0, 130, 36), "返回菜单 (Esc)",
                       lambda: setattr(self, "running", False)),
            ]
            message = "全盘吃满，胜利！" if self.state == "won" else "游戏结束"
            self.draw_gameover_overlay(message, buttons=btns)

    def _draw_side_panel(self):
        px = BOARD_X + BOARD_W + 20
        py = BOARD_Y
        rect = pygame.Rect(px, py, PANEL_W, 110)
        pygame.draw.rect(self.screen, COLORS["panel"], rect, border_radius=8)
        pygame.draw.rect(self.screen, COLORS["border"], rect, 1, border_radius=8)
        draw_text(self.screen, "得分", (rect.x + 16, rect.y + 14),
                  size=14, color=COLORS["text_dim"])
        draw_text(self.screen, str(self.score), (rect.x + 16, rect.y + 32),
                  size=30, color=COLORS["accent2"], bold=True)

        rect2 = pygame.Rect(px, py + 124, PANEL_W, 130)
        pygame.draw.rect(self.screen, COLORS["panel"], rect2, border_radius=8)
        pygame.draw.rect(self.screen, COLORS["border"], rect2, 1, border_radius=8)
        draw_text(self.screen, f"长度: {len(self.body)}",
                  (rect2.x + 16, rect2.y + 14), size=15, color=COLORS["text"])
        draw_text(self.screen, f"等级: {self.level}",
                  (rect2.x + 16, rect2.y + 40), size=15, color=COLORS["text"])
        draw_text(self.screen, f"速度: {self.move_speed:g} 格/秒",
                  (rect2.x + 16, rect2.y + 64), size=15, color=COLORS["text"])
        draw_text(self.screen, "方向键/WASD 移动",
                  (rect2.x + 16, rect2.y + 94), size=12,
                  color=COLORS["text_dim"])
        draw_text(self.screen, "P 暂停 · Esc 退出",
                  (rect2.x + 16, rect2.y + 112), size=12,
                  color=COLORS["text_dim"])


def run_game(backend: Optional[BackendClient] = None,
             player: str = "anonymous") -> None:
    Snake(backend=backend, player=player).run()


if __name__ == "__main__":
    run_game()
