"""Game launcher / hub for the five locally installed games."""
from __future__ import annotations

import atexit
import importlib
import os
import sys
import time
from pathlib import Path
from typing import List

import pygame

# Make `client.*` importable whether we run from project root or this folder.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client.common.ui import (  # noqa: E402
                              COLORS, draw_card, draw_playroom_backdrop,
                              draw_leaderboard, draw_text, font)
from game_service.catalog import GAME_BY_ID, GAMES  # noqa: E402
from game_service.local_backend import LocalBackendClient  # noqa: E402

WIDTH, HEIGHT = 980, 680
LEADERBOARD_REFRESH_SECS = 4.0
HEALTH_REFRESH_SECS = 5.0


def import_game_module(game_id: str):
    return importlib.import_module(GAME_BY_ID[game_id].module)


# ---------------------------------------------------------------------------
# Per-game programmatic icons. Drawn fresh each frame so they always render
# crisply at the current card size; we don't depend on any external image
# assets (which keeps the project self-contained).
#
# Smooth vector shapes keep the icons recognizable at card size and avoid
# introducing external image assets.
# ---------------------------------------------------------------------------
def draw_game_icon(surf, gid: str, cx: int, cy: int, size: int = 56) -> None:
    """Draw a small visual signature for each game centered at (cx, cy)."""
    s = size
    if gid == "tetris":
        # Classic T-piece with 4 multi-color cells.
        cell = s // 4
        # cells relative to center: top-center, mid-left, mid-center, mid-right
        cells = [(0, -cell), (-cell, 0), (0, 0), (cell, 0)]
        palette = [
            (160, 100, 240),   # purple (top)
            (240, 200, 80),    # gold (left)
            (90, 200, 200),    # cyan (center)
            (240, 100, 100),   # red (right)
        ]
        for (dx, dy), c in zip(cells, palette):
            r = pygame.Rect(0, 0, cell - 2, cell - 2)
            r.center = (cx + dx, cy + dy)
            pygame.draw.rect(surf, c, r, border_radius=4)
            pygame.draw.rect(surf, tuple(max(0, x - 70) for x in c),
                             r, 2, border_radius=4)
            # tiny inner highlight
            pygame.draw.rect(surf, tuple(min(255, x + 50) for x in c),
                             pygame.Rect(r.x + 3, r.y + 3, r.w - 6, 3),
                             border_radius=2)

    elif gid == "snake":
        # Snake body (3 green segments) + red apple with leaf.
        seg = s // 5
        # body — serpentine path of 3 circles
        body_pts = [(cx - s // 2, cy + seg),
                    (cx, cy + seg),
                    (cx, cy - seg),
                    (cx + s // 2, cy - seg)]
        for i, pt in enumerate(body_pts):
            shade = (90 + i * 15, 190 + i * 10, 100)
            pygame.draw.circle(surf, shade, pt, seg)
            pygame.draw.circle(surf, (40, 90, 50), pt, seg, 2)
            # eye on the head (last segment)
            if i == len(body_pts) - 1:
                pygame.draw.circle(surf, (250, 250, 250),
                                   (pt[0] + 3, pt[1] - 2), 3)
                pygame.draw.circle(surf, (20, 30, 30),
                                   (pt[0] + 4, pt[1] - 2), 2)
        # apple (top right)
        ax, ay = cx + s // 2 - 2, cy - s // 2 + 4
        pygame.draw.circle(surf, (230, 70, 70), (ax, ay), seg - 1)
        pygame.draw.circle(surf, (150, 30, 30), (ax, ay), seg - 1, 2)
        pygame.draw.circle(surf, (255, 180, 180), (ax - 2, ay - 2), 2)
        # leaf
        pygame.draw.polygon(surf, (110, 200, 110),
                            [(ax, ay - seg), (ax + 4, ay - seg - 3),
                             (ax + 1, ay - seg + 1)])

    elif gid == "2048":
        # Stylized "2048" tile — a rounded square with two stacked
        # smaller squares (representing 2+2=4 merging) inside.
        outer = s * 9 // 10
        r = pygame.Rect(0, 0, outer, outer)
        r.center = (cx, cy)
        # gradient body (orange→dark orange)
        body = pygame.Surface((outer, outer), pygame.SRCALPHA)
        for y in range(outer):
            t = y / max(1, outer - 1)
            c = (int(245 - 30 * t), int(165 - 60 * t), int(90 - 30 * t))
            pygame.draw.line(body, c, (0, y), (outer, y))
        mask = pygame.Surface((outer, outer), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255),
                         pygame.Rect(0, 0, outer, outer), border_radius=10)
        body.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(body, r.topleft)
        pygame.draw.rect(surf, (200, 110, 50), r, 3, border_radius=10)
        # Draw "2048" text — fits comfortably now since icon is bigger.
        draw_text(surf, "2048", (cx, cy), size=int(s * 0.27),
                  color=(250, 248, 240), bold=True, center=True)

    elif gid == "sokoban":
        # Brown box (with X mark + wood grain) sitting on a yellow target.
        box_s = s * 2 // 3
        # target ring (below box)
        target_y = cy + box_s // 2 + 4
        pygame.draw.circle(surf, COLORS["accent2"], (cx, target_y), box_s // 4)
        pygame.draw.circle(surf, (200, 150, 50), (cx, target_y), box_s // 4, 2)
        pygame.draw.circle(surf, COLORS["accent"],
                           (cx, target_y), box_s // 4 + 4, 1)
        # box
        box = pygame.Rect(0, 0, box_s, box_s)
        box.center = (cx, cy - 2)
        # gradient body
        boxbody = pygame.Surface((box_s, box_s), pygame.SRCALPHA)
        for y in range(box_s):
            t = y / max(1, box_s - 1)
            c = (int(190 - 30 * t), int(130 - 30 * t), int(70 - 20 * t))
            pygame.draw.line(boxbody, c, (0, y), (box_s, y))
        surf.blit(boxbody, box.topleft)
        pygame.draw.rect(surf, (110, 70, 30), box, 2, border_radius=3)
        # X cross (wooden plank暗示)
        pygame.draw.line(surf, (110, 70, 30),
                         box.topleft, box.bottomright, 2)
        pygame.draw.line(surf, (110, 70, 30),
                         (box.right, box.y), (box.x, box.bottom), 2)

    elif gid == "zuma":
        # Three colored balls (red/blue/green) in a row + a shooter.
        r = s // 5
        ball_data = [
            (cx - s // 2 + r, cy, (230, 70, 70), (255, 180, 180)),
            (cx, cy, (80, 160, 230), (180, 220, 255)),
            (cx + s // 2 - r, cy, (110, 200, 110), (200, 240, 200)),
        ]
        for bx, by, base, hi in ball_data:
            pygame.draw.circle(surf, base, (bx, by), r)
            pygame.draw.circle(surf, (20, 22, 30), (bx, by), r, 2)
            pygame.draw.circle(surf, hi, (bx - r // 3, by - r // 3),
                               max(2, r // 3))
        # shooter below — purple/pink frog body
        # Keep the frog inside the icon zone.  The old ``+ 2`` position
        # extended into the title line and made “祖玛” look pasted on top of
        # the artwork; this leaves a deliberate text-safe gutter below it.
        sx, sy = cx, cy + s // 2 - 6
        pygame.draw.circle(surf, (220, 110, 180), (sx, sy), r + 1)
        pygame.draw.circle(surf, (130, 50, 100), (sx, sy), r + 1, 2)
        # shooter barrel pointing up
        pygame.draw.line(surf, (220, 110, 180),
                         (sx, sy), (sx, sy - r - 2), 4)

    else:
        pygame.draw.circle(surf, COLORS["accent"], (cx, cy), s // 2)


def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("经典小游戏 · 启动器")
    clock = pygame.time.Clock()

    use_http = os.environ.get("GAMES_USE_HTTP") == "1"
    if use_http:
        try:
            from client.common.network import BackendClient
        except ImportError as exc:
            raise RuntimeError(
                "HTTP 调试模式需要安装可选依赖：pip install '.[api]'") from exc
        backend = BackendClient()
    else:
        backend = LocalBackendClient()
    atexit.register(backend.close)
    # Paint the launcher immediately from local metadata while the localhost
    # health check runs in the background. A stopped backend can otherwise
    # freeze the very first frame for the full HTTP timeout.
    online = False
    last_health_check = time.monotonic()
    health_future = backend.health_async()
    games_meta = [game.public_dict() for game in GAMES]

    # ----- Layout: 5 cards in a single row + 2 leaderboard panels below
    cols = 5
    card_w, card_h = 168, 220
    gap_x = 14
    start_x = (WIDTH - (cols * card_w + (cols - 1) * gap_x)) // 2
    start_y = 110
    cards: List[dict] = []
    for i, g in enumerate(games_meta):
        rect = pygame.Rect(start_x + i * (card_w + gap_x),
                           start_y, card_w, card_h)
        cards.append({"meta": g, "rect": rect})

    # Empty means the visible "guest" text is a placeholder.  Typing after
    # the first click now starts a real name instead of appending to the
    # literal string "guest".
    player = ""

    # Bottom panels — leaderboard (left, switches on hover) + recent (right).
    panel_y = start_y + card_h + 24
    panel_h = HEIGHT - panel_y - 36
    lb_rect = pygame.Rect(40, panel_y, WIDTH // 2 - 60, panel_h)
    recent_rect = pygame.Rect(WIDTH // 2 + 20, panel_y,
                              WIDTH // 2 - 60, panel_h)

    editing_player = False
    player_input_rect = pygame.Rect(WIDTH - 220, 28, 180, 30)

    # Per-game leaderboard cache. ``current_lb_game`` is the game whose
    # leaderboard is currently displayed (changes when the user hovers a
    # different card).
    lb_cache: dict = {}      # game_id -> list[dict]
    lb_cache_ts: dict = {}   # game_id -> last fetch time
    recent_cache: List[dict] = []
    recent_ts = 0.0
    leaderboard_futures: dict = {}
    recent_future = None
    records_error = None
    current_lb_game = "tetris"
    if games_meta:
        current_lb_game = games_meta[0]["id"]

    def refresh_game_leaderboard(gid: str, force: bool = False) -> None:
        if not online:
            return
        now = time.monotonic()
        if not force and gid in lb_cache_ts \
                and now - lb_cache_ts[gid] < LEADERBOARD_REFRESH_SECS:
            return
        if gid not in leaderboard_futures:
            leaderboard_futures[gid] = backend.leaderboard_async(
                gid, limit=10)

    def refresh_recent(force: bool = False) -> None:
        nonlocal recent_cache, recent_ts, recent_future
        if not online:
            return
        now = time.monotonic()
        if not force and recent_ts and now - recent_ts < LEADERBOARD_REFRESH_SECS:
            return
        if recent_future is None:
            recent_future = backend.recent_async(limit=8)

    def poll_network() -> None:
        nonlocal online, health_future, recent_cache, recent_ts, recent_future
        nonlocal records_error
        if health_future is not None and health_future.done():
            was_online = online
            try:
                online = bool(health_future.result())
            except Exception:  # noqa: BLE001
                online = False
                records_error = "本机记录暂时不可读"
            if not online and getattr(backend, "last_read_error", None):
                records_error = backend.last_read_error
            health_future = None
            if online and not was_online:
                for game in games_meta:
                    refresh_game_leaderboard(game["id"], force=True)
                refresh_recent(force=True)
        for gid, future in list(leaderboard_futures.items()):
            if not future.done():
                continue
            try:
                result = future.result()
                lb_cache[gid] = result if isinstance(result, list) else []
            except Exception:  # noqa: BLE001
                records_error = "本机记录暂时不可读"
            lb_cache_ts[gid] = time.monotonic()
            del leaderboard_futures[gid]
        if recent_future is not None and recent_future.done():
            try:
                result = recent_future.result()
                recent_cache = result if isinstance(result, list) else []
                records_error = None
            except Exception:  # noqa: BLE001
                records_error = "本机记录暂时不可读"
            recent_ts = time.monotonic()
            recent_future = None

    for gm in games_meta:
        refresh_game_leaderboard(gm["id"], force=True)
    refresh_recent(force=True)

    def reset_after_subgame() -> pygame.Surface:
        """Re-create the launcher window after a sub-game exits.

        Sub-games now close only their DISPLAY (via
        ``pygame.display.quit()`` in BaseGame.run) instead of shutting
        down all of pygame. That means pygame.font, pygame.time, and our
        ``_font_cache`` are all still valid — we just need to spin up a
        fresh window. This is what makes the launcher feel instant when
        returning from a game (the old version called ``pygame.quit()``
        + ``pygame.init()`` and could hang for ~1 minute on macOS while
        SDL rebuilt itself and the font cache was rescanned).

        We also do NOT issue any HTTP calls here — the launcher main
        loop already refreshes leaderboards every
        ``LEADERBOARD_REFRESH_SECS`` seconds. Doing sync HTTP in this
        critical path would block the redraw.
        """
        pygame.display.init()
        screen_ = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("经典小游戏 · 启动器")
        pygame.event.get()
        pygame.event.clear()
        return screen_

    lb_title_map = {g["id"]: g["name"] for g in games_meta}
    accent_map = {game.id: COLORS[game.color_key] for game in GAMES}
    tag_map = {game.id: game.tag for game in GAMES}
    # Animated hover lift: each card has a current lift value that we
    # ease toward its target (1.0 on hover, 0.0 otherwise) every frame.
    card_lift = {g["id"]: 0.0 for g in games_meta}
    launch_error = getattr(backend, "initialization_error", None)
    quit_with_unsaved_armed = False
    quit_with_unsaved_deadline = 0.0

    def exit_confirmed() -> bool:
        nonlocal quit_with_unsaved_armed, quit_with_unsaved_deadline
        durable = getattr(backend, "pending_saves_are_durable", False)
        if durable or backend.failed_save_count() == 0:
            return True
        now_ = time.monotonic()
        if quit_with_unsaved_armed and now_ <= quit_with_unsaved_deadline:
            return True
        quit_with_unsaved_armed = True
        quit_with_unsaved_deadline = now_ + 3.0
        return False

    running = True
    mouse_pos = pygame.mouse.get_pos()
    while running:
        frame_dt = clock.tick(60) / 1000.0
        now = time.monotonic()
        if quit_with_unsaved_armed and now > quit_with_unsaved_deadline:
            quit_with_unsaved_armed = False
        poll_pending = getattr(backend, "poll_pending_saves", None)
        if callable(poll_pending):
            poll_pending()
        poll_network()
        if (health_future is None
                and now - last_health_check >= HEALTH_REFRESH_SECS):
            health_future = backend.health_async()
            last_health_check = now
        # Hovered card → switch leaderboard view.
        hovered_game = current_lb_game
        for card in cards:
            if card["rect"].collidepoint(mouse_pos):
                hovered_game = card["meta"]["id"]
                break
        if hovered_game != current_lb_game:
            current_lb_game = hovered_game
            refresh_game_leaderboard(current_lb_game, force=False)

        # Periodic refresh of the displayed game + recent.
        refresh_game_leaderboard(current_lb_game)
        refresh_recent()

        # Ease card lifts toward target.
        for card in cards:
            gid = card["meta"]["id"]
            target = 1.0 if card["rect"].collidepoint(mouse_pos) else 0.0
            cur = card_lift[gid]
            card_lift[gid] = cur + (target - cur) * min(1.0, frame_dt * 12.0)

        launched_this_frame = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                if exit_confirmed():
                    running = False
                    break
                continue
            elif event.type == pygame.MOUSEMOTION:
                mouse_pos = event.pos
                continue
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if editing_player:
                        editing_player = False
                    elif exit_confirmed():
                        running = False
                    continue
                elif editing_player:
                    if event.key == pygame.K_RETURN:
                        editing_player = False
                    elif event.key == pygame.K_BACKSPACE:
                        player = player[:-1]
                    elif (event.unicode and len(player) < 32
                          and event.unicode.isprintable()):
                        player += event.unicode
                    continue
                elif event.key == pygame.K_s and backend.failed_save_count():
                    backend.retry_failed_saves()
                    quit_with_unsaved_armed = False
                    quit_with_unsaved_deadline = 0.0
                    continue
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if player_input_rect.collidepoint(event.pos):
                    editing_player = True
                    continue
                editing_player = False
                for card in cards:
                    gid = card["meta"]["id"]
                    visible_rect = card["rect"].move(
                        0, -int(card_lift[gid] * 6))
                    if visible_rect.collidepoint(event.pos):
                        try:
                            launch_error = None
                            mod = import_game_module(gid)
                            mod.run_game(backend=backend,
                                         player=player.strip() or "guest")
                        except Exception as e:  # noqa: BLE001
                            launch_error = (
                                f"{card['meta']['name']}启动失败，请查看终端日志")
                            print(f"[launcher] failed to launch {gid}: {e}")
                        screen = reset_after_subgame()
                        lb_cache_ts[gid] = 0.0
                        recent_ts = 0.0
                        last_health_check = 0.0
                        launched_this_frame = True
                        break
                if launched_this_frame:
                    break

        # ---- Draw -------------------------------------------------------
        draw_playroom_backdrop(screen)

        # ---- Header -----------------------------------------------------
        hero = pygame.Rect(28, 15, 560, 74)
        pygame.draw.rect(screen, COLORS["panel"], hero, border_radius=18)
        pygame.draw.rect(screen, COLORS["border"], hero, 1, border_radius=18)
        pygame.draw.circle(screen, COLORS["game_tetris"],
                           (hero.x + 20, hero.centery), 5)
        draw_text(screen, "童年小游戏集合", (52, 23), size=30,
                  color=COLORS["text"], bold=True)
        draw_text(screen, "挑一个颜色，马上开始；悬停卡片可以查看排行",
                  (53, 60), size=13, color=COLORS["text_dim"])
        dot_colors = [COLORS["game_tetris"], COLORS["game_snake"],
                      COLORS["game_2048"], COLORS["game_sokoban"],
                      COLORS["game_zuma"]]
        for dot_index, dot_color in enumerate(dot_colors):
            pygame.draw.circle(screen, dot_color,
                               (455 + dot_index * 21, 39), 6)

        # Player name input (top-right) — truncate the visible string to
        # fit the input field so 16 CJK chars + cursor can't overflow.
        draw_text(screen, "玩家信息", (WIDTH - 282, 20), size=11,
                  color=COLORS["accent"], bold=True)
        draw_text(screen, "名字", (WIDTH - 270, 43), size=13,
                  color=COLORS["text_dim"])
        pygame.draw.rect(screen, COLORS["panel"], player_input_rect,
                         border_radius=6)
        pygame.draw.rect(screen,
                         COLORS["accent"] if editing_player else COLORS["border"],
                         player_input_rect, 1, border_radius=6)
        shown = player or "guest"
        cursor_visible = (editing_player
                          and int(time.monotonic() * 2) % 2 == 0)
        if cursor_visible:
            shown = player + "|"
        # Truncate to field width (180 - 16 padding = 164 usable).
        max_w = player_input_rect.w - 16
        f14 = font(14)
        while shown and f14.size(shown)[0] > max_w:
            # While editing, keep the newest suffix and blinking cursor in
            # view. The old code removed the cursor first and showed only the
            # beginning of long names, making editing appear unfocused.
            shown = shown[1:] if cursor_visible else shown[:-1]
        draw_text(screen, shown,
                  (player_input_rect.x + 8, player_input_rect.y + 7),
                  size=14, color=COLORS["text"])

        # ---- Cards / bright toy-box stickers ---------------------------
        for card_index, card in enumerate(cards):
            gid = card["meta"]["id"]
            accent = accent_map.get(gid, COLORS["accent"])
            is_selected = (gid == current_lb_game)
            lift = card_lift[gid]
            # The card "lifts" up by a few pixels on hover for a tactile feel.
            lifted_rect = card["rect"].move(0, -int(lift * 6))
            draw_card(screen, lifted_rect,
                      hover=(lift > 0.05),
                      accent=accent, selected=is_selected)
            # Color tab encodes the game's kind; unlike the old cabinet
            # number, it carries useful content and avoids retro styling.
            tab = pygame.Rect(lifted_rect.x + 15, lifted_rect.y + 10,
                              lifted_rect.w - 30, 25)
            pygame.draw.rect(screen, accent, tab, border_radius=13)
            draw_text(screen, tag_map.get(gid, "开始游戏"), tab.center,
                      size=10, color=(255, 255, 255), bold=True, center=True)
            # Game icon
            draw_game_icon(screen, gid,
                           lifted_rect.centerx,
                           lifted_rect.y + 69, size=44)
            # Name
            draw_text(screen, card["meta"]["name"],
                      (lifted_rect.centerx, lifted_rect.y + 118),
                      size=20, color=accent, bold=True, center=True)
            # Descriptions are deliberately kept to one short line.
            draw_text(screen, card["meta"]["description"],
                      (lifted_rect.centerx, lifted_rect.y + 145),
                      size=11, color=COLORS["text_dim"], center=True)
            # Top 1 player from cache
            top = (lb_cache.get(gid) or [{}])[0]
            if top.get("player"):
                tp = (top.get("player") or "?")[:10]
                ts = top.get("score", 0)
                draw_text(screen, f"Top: {tp}  {ts}",
                          (lifted_rect.centerx, lifted_rect.y + 171),
                          size=11, color=COLORS["accent2"], center=True)
            else:
                draw_text(screen, "（暂无记录）",
                          (lifted_rect.centerx, lifted_rect.y + 171),
                          size=10, color=COLORS["text_dim"], center=True)
            # Plays count if available
            # Status hint at bottom
            status = "已选中 · 点击开始" if is_selected else (
                "松手即可启动" if lift > 0.05 else "点击开始")
            draw_text(screen, status,
                      (lifted_rect.centerx, lifted_rect.y + 199),
                      size=10,
                      color=accent if (lift > 0.05 or is_selected)
                            else COLORS["text_dim"],
                      center=True)

        # ---- Leaderboards ----------------------------------------------
        lb_title = f"{lb_title_map.get(current_lb_game, '?')} · 本机最佳"
        draw_leaderboard(screen, lb_rect,
                         lb_cache.get(current_lb_game, []),
                         title=lb_title)
        recent_entries = [{"rank": i + 1,
                           "game_id": r["game_id"],
                           "player": r["player"],
                           "score": r["score"]}
                          for i, r in enumerate(recent_cache)]
        draw_leaderboard(screen, recent_rect, recent_entries,
                         title="最近游戏", show_game=True,
                         game_names=lb_title_map)

        draw_text(screen,
                  f"Esc 退出 · {'API 成绩服务' if use_http else '本机记录'}: "
                  f"{'可用' if online else '不可用'} · "
                  f"玩家: {player.strip() or 'guest'}",
                  (WIDTH // 2, HEIGHT - 14), size=11,
                  color=COLORS["text_dim"], center=True)
        recovery_notice = getattr(backend, "recovery_notice", None)
        if launch_error:
            draw_text(screen, launch_error, (WIDTH // 2, HEIGHT - 34),
                      size=12, color=COLORS["danger"], center=True)
        elif records_error:
            draw_text(screen, records_error, (WIDTH // 2, HEIGHT - 34),
                      size=12, color=COLORS["danger"], center=True)
        elif recovery_notice:
            draw_text(screen, recovery_notice, (WIDTH // 2, HEIGHT - 34),
                      size=12, color=COLORS["accent"], center=True)
        failed_saves = backend.failed_save_count()
        if failed_saves:
            draw_text(screen,
                      ("成绩未落盘 · 3 秒内再次退出将放弃"
                       if quit_with_unsaved_armed
                       else f"有 {failed_saves} 条成绩尚未保存 · 按 S 重试"),
                      (WIDTH // 2, HEIGHT - 34), size=12,
                      color=COLORS["danger"], center=True)

        pygame.display.flip()

    backend.close()
    pygame.quit()


if __name__ == "__main__":
    main()
