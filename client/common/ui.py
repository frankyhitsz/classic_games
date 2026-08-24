"""Shared UI helpers used across every game client.

Buttons, centered text, color palettes, leaderboard panels, and a small
``BaseGame`` skeleton that handles the pygame event loop, FPS, ESC-to-quit,
and an end-of-game ``GameOverOverlay`` that talks to the backend to submit
the score and show the leaderboard.
"""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional

import pygame

from game_service.local_backend import LocalBackendClient
from game_service.service import (AttemptContext, GameDataService, SaveState,
                                  parse_score_response)

OVERLAY_INPUT_GUARD_MS = 180
DESTRUCTIVE_CONFIRM_MS = 3000
SAVE_IDLE = "idle"
SAVE_SAVING = "saving"
SAVE_SAVED = "saved"
SAVE_PENDING = "pending"
SAVE_FAILED = "failed"

# ----------------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------------
COLORS = {
    # Bright "after-school toy table": clean sky, white toy boxes, dark ink,
    # and independent candy accents instead of one dominant dark green.
    "bg": (221, 238, 255),
    "bg2": (249, 251, 255),
    "panel": (255, 255, 255),
    "panel_light": (241, 246, 255),
    "panel_hover": (229, 239, 255),
    "text": (37, 50, 78),
    "text_dim": (103, 119, 146),
    "accent": (75, 139, 245),
    "accent2": (255, 194, 71),
    "ok": (67, 190, 135),
    "danger": (247, 102, 124),
    "border": (188, 207, 232),
    "gold": (242, 172, 43),
    "silver": (142, 157, 180),
    "bronze": (199, 123, 68),
    # Per-game accent colors for the launcher card theming.
    "game_tetris":  (75, 139, 245),
    "game_snake":   (61, 190, 126),
    "game_2048":    (247, 145, 72),
    "game_sokoban": (222, 154, 48),
    "game_zuma":    (222, 91, 175),
}

# Game-specific color tables (RGB). Keep them centralized so the launcher
# and each game render identically.
GAME_COLORS = {
    "tetris": [(0, 230, 230), (230, 230, 60), (160, 100, 240), (90, 200, 90),
               (230, 130, 60), (230, 70, 70), (60, 130, 230)],
    "snake": [(110, 220, 145), (60, 180, 90), (240, 100, 110), (255, 196, 87)],
    "2048": [(220, 215, 200), (230, 195, 130), (240, 165, 90), (240, 130, 65),
             (235, 95, 50), (225, 70, 40), (210, 50, 40), (200, 40, 130),
             (90, 50, 150), (50, 50, 150)],
    "sokoban": [(160, 120, 80), (220, 100, 60), (240, 200, 70), (130, 90, 50)],
    "zuma": [(230, 70, 70), (70, 170, 230), (90, 200, 90), (240, 200, 70),
             (180, 100, 230), (250, 250, 250)],
}


# ----------------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------------
_font_cache: dict = {}


def font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    if key not in _font_cache:
        # Prefer CJK-capable fonts so Chinese text renders on macOS.
        candidates = [
            "pingfangsc", "pingfang sc", "hiragino sans gb",
            "stheiti", "microsoft yahei", "arialunicodems",
            "arial", None,
        ]
        name = next((c for c in candidates
                     if c is None or pygame.font.match_font(c)), None)
        f = pygame.font.SysFont(name, size, bold=bold)
        _font_cache[key] = f
    return _font_cache[key]


def mono_font(size: int, bold: bool = False) -> pygame.font.Font:
    """Utility face for cabinet numbers, scores, and compact data labels."""
    key = ("mono", size, bold)
    if key not in _font_cache:
        name = next((candidate for candidate in
                     ("menlo", "sfmono-regular", "monaco", "couriernew")
                     if pygame.font.match_font(candidate)), None)
        _font_cache[key] = pygame.font.SysFont(name, size, bold=bold)
    return _font_cache[key]


def draw_text(surf, text, pos, size=18, color=None, bold=False, center=False):
    color = color or COLORS["text"]
    txt = font(size, bold=bold).render(text, True, color)
    rect = txt.get_rect()
    if center:
        rect.center = pos
    else:
        rect.topleft = pos
    surf.blit(txt, rect)
    return rect


def fit_text(text: str, text_font: pygame.font.Font, max_width: int) -> str:
    """Pixel-fit text with an ellipsis instead of overlapping neighbours."""
    if max_width <= 0:
        return ""
    if text_font.size(text)[0] <= max_width:
        return text
    ellipsis = "…"
    value = text
    while value and text_font.size(value + ellipsis)[0] > max_width:
        value = value[:-1]
    return value + ellipsis if value else ""


# ----------------------------------------------------------------------------
# Background gradient & panel helpers (visual polish)
# ----------------------------------------------------------------------------
def draw_gradient_bg(surf, rect=None, top=None, bottom=None) -> None:
    """Paint a vertical gradient using the shared bright sky palette."""
    rect = rect or surf.get_rect()
    top = top or COLORS["bg2"]
    bottom = bottom or COLORS["bg"]
    h = rect.height
    if h <= 0:
        return
    for y in range(rect.top, rect.bottom):
        t = (y - rect.top) / max(1, h - 1)
        c = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
        pygame.draw.line(surf, c, (rect.left, y), (rect.right, y))


def draw_playroom_backdrop(surf) -> None:
    """Draw a bright paper-and-toy backdrop for the launcher."""
    rect = surf.get_rect()
    draw_gradient_bg(surf, top=(250, 252, 255), bottom=(217, 237, 255))
    paper = pygame.Surface(rect.size, pygame.SRCALPHA)
    # Two broad sheets establish depth without darkening the page.
    pygame.draw.polygon(paper, (132, 185, 255, 28),
                        [(0, 430), (280, 380), (570, 455),
                         (rect.w, 392), (rect.w, rect.h), (0, rect.h)])
    pygame.draw.polygon(paper, (215, 142, 255, 20),
                        [(0, 535), (240, 470), (520, 540),
                         (810, 475), (rect.w, 510),
                         (rect.w, rect.h), (0, rect.h)])
    confetti = [COLORS["game_tetris"], COLORS["game_snake"],
                COLORS["game_2048"], COLORS["game_zuma"]]
    for index in range(26):
        x = (index * 139 + 53) % rect.w
        y = 18 + (index * 83) % (rect.h - 36)
        color = confetti[index % len(confetti)]
        pygame.draw.circle(paper, (*color, 24), (x, y), 2 + index % 2)
    surf.blit(paper, (0, 0))


def draw_panel(surf, rect, fill=None, border=None, radius=10,
               shadow=True) -> None:
    """Rounded panel with optional drop shadow. ``fill`` defaults to
    white, ``border`` to the shared pale-blue border."""
    fill = fill or COLORS["panel"]
    border = border or COLORS["border"]
    if shadow:
        shadow_rect = rect.move(0, 3)
        shadow_layer = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        pygame.draw.rect(shadow_layer, (41, 69, 111, 30),
                         pygame.Rect(0, 0, rect.w, rect.h),
                         border_radius=radius)
        surf.blit(shadow_layer, shadow_rect.topleft)
    pygame.draw.rect(surf, fill, rect, border_radius=radius)
    pygame.draw.rect(surf, border, rect, 1, border_radius=radius)


def draw_card(surf, rect, hover: bool, accent: tuple = None,
              selected: bool = False) -> None:
    """Game-card visual: drop shadow, rounded body, accent border on
    hover/selected. ``accent`` is the per-game accent color tuple."""
    accent = accent or COLORS["accent"]
    body = COLORS["panel_hover"] if hover else COLORS["panel"]
    if selected:
        body = COLORS["panel_hover"]
    # Shadow
    shadow = pygame.Surface((rect.w + 8, rect.h + 8), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (46, 75, 122, 48 if hover else 28),
                     pygame.Rect(3, 3, rect.w, rect.h), border_radius=12)
    surf.blit(shadow, (rect.x - 3, rect.y))
    # Body with subtle vertical gradient
    grad = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    draw_gradient_bg(grad, grad.get_rect(),
                     top=tuple(min(255, c + 12) for c in body),
                     bottom=body)
    # Use the gradient via a mask (rounded rect)
    mask = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255),
                     pygame.Rect(0, 0, rect.w, rect.h), border_radius=12)
    grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    surf.blit(grad, rect.topleft)
    # Border (thicker & accent-colored on hover/selected)
    if selected:
        pygame.draw.rect(surf, COLORS["accent2"], rect, 2, border_radius=12)
    elif hover:
        pygame.draw.rect(surf, accent, rect, 2, border_radius=12)
    else:
        pygame.draw.rect(surf, COLORS["border"], rect, 1, border_radius=12)


# ----------------------------------------------------------------------------
# Easing functions (used for 2048 slide/spawn/merge animations)
# ----------------------------------------------------------------------------
def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_out_back(t: float, s: float = 1.5) -> float:
    t = max(0.0, min(1.0, t))
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


# ----------------------------------------------------------------------------
# Button
# ----------------------------------------------------------------------------
@dataclass
class Button:
    rect: pygame.Rect
    label: str
    callback: Callable[[], None]
    primary: bool = False

    def draw(self, surf, hover: bool):
        if self.primary:
            top = COLORS["accent"] if not hover else (140, 190, 255)
            bottom = tuple(max(0, c - 35) for c in top)
            text_color = (20, 25, 40)
            border = top
        else:
            top = COLORS["panel_light"] if not hover else COLORS["panel_hover"]
            bottom = tuple(max(0, c - 18) for c in top)
            text_color = COLORS["text"]
            border = COLORS["accent"] if hover else COLORS["border"]
        # Vertical-gradient body.
        body = pygame.Surface((self.rect.w, self.rect.h), pygame.SRCALPHA)
        draw_gradient_bg(body, body.get_rect(), top=top, bottom=bottom)
        mask = pygame.Surface((self.rect.w, self.rect.h), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255),
                         pygame.Rect(0, 0, self.rect.w, self.rect.h),
                         border_radius=8)
        body.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(body, self.rect.topleft)
        pygame.draw.rect(surf, border, self.rect, 1, border_radius=8)
        if hover:
            # Glow ring
            glow = pygame.Surface((self.rect.w + 8, self.rect.h + 8),
                                  pygame.SRCALPHA)
            pygame.draw.rect(glow, (*COLORS["accent"], 55),
                             pygame.Rect(0, 0, self.rect.w + 8, self.rect.h + 8),
                             border_radius=12)
            surf.blit(glow, (self.rect.x - 4, self.rect.y - 4),
                      special_flags=pygame.BLEND_RGBA_ADD)
            surf.blit(body, self.rect.topleft)
            pygame.draw.rect(surf, border, self.rect, 2, border_radius=8)
        draw_text(surf, self.label, self.rect.center, size=17,
                  color=text_color, bold=self.primary, center=True)

    def handle(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.callback()
                return True
        return False

    def hovered_by(self, pos) -> bool:
        return self.rect.collidepoint(pos)


# ----------------------------------------------------------------------------
# Leaderboard panel
# ----------------------------------------------------------------------------
def draw_leaderboard(surf, rect: pygame.Rect, entries: List[dict],
                     title: str = "排行榜", show_game: bool = False,
                     game_names: Optional[dict] = None,
                     competitive: bool = True):
    """Render a leaderboard panel.

    ``show_game=True`` shows the game name (looked up in ``game_names``
    by ``game_id``) before each entry's player/score — used for the
    launcher's "recent games" panel where entries span multiple games.
    """
    draw_panel(surf, rect)
    title_rect = pygame.Rect(rect.x, rect.y, rect.w, 36)
    # Subtle accent strip under the title.
    pygame.draw.rect(surf, COLORS["accent"], pygame.Rect(
        rect.x + 16, rect.y + 32, rect.w - 32, 2))
    draw_text(surf, title, (title_rect.centerx, title_rect.centery),
              size=18, color=COLORS["accent2"], bold=True, center=True)
    # Clip drawing to the panel so an over-long list can never bleed past
    # the bottom border. Only as many rows as fit are rendered.
    clip = surf.get_clip()
    inner = pygame.Rect(rect.x + 1, rect.y + 38, rect.w - 2, rect.h - 40)
    surf.set_clip(inner)
    y = rect.y + 44
    if not entries:
        draw_text(surf, "（暂无记录）", (rect.centerx, y + 12), size=14,
                  color=COLORS["text_dim"], center=True)
        surf.set_clip(clip)
        return
    row_h = 22
    max_rows = max(1, (inner.h - 4) // row_h)
    medal_colors = [COLORS["gold"], COLORS["silver"], COLORS["bronze"]]
    for i, e in enumerate(entries[:max_rows]):
        rank = e.get("rank", i + 1)
        player = (e.get("player") or "anon")[:32]
        score = e.get("score", 0)
        if competitive and type(rank) is int and 1 <= rank <= 3:
            medal = ["1st", "2nd", "3rd"][rank - 1]
            medal_color = medal_colors[rank - 1]
            medal_bold = True
        elif competitive:
            medal = f"#{rank}"
            medal_color = COLORS["text_dim"]
            medal_bold = False
        else:
            medal = "•"
            medal_color = COLORS["accent"]
            medal_bold = False
        # Render medal, name, score as separate text runs so we can
        # color the medal and the game-name tag differently.
        x = rect.x + 14
        draw_text(surf, medal, (x, y), size=15, color=medal_color,
                  bold=medal_bold)
        x += 52
        if show_game:
            gid = e.get("game_id", "")
            gname = fit_text((game_names or {}).get(gid, gid), font(12), 58)
            draw_text(surf, f"[{gname}]", (x, y), size=12,
                      color=COLORS["accent"])
            x += 64
        score_str = f"{score}"
        score_x = rect.right - 14 - font(15).size(score_str)[0]
        player = fit_text(player, font(15), max(0, score_x - x - 8))
        draw_text(surf, player, (x, y), size=15, color=COLORS["text"])
        draw_text(surf, score_str,
                  (score_x, y),
                  size=15,
                  color=(COLORS["accent2"]
                         if competitive and rank == 1 else COLORS["text"]))
        y += row_h
    surf.set_clip(clip)


# ----------------------------------------------------------------------------
# BaseGame skeleton
# ----------------------------------------------------------------------------
class BaseGame(abc.ABC):
    """Common scaffolding: window setup, loop, ESC handling, restart flow.

    Subclasses implement ``update`` and ``draw``. Mouse clicks on overlay
    buttons (drawn via ``draw_gameover_overlay``/``draw_paused_overlay``)
    are routed automatically by ``handle_event`` — games must NOT call
    ``pygame.event.get()`` inside their ``draw`` method.
    """

    game_id: str = ""
    title: str = "Game"
    # If True, later confirmed totals in the same run update one attempt.
    # A new run still creates its own history row; personal best is derived
    # from attempts instead of deleting lower completed runs.
    submit_replaces_existing: bool = False

    def __init__(self, width: int, height: int, fps: int = 60,
                 backend: Optional[GameDataService] = None,
                 player: str = "anonymous",
                 profile_id: Optional[str] = None):
        pygame.init()
        self.screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption(self.title)
        self.clock = pygame.time.Clock()
        self.fps = fps
        self._owns_backend = backend is None
        self.backend = backend if backend is not None else LocalBackendClient()
        self.player = player
        self.attempt_context = AttemptContext.for_game(
            self.game_id, player, profile_id=profile_id)
        self.profile_id = self.attempt_context.profile_id
        ensure_profile = getattr(self.backend, "ensure_profile_async", None)
        self._profile_ensure_future = (
            ensure_profile(self.player, self.profile_id)
            if callable(ensure_profile) else None)
        self.running = True
        self.state = "playing"  # playing | paused | gameover | won
        self.score = 0
        self.extra = None
        self.width = width
        self.height = height
        # Overlay buttons currently on screen. Populated by
        # ``draw_gameover_overlay``/``draw_paused_overlay``; clicks are
        # dispatched in ``handle_event`` when state != "playing".
        self.overlay_buttons: List[Button] = []
        # Game-over overlays are drawn every frame.  Keep their leaderboard
        # result here so drawing 60 FPS does not also issue 60 data reads.
        self._overlay_lb_key = None
        self._overlay_leaderboard: List[dict] = []
        self._overlay_lb_error: Optional[str] = None
        self._overlay_lb_future = None
        self._overlay_lb_generation = 0
        self._overlay_lb_future_generation = 0
        self._score_submit_future = None
        self._score_submit_generation = 0
        self._score_submit_future_generation = 0
        self._score_submit_active_payload = None
        self._last_score_payload = None
        self._score_submission_id: Optional[int] = None
        self._score_attempt_uuid = self.attempt_context.attempt_uuid
        self._score_attempt_revision = self.attempt_context.revision
        self.score_save_state = SAVE_IDLE
        self.score_save_error: Optional[str] = None
        self.score_save_retryable = True
        self.score_save_message = ""
        self.score_save_durable_pending = False
        self._discard_unsaved_armed = False
        self._destructive_action_armed: Optional[str] = None
        self._destructive_action_deadline = 0
        # Restart/continue buttons share the same mouse with some games.
        # Briefly swallow a rapid second click after an overlay action so a
        # double-click cannot fire a Zuma ball or begin a 2048 swipe.
        self._overlay_mouse_guard_until = 0

    # subclasses implement these:
    @abc.abstractmethod
    def update(self, dt: float) -> None: ...

    @abc.abstractmethod
    def draw(self) -> None: ...

    def handle_event(self, event) -> bool:
        """Handle shared events and report whether the event was consumed.

        Subclasses return immediately when this method returns ``True``.
        That prevents a click on a restart/continue overlay button from also
        reaching the newly-reset game underneath the overlay.
        """
        self._poll_score_submission()
        if (event.type == pygame.KEYDOWN and event.key == pygame.K_s
                and self.state in ("gameover", "won")
                and self.score_save_state == SAVE_FAILED
                and self.score_save_retryable):
            self.retry_score_save()
            return True
        if (event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP)
                and getattr(event, "button", None) == 1
                and pygame.time.get_ticks() < self._overlay_mouse_guard_until):
            return True
        if event.type == pygame.QUIT:
            self.request_exit()
            return True
        if event.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
            # Timed games should not keep moving while the player is in
            # another app.  Resume remains explicit via P.
            if self.state == "playing":
                self.state = "paused"
                self.overlay_buttons = []
            return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.request_exit()
                return True
            if event.key == pygame.K_p:
                if self.state == "playing":
                    self.state = "paused"
                    self.overlay_buttons = []
                elif self.state == "paused":
                    self.state = "playing"
                    self.overlay_buttons = []
                return True
        # Route mouse clicks to overlay buttons whenever we're showing
        # an overlay (gameover / won / paused).
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.state in ("gameover", "won", "paused")):
            activated = False
            for b in self.overlay_buttons:
                if b.handle(event):
                    activated = True
                    break
            if activated:
                self._overlay_mouse_guard_until = (
                    pygame.time.get_ticks() + OVERLAY_INPUT_GUARD_MS)
            # The overlay itself consumes the click even when it missed a
            # button.  Otherwise a callback that changes state to "playing"
            # can make this same click fire a shot in the subclass.
            return True
        return False

    def invalidate_overlay_leaderboard(self) -> None:
        """Force one fresh leaderboard fetch for the next result overlay."""
        self._overlay_lb_key = None
        self._overlay_leaderboard = []
        self._overlay_lb_error = None
        self._overlay_lb_future = None
        self._overlay_lb_generation += 1

    def begin_score_session(self) -> None:
        """Detach UI state from an older run without cancelling its save."""
        self._score_submit_generation += 1
        self._score_submit_future = None
        self._score_submit_active_payload = None
        self._last_score_payload = None
        self._score_submission_id = None
        self.attempt_context = AttemptContext.for_game(
            self.game_id, self.player, profile_id=self.profile_id)
        self._score_attempt_uuid = self.attempt_context.attempt_uuid
        self._score_attempt_revision = self.attempt_context.revision
        self.score_save_state = SAVE_IDLE
        self.score_save_error = None
        self.score_save_retryable = True
        self.score_save_message = ""
        self.score_save_durable_pending = False
        self._discard_unsaved_armed = False
        self._destructive_action_armed = None
        self._destructive_action_deadline = 0
        self.invalidate_overlay_leaderboard()

    def request_destructive_action(self, name: str,
                                   action: Callable[[], None]) -> bool:
        """Apply one save-aware guard to quit, reset, keys, and buttons."""
        unsafe = (self.score_save_state == SAVE_SAVING
                  or (self.score_save_state == SAVE_FAILED
                      and not self.score_save_durable_pending))
        now = pygame.time.get_ticks()
        confirmation_active = (
            self._destructive_action_armed == name
            and now <= self._destructive_action_deadline)
        if unsafe and not confirmation_active:
            self._destructive_action_armed = name
            self._destructive_action_deadline = now + DESTRUCTIVE_CONFIRM_MS
            self._discard_unsaved_armed = True
            return False
        self._destructive_action_armed = None
        self._destructive_action_deadline = 0
        self._discard_unsaved_armed = False
        action()
        return True

    def request_exit(self) -> bool:
        return self.request_destructive_action(
            "exit", lambda: setattr(self, "running", False))

    def request_reset(self) -> bool:
        return self.request_destructive_action("reset", self.reset)

    def _submit_result_score(self, score: int, extra=None,
                             request_id: Optional[str] = None,
                             revision: Optional[int] = None) -> None:
        if not self.backend or not self.game_id:
            return
        if revision is None:
            revision = self._next_score_revision()
        payload = {"score": score, "extra": extra,
                   "replace": self.submit_replaces_existing,
                   "request_id": request_id or uuid.uuid4().hex,
                   "attempt_uuid": self._score_attempt_uuid,
                   "revision": revision,
                   "submission_id": (self._score_submission_id
                                     if self.submit_replaces_existing
                                     else None),
                   **self.attempt_context.as_submit_kwargs()}
        self._last_score_payload = payload
        self._score_submit_generation += 1
        generation = self._score_submit_generation
        self._score_submit_active_payload = payload
        self.score_save_state = SAVE_SAVING
        self.score_save_error = None
        self.score_save_retryable = True
        self.score_save_message = ""
        self.score_save_durable_pending = False
        self._discard_unsaved_armed = False
        submit_async = getattr(
            self.backend, "submit_score_reliable_async", None)
        if not callable(submit_async):
            submit_async = getattr(self.backend, "submit_score_async", None)
        if callable(submit_async):
            try:
                self._score_submit_future = submit_async(
                    self.game_id, self.player, score, extra=extra,
                    replace=self.submit_replaces_existing,
                    submission_id=payload["submission_id"],
                    request_id=payload["request_id"],
                    attempt_uuid=payload["attempt_uuid"],
                    revision=payload["revision"],
                    profile_id=payload["profile_id"], mode=payload["mode"],
                    ruleset_version=payload["ruleset_version"],
                    status=payload["status"])
                self._score_submit_future_generation = generation
            except Exception as exc:  # noqa: BLE001
                self._finish_score_submission(None, payload, generation,
                                              str(exc))
        else:
            try:
                result = self.backend.submit_score(
                    self.game_id, self.player, score, extra=extra,
                    replace=self.submit_replaces_existing,
                    submission_id=payload["submission_id"],
                    request_id=payload["request_id"],
                    attempt_uuid=payload["attempt_uuid"],
                    revision=payload["revision"],
                    profile_id=payload["profile_id"], mode=payload["mode"],
                    ruleset_version=payload["ruleset_version"],
                    status=payload["status"])
            except Exception as exc:  # noqa: BLE001
                self._finish_score_submission(None, payload, generation,
                                              str(exc))
            else:
                self._finish_score_submission(result, payload, generation)

    def _poll_score_submission(self) -> None:
        future = self._score_submit_future
        if future is not None and future.done():
            generation = self._score_submit_future_generation
            payload = self._score_submit_active_payload
            self._score_submit_future = None
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                self._finish_score_submission(None, payload, generation, str(exc))
            else:
                self._finish_score_submission(result, payload, generation)
        self._poll_pending_score_status()

    def _poll_pending_score_status(self) -> None:
        if self.score_save_state != SAVE_PENDING or not self._last_score_payload:
            return
        getter = getattr(self.backend, "get_save_status", None)
        if not callable(getter):
            return
        event = getter(self._last_score_payload["request_id"])
        if event is None:
            return
        state = getattr(event, "state", None)
        if state == SaveState.COMMITTED:
            self._finish_score_submission(
                event.result, self._last_score_payload,
                self._score_submit_generation)
        elif state in (SaveState.QUARANTINED, SaveState.PERMANENT_FAILURE):
            self.score_save_state = SAVE_FAILED
            self.score_save_error = event.result.get("error", "待保存记录需要恢复")
            self.score_save_retryable = False
            self.score_save_durable_pending = bool(
                event.result.get("pending_preserved"))
            self.score_save_message = "待保存记录已隔离，请返回菜单查看"
        elif state == SaveState.RECOVERY_REQUIRED:
            self.score_save_message = event.result.get(
                "error", "待保存记录需要恢复")

    def _finish_score_submission(self, result, payload, generation: int,
                                 exception_text: Optional[str] = None) -> None:
        if generation != self._score_submit_generation:
            return
        row_id, error = parse_score_response(result)
        if row_id is None:
            durable_pending = bool(
                isinstance(result, dict) and result.get("durable_pending"))
            if durable_pending:
                self.score_save_state = SAVE_PENDING
                self.score_save_error = None
                self.score_save_retryable = True
                self.score_save_durable_pending = True
                self.score_save_message = "已写入待保存文件"
                self._destructive_action_armed = None
                self._destructive_action_deadline = 0
                self._discard_unsaved_armed = False
                self.on_score_save_failed(payload, error)
                return
            self.score_save_state = SAVE_FAILED
            self.score_save_error = exception_text or error or "保存失败"
            self.score_save_retryable = (
                result is None or (isinstance(result, dict)
                                   and bool(result.get(
                                       "retryable",
                                       result.get("_retryable", False)))))
            self.score_save_durable_pending = bool(
                isinstance(result, dict) and result.get("durable_pending"))
            self.on_score_save_failed(payload, self.score_save_error)
            return
        self.score_save_state = SAVE_SAVED
        self.score_save_error = None
        self.score_save_durable_pending = False
        self._discard_unsaved_armed = False
        self._destructive_action_armed = None
        self._destructive_action_deadline = 0
        if self.submit_replaces_existing:
            self._score_submission_id = row_id
        if isinstance(result, dict) and result.get("attempt_recorded"):
            self.score_save_message = (
                "本局已记录 · 新纪录" if result.get("new_personal_best")
                else "本局已记录")
        else:
            self.score_save_message = "成绩已保存"
        self.on_score_save_succeeded(result, payload)
        # Read the leaderboard only after the server has acknowledged the
        # score, otherwise the GET can race ahead of the POST.
        self.invalidate_overlay_leaderboard()

    def _next_score_revision(self) -> int:
        revision = self.attempt_context.next_revision()
        self._score_attempt_revision = revision
        return revision

    def retry_score_save(self) -> None:
        payload = self._last_score_payload
        if self.score_save_state != SAVE_FAILED or not payload:
            return
        self._submit_result_score(payload["score"], payload.get("extra"),
                                  request_id=payload["request_id"],
                                  revision=payload["revision"])

    def on_score_save_succeeded(self, result: dict, payload: dict) -> None:
        """Hook for games that maintain their own confirmed-save state."""

    def on_score_save_failed(self, payload: dict,
                             error: Optional[str]) -> None:
        """Hook for games that maintain their own pending-save state."""

    def on_game_over(self, score: int, extra=None) -> None:
        self.score = score
        self.extra = extra
        self.state = "gameover"
        self.invalidate_overlay_leaderboard()
        self._submit_result_score(score, extra)

    def on_win(self, score: int, extra=None) -> None:
        """Variant of ``on_game_over`` that flips state to ``won`` instead
        of ``gameover``. Subclasses that distinguish a win from a loss
        (Sokoban, Zuma, 2048) should call this on a successful clear so
        the win overlay rather than the loss overlay shows up."""
        self.score = score
        self.extra = extra
        self.state = "won"
        self.invalidate_overlay_leaderboard()
        self._submit_result_score(score, extra)

    def run(self) -> None:
        try:
            while self.running:
                dt = self.clock.tick(self.fps) / 1000.0
                self._poll_score_submission()
                for event in pygame.event.get():
                    self.handle_event(event)
                # 2048 keeps animating during won/gameover overlays, so we
                # let each subclass decide by always calling update — but
                # most games just early-out when state != "playing".
                if self.state == "playing":
                    self.update(dt)
                else:
                    # Give subclasses a chance to advance animations even
                    # when an overlay is up. They can opt in by overriding
                    # ``update_overlay``; default is no-op.
                    self.update_overlay(dt)
                self.draw()
                pygame.display.flip()
        finally:
            if self._owns_backend:
                close_backend = getattr(self.backend, "close", None)
                if callable(close_backend):
                    close_backend()
            # CRITICAL: only tear down the *display* — not all of pygame.
            # This keeps pygame.font, pygame.time, etc. initialized so the
            # launcher can resume instantly (just recreates the window).
            # Calling pygame.quit() here used to freeze the launcher for
            # ~1 minute while SDL re-initialized and the font cache was
            # rebuilt from scratch.
            pygame.display.quit()

    def update_overlay(self, dt: float) -> None:
        """Override to advance animations while a game-over/won overlay is
        visible. Default: do nothing."""

    # ---------- Game-over overlay (used by subclasses) -------------------
    def _layout_overlay_buttons(self, buttons: List[Button],
                                y_offset_from_center: int = 80) -> None:
        """Position buttons in a centered row inside the overlay panel.

        If the row would be wider than 80% of the window, we stack buttons
        vertically instead so they always fit.
        """
        if not buttons:
            return
        gap = 12
        total_w = sum(b.rect.w for b in buttons) + gap * (len(buttons) - 1)
        max_w = int(self.width * 0.8)
        if total_w <= max_w:
            # Horizontal row.
            x = (self.width - total_w) // 2
            y = self.height // 2 + y_offset_from_center
            for b in buttons:
                b.rect.topleft = (x, y)
                x += b.rect.w + gap
        else:
            # Vertical stack, centered.
            total_h = sum(b.rect.h for b in buttons) + gap * (len(buttons) - 1)
            y = self.height // 2 + y_offset_from_center - total_h // 2 + 18
            for b in buttons:
                b.rect.topleft = ((self.width - b.rect.w) // 2, y)
                y += b.rect.h + gap

    def _overlay_panel(self, buttons: List[Button]) -> pygame.Rect:
        """Auto-sized panel rect that always contains the title, score,
        leaderboard line, and the full button row/stack."""
        min_w, min_h = 380, 260
        if buttons:
            gap = 12
            row_w = sum(b.rect.w for b in buttons) + gap * (len(buttons) - 1)
            row_h = max(b.rect.h for b in buttons)
            # Account for stacked layout too.
            col_w = max(b.rect.w for b in buttons)
            stack_h = sum(b.rect.h for b in buttons) + gap * (len(buttons) - 1)
            needed_w = max(row_w, col_w) + 60
            needed_h = max(row_h, stack_h) + 200
            panel_w = max(min_w, needed_w, 320)
            panel_h = max(min_h, needed_h, 240)
        else:
            panel_w, panel_h = min_w, min_h
        # Clamp to window.
        panel_w = min(panel_w, self.width - 20)
        panel_h = min(panel_h, self.height - 20)
        panel = pygame.Rect(0, 0, panel_w, panel_h)
        panel.center = (self.width // 2, self.height // 2)
        return panel

    def draw_gameover_overlay(self, message: str = "游戏结束",
                              buttons: Optional[List[Button]] = None,
                              detail: Optional[str] = None) -> None:
        self._poll_score_submission()
        # Store buttons so handle_event can route clicks to them on the
        # next event loop iteration. Drawing must not pump events itself.
        self.overlay_buttons = list(buttons) if buttons else []
        # Layout the buttons FIRST so the panel can size to fit them.
        self._layout_overlay_buttons(self.overlay_buttons)

        # Backdrop with subtle blue tint instead of pure black.
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((10, 14, 28, 200))
        self.screen.blit(overlay, (0, 0))

        panel = self._overlay_panel(self.overlay_buttons)
        # Panel body with vertical gradient + shadow.
        shadow = pygame.Surface((panel.w + 8, panel.h + 8), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 130),
                         pygame.Rect(4, 4, panel.w, panel.h), border_radius=14)
        self.screen.blit(shadow, (panel.x - 4, panel.y - 4))
        body = pygame.Surface((panel.w, panel.h), pygame.SRCALPHA)
        draw_gradient_bg(body, body.get_rect(),
                         top=(54, 60, 86), bottom=(38, 44, 66))
        mask = pygame.Surface((panel.w, panel.h), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255),
                         pygame.Rect(0, 0, panel.w, panel.h), border_radius=12)
        body.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        self.screen.blit(body, panel.topleft)
        pygame.draw.rect(self.screen, COLORS["accent"], panel, 2, border_radius=12)

        # Title with accent underline.
        draw_text(self.screen, message, (panel.centerx, panel.y + 38),
                  size=26, color=COLORS["accent2"], bold=True, center=True)
        pygame.draw.rect(self.screen, COLORS["accent2"],
                         pygame.Rect(panel.centerx - 60, panel.y + 60, 120, 2),
                         border_radius=1)
        draw_text(self.screen, f"得分: {self.score}",
                  (panel.centerx, panel.y + 82), size=20,
                  color=COLORS["text"], center=True)
        if detail:
            draw_text(self.screen, detail, (panel.centerx, panel.y + 114),
                      size=14, color=COLORS["text_dim"], center=True)

        save_messages = {
            SAVE_SAVING: (("保存尚未完成 · 3 秒内再次操作将放弃"
                           if self._discard_unsaved_armed
                           else "成绩保存中…"),
                          (COLORS["danger"] if self._discard_unsaved_armed
                           else COLORS["text_dim"])),
            SAVE_SAVED: (self.score_save_message or "成绩已保存", COLORS["ok"]),
            SAVE_PENDING: (self.score_save_message or "已写入待保存文件",
                           COLORS["accent2"]),
            SAVE_FAILED: (("保存失败 · 按 S 重试"
                           if (self.score_save_retryable
                               and not self._discard_unsaved_armed)
                           else "未落盘 · 再按 Esc 放弃"
                           if self._discard_unsaved_armed
                           else "成绩数据无效 · 无法重试"), COLORS["danger"]),
        }
        save_line = save_messages.get(self.score_save_state)
        if save_line:
            draw_text(self.screen, save_line[0],
                      (panel.centerx, panel.y + 132), size=13,
                      color=save_line[1], center=True)

        # Try to fetch leaderboard (cached by BackendClient per call site).
        # Render up to 3 entries as STACKED lines (one per entry) so the
        # text can never overflow the panel horizontally — even on narrow
        # windows like Tetris (560) or 2048 (460).
        lb_key = (self.game_id, self.state, self.score, repr(self.extra))
        if (self.score_save_state != SAVE_SAVING
                and lb_key != self._overlay_lb_key):
            self._overlay_lb_key = lb_key
            self._overlay_leaderboard = []
            self._overlay_lb_error = None
            self._overlay_lb_future = None
            if self.backend and self.game_id:
                try:
                    leaderboard_async = getattr(
                        self.backend, "leaderboard_async", None)
                    if callable(leaderboard_async):
                        self._overlay_lb_future = leaderboard_async(
                            self.game_id, limit=3)
                        self._overlay_lb_future_generation = (
                            self._overlay_lb_generation)
                    else:
                        self._overlay_leaderboard = self.backend.leaderboard(
                            self.game_id, limit=3)
                except Exception:  # noqa: BLE001 - records UI must degrade
                    self._overlay_leaderboard = []
                    self._overlay_lb_error = "本机记录暂时不可读"
        if (self._overlay_lb_future is not None
                and self._overlay_lb_future.done()):
            generation = self._overlay_lb_future_generation
            try:
                result = self._overlay_lb_future.result()
                if generation == self._overlay_lb_generation:
                    self._overlay_leaderboard = (result
                                                 if isinstance(result, list)
                                                 else [])
            except Exception:  # noqa: BLE001 - records UI must degrade
                self._overlay_leaderboard = []
                self._overlay_lb_error = "本机记录暂时不可读"
            self._overlay_lb_future = None
        lb = self._overlay_leaderboard
        if self.score_save_state == SAVE_SAVING:
            draw_text(self.screen, "（保存后更新排行）",
                      (panel.centerx, panel.y + 154), size=13,
                      color=COLORS["text_dim"], center=True)
        elif lb:
            f13 = font(13)
            for i, e in enumerate(lb):
                line = f"#{e.get('rank', i+1)} {(e.get('player') or 'anon')[:10]}: {e.get('score', 0)}"
                # Truncate to the panel inner width if needed.
                max_w = panel.w - 40
                line = fit_text(line, f13, max_w)
                draw_text(self.screen, line,
                          (panel.centerx, panel.y + 150 + i * 16),
                          size=13, color=COLORS["text_dim"], center=True)
        elif self._overlay_lb_future is not None:
            draw_text(self.screen, "（排行加载中…）",
                      (panel.centerx, panel.y + 154), size=13,
                      color=COLORS["text_dim"], center=True)
        elif self._overlay_lb_error:
            draw_text(self.screen, f"（{self._overlay_lb_error}）",
                      (panel.centerx, panel.y + 154), size=13,
                      color=COLORS["danger"], center=True)
        else:
            draw_text(self.screen, "（暂无排行）",
                      (panel.centerx, panel.y + 154), size=13,
                      color=COLORS["text_dim"], center=True)

        # Buttons row/stack
        mouse_pos = pygame.mouse.get_pos()
        for b in self.overlay_buttons:
            b.draw(self.screen, hover=b.hovered_by(mouse_pos))

    def draw_paused_overlay(self,
                            buttons: Optional[List[Button]] = None) -> None:
        self.overlay_buttons = list(buttons) if buttons else []
        self._layout_overlay_buttons(self.overlay_buttons, y_offset_from_center=20)
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        self.screen.blit(overlay, (0, 0))
        draw_text(self.screen, "已暂停 — 按 P 继续 / Esc 返回菜单",
                  (self.width // 2, self.height // 2 - 30),
                  size=22, color=COLORS["accent2"], bold=True, center=True)
        mouse_pos = pygame.mouse.get_pos()
        for b in self.overlay_buttons:
            b.draw(self.screen, hover=b.hovered_by(mouse_pos))
