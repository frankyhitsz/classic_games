"""Zuma — simplified frog-shooter chain-matching game.

A train of colored balls slides along a curvy track toward the "skull"
exit. The player aims with the mouse and clicks to shoot a colored ball
from the frog at the bottom of the screen. When the projectile hits a
chain ball, it inserts into the chain at that point. Any run of 3+ same
color balls is removed and the chain snaps together. Combo chain
reactions are awarded bonus points.

Win: clear the entire chain (after the level's ball budget is exhausted).
Lose: the front ball reaches the skull.

Controls:
  Mouse aim         aim
  Left click        shoot
  Right click / S   swap current & next ball
  P                 pause
  R                 restart
  Esc               quit
"""
from __future__ import annotations

import math
import random
from bisect import bisect_left
from typing import List, Optional, Tuple

import pygame

from game_service.catalog import GAME_BY_ID
from game_service.service import GameDataService, SaveState
from client.common.ui import (COLORS, BaseGame, Button, draw_gradient_bg,
                              draw_panel, draw_text)

WIDTH, HEIGHT = 820, 640
BALL_R = 12
GAP = 22  # distance between consecutive chain balls along path
PROJ_SPEED = 560.0
CHAIN_ANIM_SPEED = 240.0  # px/sec for insertion/collapse visual easing
CHAIN_REACTION_DELAY = 0.12  # wait until separated groups visibly reconnect
SHOT_INTERVAL = 0.075     # ~13 shots/sec; responsive without frame spam
MAX_SHOT_QUEUE = 12       # preserve a short burst of rapid clicks
TRACK_RENDER_SCALE = 3    # supersampling removes stair-step pixel edges
SHOOTER_POS = (WIDTH // 2, HEIGHT - 60)
_TRACK_SURFACE_CACHE: dict = {}

NUM_COLORS = 5  # use GAME_COLORS["zuma"][0:5]
LEVEL_CLEAR_BONUS = 500
# The opening round is intentionally gentler than the old single-level game.
# Every subsequent round has both a larger ball budget and a faster chain.
ZUMA_LEVELS = [
    {"balls": 24, "speed": 14.0, "spawn_interval": 0.95,
     "track": "river", "track_name": "萤火溪谷", "accent": (73, 208, 196)},
    {"balls": 30, "speed": 17.0, "spawn_interval": 0.90,
     "track": "canyon", "track_name": "折返峡湾", "accent": (245, 190, 75)},
    {"balls": 36, "speed": 20.0, "spawn_interval": 0.85,
     "track": "figure8", "track_name": "双环花园", "accent": (239, 118, 106)},
    {"balls": 42, "speed": 23.0, "spawn_interval": 0.80,
     "track": "orbit", "track_name": "月蚀回廊", "accent": (137, 151, 255)},
    {"balls": 48, "speed": 26.0, "spawn_interval": 0.75,
     "track": "spiral", "track_name": "环城终局", "accent": (220, 110, 180)},
]


# ---------------------------------------------------------------------------
# Path geometry
# ---------------------------------------------------------------------------
def _finish_path(pts: List[Tuple[float, float]]) -> Tuple[
        List[Tuple[float, float]], List[float]]:
    """Calculate cumulative distance for one of the authored tracks."""
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + math.hypot(pts[i][0] - pts[i - 1][0],
                                        pts[i][1] - pts[i - 1][1]))
    return pts, cum


def build_path(track: str = "river") -> Tuple[
        List[Tuple[float, float]], List[float]]:
    """Build one of five level-specific tracks.

    Later paths fold back, cross, and spiral around the play field. A chain
    that travels deep into those tracks therefore occupies real aiming lanes
    instead of merely moving farther to the right on the same sine wave.
    """
    pts: List[Tuple[float, float]] = []
    if track == "river":
        n = 300
        for i in range(n + 1):
            t = i / n
            x = 48 + (WIDTH - 96) * t
            y = 220 + 96 * math.sin(t * math.pi * 3.0) - 24 * (1 - t)
            pts.append((x, y))
    elif track == "canyon":
        n = 360
        for i in range(n + 1):
            t = i / n
            x = WIDTH / 2 - 338 * math.cos(t * math.pi * 3.0)
            y = 128 + 330 * t + 22 * math.sin(t * math.pi * 6.0)
            pts.append((x, y))
    elif track == "figure8":
        n = 400
        for i in range(n + 1):
            t = i / n
            theta = -math.pi / 2 + math.pi * 1.75 * t
            x = WIDTH / 2 + 328 * math.sin(theta)
            y = 272 + 142 * math.sin(theta * 2.0) + 24 * t
            pts.append((x, y))
    elif track == "orbit":
        n = 420
        for i in range(n + 1):
            t = i / n
            theta = math.pi + math.pi * 2.6 * t
            rx = 344 - 174 * t
            ry = 192 - 92 * t
            pts.append((WIDTH / 2 + rx * math.cos(theta),
                        300 + ry * math.sin(theta)))
    elif track == "spiral":
        n = 480
        for i in range(n + 1):
            t = i / n
            theta = math.pi + math.pi * 3.2 * t
            rx = 350 - 224 * t
            ry = 214 - 126 * t
            pts.append((WIDTH / 2 + rx * math.cos(theta),
                        314 + ry * math.sin(theta)))
    else:
        raise ValueError(f"unknown Zuma track: {track}")
    return _finish_path(pts)


def pos_at(pts, cum, d: float) -> Tuple[float, float]:
    total = cum[-1]
    if d <= 0:
        return pts[0]
    if d >= total:
        return pts[-1]
    i = bisect_left(cum, d, lo=1)
    seg = cum[i] - cum[i - 1]
    if seg <= 1e-6:
        return pts[i]
    t = (d - cum[i - 1]) / seg
    return (pts[i - 1][0] + t * (pts[i][0] - pts[i - 1][0]),
            pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1]))


def _build_smooth_track_surface(
        points: List[Tuple[float, float]],
        accent: Tuple[int, int, int]) -> pygame.Surface:
    """Render one continuous, anti-aliased marble channel.

    The previous rail used small perpendicular sleepers and several thin
    native-resolution lines.  On curves those details read as broken pixels.
    This version draws a single filled ribbon at 3× resolution, then downsizes
    it once and caches the result for the whole level.
    """
    scale = TRACK_RENDER_SCALE
    high_size = (WIDTH * scale, HEIGHT * scale)
    layer = pygame.Surface(high_size, pygame.SRCALPHA)
    scaled_points = [(round(x * scale), round(y * scale))
                     for x, y in points]

    def stroke(color: tuple, width: int) -> None:
        scaled_width = max(1, width * scale)
        pygame.draw.lines(layer, color, False, scaled_points, scaled_width)
        # Pygame's thick polyline renderer leaves miter-shaped pinholes at
        # some sharp joins.  Filling every sampled joint with the same round
        # brush welds the segments into one genuinely continuous ribbon.
        radius = scaled_width // 2
        for point in scaled_points:
            pygame.draw.circle(layer, color, point, radius)

    # Soft depth, a narrow colored rim, then one uninterrupted pale channel.
    stroke((*accent, 30), GAP + 16)
    stroke((151, 176, 205, 210), GAP + 10)
    stroke((*accent, 245), GAP + 6)
    stroke((248, 251, 255, 255), GAP)
    center_highlight = tuple(round(base * 0.72 + tint * 0.28)
                             for base, tint in zip((248, 251, 255), accent))
    stroke((*center_highlight, 255), 2)
    return pygame.transform.smoothscale(layer, (WIDTH, HEIGHT))


class Zuma(BaseGame):
    game_id = "zuma"
    title = "祖玛"

    def __init__(self, backend: Optional[GameDataService] = None,
                 player: str = "anonymous",
                 profile_id: Optional[str] = None):
        super().__init__(WIDTH, HEIGHT, fps=60, backend=backend, player=player,
                         profile_id=profile_id)
        self.unlocked_level = 1
        self.saved_high_score = 0
        self._progress_generation = 0
        self._progress_future = None
        self._progress_write_future = None
        self.progress_save_message = ""
        self.reset()
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
                    f"{ruleset}:campaign")
                if getattr(event, "state", None) == SaveState.COMMITTED:
                    self.progress_save_message = ""
        pending = self._progress_future
        if pending is None or not pending[0].done():
            return
        self._progress_future = None
        try:
            value = pending[0].result()
        except Exception:  # noqa: BLE001 - a new run remains playable
            return
        self._apply_progress(value, pending[1])

    def _apply_progress(self, value, generation: int) -> None:
        if generation != self._progress_generation:
            return
        if not isinstance(value, dict):
            return
        unlocked = value.get("unlocked_level", 1)
        high_score = value.get("highest_score", 0)
        if type(unlocked) is int:
            self.unlocked_level = min(len(ZUMA_LEVELS), max(1, unlocked))
        if type(high_score) is int:
            self.saved_high_score = max(0, high_score)

    # ------------------------------------------------------------------
    def reset(self):
        """Start a fresh five-level run."""
        self.begin_score_session()
        self.level_idx = 0
        self.score = 0
        self.cleared_balls = 0
        self._start_level()

    def _start_level(self) -> None:
        config = ZUMA_LEVELS[self.level_idx]
        self.path_pts, self.path_cum = build_path(config["track"])
        self.path_length = self.path_cum[-1]
        self.track_name = config["track_name"]
        self.track_accent = config["accent"]
        track_cache_key = (config["track"], self.track_accent)
        if track_cache_key not in _TRACK_SURFACE_CACHE:
            _TRACK_SURFACE_CACHE[track_cache_key] = _build_smooth_track_surface(
                self.path_pts, self.track_accent)
        self.track_surface = _TRACK_SURFACE_CACHE[track_cache_key]
        # ``pos`` is the logical distance along the pipe. ``visual_offset``
        # is a short-lived draw-only offset used to animate insertions and
        # backward collapses without ever breaking the logical chain.
        self.chain: List[dict] = []
        # Spawned balls wait at the fixed entrance until a complete GAP-sized
        # slot opens behind the chain.  They no longer travel as a separate
        # fast group, which previously looked like balls floating beside the
        # pipe or like a second chain above/below the main one.
        self.incoming: List[dict] = []
        self.projectiles: List[dict] = []
        self.spawned = 0
        self.spawn_timer = 0.0
        self.level_ball_count = config["balls"]
        self.spawn_interval = config["spawn_interval"]
        self.chain_speed = config["speed"]  # px/sec along path
        self.combo = 0
        self.level_bonus = 0
        self.spawn_color_history: List[int] = []
        # More than one projectile can create a reconnecting pair before an
        # earlier collapse finishes. Keep every pending reaction; a single
        # slot lets the newest match silently overwrite the older one.
        self.pending_chain_matches: List[dict] = []
        self.match_particles: List[dict] = []
        self.chain_banner_timer = 0.0
        self.chain_banner_depth = 0
        self.visual_time = 0.0
        self.state = "playing"
        self.extra = None
        self.current_color = random.randint(0, NUM_COLORS - 1)
        self.next_color = random.randint(0, NUM_COLORS - 1)
        self.aim_angle = -math.pi / 2
        self.shoot_cooldown = 0.0
        self.shot_queue = 0
        self.overlay_buttons = []

    def advance_level(self) -> None:
        """Continue after clearing a non-final level."""
        if self.level_idx >= len(ZUMA_LEVELS) - 1:
            return
        self.level_idx += 1
        self._start_level()

    # ------------------------------------------------------------------
    def update(self, dt: float):
        self._poll_progress()
        if self.state != "playing":
            return
        self.visual_time += dt
        self.shoot_cooldown -= dt
        while self.shoot_cooldown <= 0.0 and self.shot_queue > 0:
            overdue = -self.shoot_cooldown
            self.shot_queue -= 1
            self._fire_shot()
            self.shoot_cooldown -= overdue
        self.shoot_cooldown = max(0.0, self.shoot_cooldown)

        # Spawn new chain ball
        if self.spawned < self.level_ball_count:
            self.spawn_timer += dt
            while (self.spawn_timer >= self.spawn_interval
                   and self.spawned < self.level_ball_count):
                self.spawn_timer -= self.spawn_interval
                self._spawn_chain_ball()

        # Admit waiting balls only when a real on-pipe slot exists.  A while
        # loop handles large dt values without creating a free-moving group.
        while self.incoming:
            next_back = self.chain[-1]["pos"] - GAP if self.chain else 0.0
            if next_back < 0.0:
                break
            ball = self.incoming.pop(0)
            self.chain.append({"pos": next_back, "color": ball["color"],
                               "visual_offset": 0.0})

        # The logical chain always stays contiguous and moves forward at one
        # speed. Draw-only offsets ease toward zero: positive offsets make the
        # exit-side group visibly move BACKWARD after a middle elimination;
        # negative offsets make insertion pushes move forward smoothly.
        for ball in self.chain:
            ball["pos"] += self.chain_speed * dt
            offset = ball.get("visual_offset", 0.0)
            step = CHAIN_ANIM_SPEED * dt
            if offset > 0.0:
                ball["visual_offset"] = max(0.0, offset - step)
            elif offset < 0.0:
                ball["visual_offset"] = min(0.0, offset + step)
        self._normalize_visual_order()
        self._update_pending_chain_match(dt)
        self._update_match_particles(dt)
        self.chain_banner_timer = max(0.0, self.chain_banner_timer - dt)

        # Resolve shots before deciding that the leading ball reached the
        # exit. A projectile already touching that ball in this frame gets
        # its legitimate last-chance clear instead of an order-dependent loss.
        for p in list(self.projectiles):
            old_pos = (p["x"], p["y"])
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            if self._check_projectile_hit(p, old_pos):
                continue
            if (p["x"] < -30 or p["x"] > WIDTH + 30
                    or p["y"] < -30 or p["y"] > HEIGHT + 30):
                self.projectiles.remove(p)

        # Check loss
        if self.chain and self._visual_distance(self.chain[0]) >= self.path_length:
            self.state = "gameover"
            self.on_game_over(self.score,
                              extra={"cleared": self.cleared_balls,
                                     "remaining": (len(self.chain)
                                                   + len(self.incoming)),
                                     "spawned": self.spawned,
                                     "level": self.level_idx + 1})
            return

        # A level is clear only after its complete budget has entered play and
        # both the on-track chain and entrance queue have been eliminated.
        if (self.spawned >= self.level_ball_count
                and not self.chain
                and not self.incoming):
            self.projectiles.clear()
            self.level_bonus = LEVEL_CLEAR_BONUS * (self.level_idx + 1)
            self.score += self.level_bonus
            completed_all = self.level_idx == len(ZUMA_LEVELS) - 1
            result = {"cleared": self.cleared_balls,
                      "level": self.level_idx + 1,
                      "levels": len(ZUMA_LEVELS),
                      "level_balls": self.level_ball_count,
                      "level_bonus": self.level_bonus,
                      "won": True,
                      "completed_all": completed_all}
            save_progress = getattr(self.backend, "merge_progress_async", None)
            if callable(save_progress):
                try:
                    self._progress_generation += 1
                    generation = self._progress_generation
                    progress_value = {
                        "unlocked_level": min(
                            len(ZUMA_LEVELS), self.level_idx + 2),
                        "highest_score": self.score,
                        "completed_all": completed_all}
                    self.unlocked_level = max(
                        self.unlocked_level,
                        progress_value["unlocked_level"])
                    self.saved_high_score = max(
                        self.saved_high_score,
                        progress_value["highest_score"])
                    self._progress_write_future = (save_progress(
                        self.profile_id, self.game_id, "campaign",
                        progress_value), generation)
                except Exception:  # noqa: BLE001 - progress is non-critical
                    self.progress_save_message = "进度暂时未保存"
            if completed_all:
                self.on_win(self.score, extra=result)
            else:
                # Reuse the shared "won" overlay state without submitting an
                # intermediate score. The next-level button starts the next
                # configuration while preserving the cumulative run score.
                self.extra = result
                self.state = "won"
                self.invalidate_overlay_leaderboard()
            return

    def _spawn_chain_ball(self):
        # New balls always queue at the fixed entrance. They are admitted by
        # ``update`` only when a complete slot opens behind the main chain.
        color = self._choose_spawn_color()
        self.spawn_color_history.append(color)
        self.incoming.append({"pos": 0.0, "color": color})
        self.spawned += 1

    def _choose_spawn_color(self) -> int:
        """Choose a stream color without ever authoring a natural triple."""
        choices = list(range(NUM_COLORS))
        if (len(self.spawn_color_history) >= 2
                and self.spawn_color_history[-1]
                == self.spawn_color_history[-2]):
            choices.remove(self.spawn_color_history[-1])
        return random.choice(choices)

    @staticmethod
    def _visual_distance(ball: dict) -> float:
        return ball["pos"] + ball.get("visual_offset", 0.0)

    def _normalize_visual_order(self) -> None:
        """Prevent compounded animations from visually crossing balls.

        Several projectiles can land while an earlier insertion/collapse is
        still easing. Their raw offsets may then put a later logical ball in
        front of its predecessor. Clamp only the visual offset (never logical
        positions), allowing a brief overlap but no reversed/floating order.
        """
        previous = None
        for ball in self.chain:
            visual = max(0.0, self._visual_distance(ball))
            if previous is not None and visual > previous:
                visual = previous
            ball["visual_offset"] = visual - ball["pos"]
            previous = visual

    def _update_pending_chain_match(self, dt: float) -> None:
        """Resolve every due reaction after its boundary groups meet."""
        if not self.pending_chain_matches:
            return
        due = []
        waiting = []
        for pending in self.pending_chain_matches:
            pending["timer"] -= dt
            (due if pending["timer"] <= 0.0 else waiting).append(pending)
        # Install the waiting list before resolving due entries because a
        # successful recursive clear can enqueue another reaction.
        self.pending_chain_matches = waiting
        for pending in due:
            left = next((i for i, ball in enumerate(self.chain)
                         if ball is pending["left"]), None)
            right = next((i for i, ball in enumerate(self.chain)
                          if ball is pending["right"]), None)
            if (left is None or right != left + 1
                    or self.chain[left]["color"]
                    != self.chain[right]["color"]):
                continue
            depth = pending["depth"]
            if self._try_match_at(left, reaction_depth=depth):
                self.chain_banner_depth = max(self.chain_banner_depth,
                                              depth + 1)
                self.chain_banner_timer = 0.8

    @property
    def pending_chain_match(self):
        """Compatibility view for callers interested in any pending match."""
        return (self.pending_chain_matches[0]
                if self.pending_chain_matches else None)

    def _update_match_particles(self, dt: float) -> None:
        for particle in list(self.match_particles):
            particle["life"] -= dt
            if particle["life"] <= 0.0:
                self.match_particles.remove(particle)
                continue
            particle["x"] += particle["vx"] * dt
            particle["y"] += particle["vy"] * dt
            particle["vy"] += 85.0 * dt

    def _check_projectile_hit(self, p, old_pos=None) -> bool:
        start_x, start_y = old_pos or (p["x"], p["y"])
        seg_x = p["x"] - start_x
        seg_y = p["y"] - start_y
        seg_len_sq = seg_x * seg_x + seg_y * seg_y
        best = None
        for i, ball in enumerate(self.chain):
            visual_pos = self._visual_distance(ball)
            bx, by = pos_at(self.path_pts, self.path_cum, visual_pos)
            if seg_len_sq > 1e-9:
                hit_t = ((bx - start_x) * seg_x
                         + (by - start_y) * seg_y) / seg_len_sq
                hit_t = max(0.0, min(1.0, hit_t))
            else:
                hit_t = 0.0
            hit_x = start_x + seg_x * hit_t
            hit_y = start_y + seg_y * hit_t
            if math.hypot(hit_x - bx, hit_y - by) < BALL_R * 1.9:
                if best is None or hit_t < best[0]:
                    best = (hit_t, i, ball, visual_pos, bx, by, hit_x, hit_y)
        if best is None:
            return False

        _, i, ball, visual_pos, bx, by, hit_x, hit_y = best
        # Decide whether to insert in front of or behind the hit ball based
        # on the actual closest point along the swept projectile segment.
        eps = 2.0
        fx, fy = pos_at(self.path_pts, self.path_cum, visual_pos + eps)
        tx, ty = fx - bx, fy - by
        tlen = math.hypot(tx, ty) or 1.0
        tx, ty = tx / tlen, ty / tlen
        dot = (hit_x - bx) * tx + (hit_y - by) * ty
        self._insert(i, p["color"], ball["pos"], in_front=(dot > 0))
        if p in self.projectiles:
            self.projectiles.remove(p)
        return True

    def _insert(self, idx: int, color: int, hit_pos: float,
                in_front: bool = False) -> None:
        """Insert a new ball into the chain.

        Layout: the FRONT segment (chain[0..idx], including the ball
        the projectile struck) advances by GAP toward the exit, and
        the new ball takes the original ``hit_pos`` — exactly between
        the shifted chain[idx] and the stationary back segment.

        The back segment (chain[idx+1..]) does NOT move on insertion.
        Spawn location is decoupled entirely: new balls wait in
        ``self.incoming`` at path distance 0 until a full slot opens.
        Therefore the spawn point remains fixed regardless of insertion.
        """
        hit_visual_offset = self.chain[idx].get("visual_offset", 0.0)
        if in_front:
            # Insert between idx-1 and idx (toward the exit).  Only balls
            # already in front of the hit ball need to advance.
            for j in range(idx):
                self.chain[j]["pos"] += GAP
                self.chain[j]["visual_offset"] = (
                    self.chain[j].get("visual_offset", 0.0) - GAP)
            self.chain.insert(idx, {"pos": hit_pos + GAP, "color": color,
                                    "visual_offset": (hit_visual_offset
                                                      - GAP)})
            match_idx = idx
        else:
            # Insert behind the hit ball.  The hit ball and everything in
            # front advance, while the back segment stays anchored.
            for j in range(idx + 1):
                self.chain[j]["pos"] += GAP
                self.chain[j]["visual_offset"] = (
                    self.chain[j].get("visual_offset", 0.0) - GAP)
            self.chain.insert(idx + 1, {"pos": hit_pos, "color": color,
                                        "visual_offset": hit_visual_offset})
            match_idx = idx + 1
        matched = self._try_match_at(match_idx)
        if matched:
            self.combo += 1
        else:
            self.combo = 0
        self._normalize_visual_order()

    def _try_match_at(self, idx: int, reaction_depth: int = 0) -> bool:
        """Remove exactly one run and stage any recursive follow-up.

        The old loop removed every newly-adjacent group in the same frame.
        Now the first run disappears, the exit-side group visibly retracts,
        and only after the two surviving boundary balls meet do we test the
        next run. This makes recursive clears readable rather than abrupt.
        """
        if not 0 <= idx < len(self.chain):
            return False
        color = self.chain[idx]["color"]
        lo = idx
        while lo > 0 and self.chain[lo - 1]["color"] == color:
            lo -= 1
        hi = idx
        while (hi < len(self.chain) - 1
               and self.chain[hi + 1]["color"] == color):
            hi += 1
        count = hi - lo + 1
        if count < 3:
            return False

        old_len = len(self.chain)
        left_boundary = self.chain[lo - 1] if lo > 0 else None
        right_boundary = self.chain[hi + 1] if hi < old_len - 1 else None
        self._emit_match_particles(self.chain[lo:hi + 1])
        bonus = 1 + self.combo + reaction_depth
        self.score += count * 10 * bonus
        self.cleared_balls += count

        if left_boundary is not None and right_boundary is not None:
            collapse = count * GAP
            for ball in self.chain[:lo]:
                ball["pos"] -= collapse
                ball["visual_offset"] = (
                    ball.get("visual_offset", 0.0) + collapse)
            self.pending_chain_matches.append({
                "left": left_boundary,
                "right": right_boundary,
                "depth": reaction_depth + 1,
                "timer": max(CHAIN_REACTION_DELAY,
                             collapse / CHAIN_ANIM_SPEED),
            })
        del self.chain[lo:hi + 1]
        self._sync_shooter_colors()
        return True

    def _emit_match_particles(self, balls: List[dict]) -> None:
        from client.common.ui import GAME_COLORS
        for ball_index, ball in enumerate(balls):
            x, y = pos_at(self.path_pts, self.path_cum,
                          self._visual_distance(ball))
            for spark in range(4):
                angle = (ball_index * 1.7 + spark * math.pi / 2.0)
                speed = 38.0 + spark * 9.0
                self.match_particles.append({
                    "x": x, "y": y,
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed - 18.0,
                    "life": 0.42,
                    "max_life": 0.42,
                    "color": GAME_COLORS["zuma"][ball["color"] % NUM_COLORS],
                })

    def _playable_colors(self) -> List[int]:
        colors = {b["color"] for b in self.chain}
        colors.update(b["color"] for b in self.incoming)
        return sorted(colors) or list(range(NUM_COLORS))

    def _choose_playable_color(self) -> int:
        return random.choice(self._playable_colors())

    def _sync_shooter_colors(self) -> None:
        """Remove shooter colors that no longer exist anywhere in play."""
        playable = self._playable_colors()
        if self.current_color not in playable:
            self.current_color = random.choice(playable)
        if self.next_color not in playable:
            self.next_color = random.choice(playable)

    # ------------------------------------------------------------------
    def handle_event(self, event):
        # Pending rapid clicks should not leak through a pause or focus loss.
        if (event.type == getattr(pygame, "WINDOWFOCUSLOST", -1)
                or (event.type == pygame.KEYDOWN
                    and event.key == pygame.K_p)):
            self.shot_queue = 0
        if super().handle_event(event):
            return
        if event.type == pygame.MOUSEMOTION and self.state == "playing":
            self._set_aim(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and self.state == "playing":
            if event.button == 1:
                self._set_aim(event.pos)
                self._request_shot()
            elif event.button == 3:
                self._swap()
        elif event.type == pygame.KEYDOWN and self.state == "playing":
            if event.key in (pygame.K_s, pygame.K_SPACE):
                self._swap()
            elif event.key == pygame.K_RETURN:
                self._request_shot()
        elif (event.type == pygame.KEYDOWN and self.state == "won"
              and self.level_idx < len(ZUMA_LEVELS) - 1
              and event.key in (pygame.K_n, pygame.K_RETURN)):
            self.advance_level()
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_r:
                self.request_reset()

    def _set_aim(self, pos) -> None:
        dx = pos[0] - SHOOTER_POS[0]
        dy = pos[1] - SHOOTER_POS[1]
        if dx or dy:
            self.aim_angle = math.atan2(dy, dx)

    def _request_shot(self) -> None:
        """Fire immediately or retain the click until cooldown expires."""
        if self.shoot_cooldown <= 0.0:
            self._fire_shot()
        else:
            self.shot_queue = min(MAX_SHOT_QUEUE, self.shot_queue + 1)

    def _shoot(self) -> None:
        """Compatibility alias used by tests and keyboard integrations."""
        self._request_shot()

    def _fire_shot(self) -> None:
        dx = math.cos(self.aim_angle)
        dy = math.sin(self.aim_angle)
        self.projectiles.append({
            "x": SHOOTER_POS[0] + dx * (BALL_R + 4),
            "y": SHOOTER_POS[1] + dy * (BALL_R + 4),
            "vx": dx * PROJ_SPEED,
            "vy": dy * PROJ_SPEED,
            "color": self.current_color,
        })
        self.current_color = self.next_color
        self.next_color = self._choose_playable_color()
        self.shoot_cooldown = SHOT_INTERVAL

    def _swap(self):
        self.current_color, self.next_color = self.next_color, self.current_color

    # ------------------------------------------------------------------
    def draw(self):
        self._poll_progress()
        # A bright marble table replaces the old moonlit, dark-green scene.
        # Every level keeps its own accent, so the five tracks still feel
        # distinct without tinting the whole game one color.
        draw_gradient_bg(self.screen, top=(251, 253, 255),
                         bottom=(214, 235, 255))
        atmosphere = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        for index in range(34):
            x = (index * 97 + 41) % WIDTH
            y = 72 + (index * 53) % 420
            radius = 1 + (index % 3 == 0)
            confetti = (COLORS["game_tetris"], COLORS["game_2048"],
                        COLORS["game_zuma"])[index % 3]
            pygame.draw.circle(atmosphere, (*confetti, 20 + index % 4 * 5),
                               (x, y), radius)
        # Faint paper contours support the later orbit/spiral tracks.
        for radius in (90, 160, 235, 310):
            pygame.draw.ellipse(atmosphere, (*self.track_accent, 18),
                                pygame.Rect(WIDTH // 2 - radius,
                                            300 - radius * 2 // 3,
                                            radius * 2,
                                            radius * 4 // 3), 1)
        self.screen.blit(atmosphere, (0, 0))

        # Track: one cached, continuous supersampled ribbon.  There are no
        # repeated sleepers or segmented rails to interrupt the curve.
        self.screen.blit(self.track_surface, (0, 0))

        # Exit indicator at end of path (where balls must NOT reach).
        ex, ey = self.path_pts[-1]
        pygame.draw.circle(self.screen, COLORS["danger"], (int(ex), int(ey)),
                           BALL_R + 8)
        pygame.draw.circle(self.screen, (60, 10, 10), (int(ex), int(ey)),
                           BALL_R + 8, 2)
        draw_text(self.screen, "出口", (int(ex), int(ey) - BALL_R - 18),
                  size=14, color=COLORS["danger"], bold=True, center=True)

        # Entrance indicator — fixed at the path start. New balls
        # always appear here and slide forward along the track.
        sx, sy = self.path_pts[0]
        # Pulsing accent ring so the spawn point is obvious.
        pulse = 0.5 + 0.5 * math.sin(self.visual_time * 3.0)
        ring_r = int(BALL_R + 6 + pulse * 3)
        pygame.draw.circle(self.screen, COLORS["accent"],
                           (int(sx), int(sy)), ring_r, 2)
        pygame.draw.circle(self.screen, COLORS["accent2"],
                           (int(sx), int(sy)), BALL_R + 2, 1)
        draw_text(self.screen, "入口", (int(sx), int(sy) - BALL_R - 18),
                  size=12, color=COLORS["accent"], bold=True, center=True)

        # A waiting queue is represented by ONE translucent entrance ball
        # plus a count. Drawing every pending item at independent positions
        # was the source of the apparent second/floating chain.
        if self.incoming:
            self._draw_ball(sx, sy, self.incoming[0]["color"], alpha=200)
            if len(self.incoming) > 1:
                draw_text(self.screen, f"等待 ×{len(self.incoming)}",
                          (int(sx), int(sy) + BALL_R + 18), size=11,
                          color=COLORS["text_dim"], center=True)

        # Chain balls
        for i, ball in enumerate(self.chain):
            bx, by = pos_at(self.path_pts, self.path_cum,
                            self._visual_distance(ball))
            self._draw_ball(bx, by, ball["color"], highlight=(i == 0))

        # Projectiles
        for p in self.projectiles:
            self._draw_ball(p["x"], p["y"], p["color"])

        # Match sparks linger while groups reconnect, visually separating the
        # first clear from a later recursive clear.
        particle_layer = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        for particle in self.match_particles:
            alpha = int(255 * particle["life"] / particle["max_life"])
            pygame.draw.circle(particle_layer, (*particle["color"], alpha),
                               (int(particle["x"]), int(particle["y"])), 3)
        self.screen.blit(particle_layer, (0, 0))

        # Aim line — draw on a SRCALPHA surface so the alpha channel
        # actually takes effect (pygame.draw.line on the main screen
        # silently drops the alpha).
        if self.state == "playing":
            mx, my = pygame.mouse.get_pos()
            dx = math.cos(self.aim_angle)
            dy = math.sin(self.aim_angle)
            end = (SHOOTER_POS[0] + dx * 200, SHOOTER_POS[1] + dy * 200)
            aim_surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            pygame.draw.line(aim_surf, (*self.track_accent, 125),
                             SHOOTER_POS, end, 2)
            # Dotted extension toward the cursor for finer aiming feel.
            steps = 8
            for i in range(steps):
                t = (i + 1) / steps
                px = int(end[0] + (mx - end[0]) * t * 0.5)
                py = int(end[1] + (my - end[1]) * t * 0.5)
                pygame.draw.circle(aim_surf, (*COLORS["text"], 85),
                                   (px, py), 1)
            self.screen.blit(aim_surf, (0, 0))

        # Shooter
        self._draw_shooter()

        # HUD — two white paper labels leave the colored track as the focus.
        left_hud = pygame.Rect(14, 10, 390, 48)
        right_hud = pygame.Rect(WIDTH - 330, 10, 316, 48)
        draw_panel(self.screen, left_hud, fill=COLORS["panel"],
                   border=self.track_accent, radius=8, shadow=False)
        draw_panel(self.screen, right_hud, fill=COLORS["panel"],
                   border=COLORS["border"], radius=8, shadow=False)
        draw_text(self.screen,
                  f"第 {self.level_idx + 1}/{len(ZUMA_LEVELS)} 关 · {self.track_name}",
                  (left_hud.x + 13, left_hud.y + 7),
                  size=16, color=self.track_accent, bold=True)
        draw_text(self.screen,
                  f"得分 {self.score} · 已解锁 {self.unlocked_level}/{len(ZUMA_LEVELS)}",
                  (left_hud.x + 13, left_hud.y + 28), size=12,
                  color=COLORS["text_dim"])
        if self.progress_save_message:
            draw_text(self.screen, self.progress_save_message,
                      (WIDTH // 2, 70), size=11,
                      color=COLORS["danger"], center=True)
        # HUD — right-aligned stats so they don't overflow the window
        # even with long combo counts.
        from client.common.ui import font as _font
        remaining = (self.level_ball_count - self.spawned
                     + len(self.chain) + len(self.incoming))
        stats_line = (f"剩余 {remaining} 球   速度 {self.chain_speed:g}"
                      f"   连击 x{self.combo}")
        sw = _font(14).size(stats_line)[0]
        draw_text(self.screen, stats_line,
                  (right_hud.right - sw - 12, right_hud.y + 16),
                  size=14, color=COLORS["text"])
        if self.chain_banner_timer > 0.0:
            alpha = min(255, int(self.chain_banner_timer * 420))
            banner = pygame.Surface((180, 38), pygame.SRCALPHA)
            pygame.draw.rect(banner, (*COLORS["accent2"], min(210, alpha)),
                             banner.get_rect(), border_radius=18)
            label = _font(16, bold=True).render(
                f"连锁消除 ×{self.chain_banner_depth}", True, (24, 35, 40))
            banner.blit(label, label.get_rect(center=banner.get_rect().center))
            self.screen.blit(banner, (WIDTH // 2 - 90, 69))
        if self.shot_queue:
            draw_text(self.screen, f"发射队列 {self.shot_queue}",
                      (SHOOTER_POS[0] + 38, SHOOTER_POS[1] + 28), size=10,
                      color=COLORS["accent"], center=True)

        if self.state == "paused":
            self.draw_paused_overlay()
        elif self.state == "gameover":
            btns = [
                Button(pygame.Rect(0, 0, 150, 36), "重新开始 (R)",
                       self.request_reset, primary=True),
                Button(pygame.Rect(0, 0, 150, 36), "返回菜单 (Esc)",
                       self.request_exit),
            ]
            detail = (f"止步第 {self.level_idx + 1}/{len(ZUMA_LEVELS)} 关"
                      f"  ·  共消除 {self.cleared_balls} 球")
            self.draw_gameover_overlay("游戏结束", buttons=btns,
                                       detail=detail)
        elif self.state == "won":
            final = self.level_idx == len(ZUMA_LEVELS) - 1
            if final:
                msg = "全部通关！"
                btns = [
                    Button(pygame.Rect(0, 0, 150, 36), "再玩一轮 (R)",
                           self.request_reset, primary=True),
                    Button(pygame.Rect(0, 0, 150, 36), "返回菜单 (Esc)",
                           self.request_exit),
                ]
            else:
                msg = f"通过第 {self.level_idx + 1} 关！"
                btns = [
                    Button(pygame.Rect(0, 0, 150, 36), "下一关 (N)",
                           self.advance_level, primary=True),
                    Button(pygame.Rect(0, 0, 150, 36), "从头开始 (R)",
                           self.request_reset),
                    Button(pygame.Rect(0, 0, 150, 36), "返回菜单 (Esc)",
                           self.request_exit),
                ]
            detail = (f"本关奖励 +{self.level_bonus}  ·  "
                      f"累计消除 {self.cleared_balls} 球")
            self.draw_gameover_overlay(msg, buttons=btns, detail=detail)

    # ---------- sprites ------------------------------------------------
    def _draw_ball(self, x, y, color_idx, highlight=False, alpha=255):
        from client.common.ui import GAME_COLORS
        base = GAME_COLORS["zuma"][color_idx % NUM_COLORS]
        ix, iy = int(x), int(y)
        if alpha >= 255:
            pygame.draw.circle(self.screen, (4, 14, 22),
                               (ix + 2, iy + 3), BALL_R + 2)
            pygame.draw.circle(self.screen,
                               tuple(max(0, c - 55) for c in base),
                               (ix, iy), BALL_R + 1)
            pygame.draw.circle(self.screen, base, (ix, iy), BALL_R)
            pygame.draw.circle(self.screen, (246, 239, 218),
                               (ix, iy), BALL_R, 1)
            pygame.draw.circle(self.screen,
                               tuple(min(255, c + 72) for c in base),
                               (ix - 4, iy - 5), 4)
            pygame.draw.arc(self.screen,
                            tuple(max(0, c - 45) for c in base),
                            pygame.Rect(ix - 7, iy - 4, 14, 12),
                            0.15, math.pi - 0.15, 1)
            if highlight:
                pygame.draw.circle(self.screen, COLORS["accent2"],
                                   (ix, iy), BALL_R + 3, 2)
        else:
            # Translucent ball — used for the single visible marker of the
            # waiting entrance queue.
            sz = BALL_R * 2 + 4
            surf = pygame.Surface((sz, sz), pygame.SRCALPHA)
            pygame.draw.circle(surf, (*base, alpha),
                               (sz // 2, sz // 2), BALL_R)
            pygame.draw.circle(surf, (20, 22, 30, alpha),
                               (sz // 2, sz // 2), BALL_R, 1)
            self.screen.blit(surf, (ix - sz // 2, iy - sz // 2))

    def _draw_shooter(self):
        sx, sy = SHOOTER_POS
        # Frog-like lacquered shooter: broad silhouette, eye stalks, and a
        # bright chamber make it read as a character rather than a plain disc.
        pygame.draw.ellipse(self.screen, (146, 166, 190),
                            pygame.Rect(sx - 37, sy + 12, 74, 19))
        pygame.draw.circle(self.screen, (47, 159, 125), (sx, sy + 3), 30)
        pygame.draw.circle(self.screen, (83, 207, 162), (sx, sy), 27)
        for eye_x in (sx - 17, sx + 17):
            pygame.draw.circle(self.screen, (83, 207, 162),
                               (eye_x, sy - 20), 10)
            pygame.draw.circle(self.screen, COLORS["text"],
                               (eye_x, sy - 22), 5)
            pygame.draw.circle(self.screen, (21, 43, 55),
                               (eye_x, sy - 22), 2)
        pygame.draw.arc(self.screen, (22, 87, 69),
                        pygame.Rect(sx - 13, sy + 6, 26, 15), 0.15,
                        math.pi - 0.15, 2)
        # barrel pointing at aim
        dx = math.cos(self.aim_angle)
        dy = math.sin(self.aim_angle)
        barrel_end = (sx + dx * 28, sy + dy * 28)
        pygame.draw.line(self.screen, (24, 79, 68),
                         (sx, sy), barrel_end, 10)
        pygame.draw.line(self.screen, self.track_accent,
                         (sx, sy), barrel_end, 4)
        # current ball in chamber
        self._draw_ball(sx, sy, self.current_color)
        # next ball preview
        pygame.draw.circle(self.screen, COLORS["panel"],
                           (sx - 38, sy + 8), 12)
        pygame.draw.circle(self.screen, self.track_accent,
                           (sx - 38, sy + 8), 12, 1)
        from client.common.ui import GAME_COLORS
        nc = GAME_COLORS["zuma"][self.next_color % NUM_COLORS]
        pygame.draw.circle(self.screen, nc, (sx - 38, sy + 8), 9)
        draw_text(self.screen, "下一个", (sx - 38, sy + 28),
                  size=10, color=COLORS["text_dim"], center=True)


def run_game(backend: Optional[GameDataService] = None,
             player: str = "anonymous",
             profile_id: Optional[str] = None) -> None:
    Zuma(backend=backend, player=player, profile_id=profile_id).run()


if __name__ == "__main__":
    run_game()
