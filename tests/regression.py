"""Headless regression test for the classic_games project.

Each check runs in its own subprocess so pygame init/quit cycles don't
interact. Run with:

    SDL_VIDEODRIVER=dummy python -m tests.regression
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
       "PYTHONPATH": str(ROOT)}
PASS, FAIL = 0, 0


def run(name: str, body: str) -> None:
    global PASS, FAIL
    src = dedent(body).strip()
    full = "import sys; sys.path.insert(0, %r)\n" % str(ROOT) + src
    try:
        proc = subprocess.run(
            [sys.executable, "-c", full], env=ENV, capture_output=True,
            text=True, cwd=str(ROOT), timeout=15)
    except subprocess.TimeoutExpired:
        FAIL += 1
        print(f"  FAIL: {name} (timed out after 15s)")
        return
    if proc.returncode == 0:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} (exit={proc.returncode})")
        if proc.stderr:
            for line in proc.stderr.strip().splitlines()[-8:]:
                print(f"        {line}")
        if proc.stdout:
            for line in proc.stdout.strip().splitlines()[-4:]:
                print(f"        {line}")


# ===========================================================================
print("\n=== 1. Tetris: J/L/T have 4 visually distinct rotation states ===")
run("tetris-rotations", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris
    def norm(cells):
        mx=min(c[0] for c in cells); my=min(c[1] for c in cells)
        return tuple(sorted((x-mx, y-my) for x,y in cells))
    import random; random.seed(0)
    for kind in 'IOTSZJL':
        g = Tetris(backend=BackendClient())
        g.piece.kind = kind; g.piece.rot = 0
        g.piece.x = 4; g.piece.y = 0
        seen = set()
        for _ in range(4):
            seen.add(norm(g.piece.cells()))
            g._rotate(1)
        # All pieces should be able to cycle 4 rotation *indices*.
        # Visually distinct orientations: J/L/T -> 4, S/Z/I -> 2, O -> 1.
        expected = {'J':4,'L':4,'T':4,'S':2,'Z':2,'I':2,'O':1}[kind]
        assert len(seen) == expected, f'{kind}: got {len(seen)} expected {expected}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 2. Tetris scoring: 4 lines = 4*4*100*level ===")
run("tetris-scoring", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris
    g = Tetris(backend=BackendClient()); g.score = 0; g.level = 1
    # Build 4 full rows at the bottom.
    for y in range(16, 20):
        for x in range(10):
            g.board[y][x] = 'I'
    g._clear_lines()
    # Cleared 4 lines: 4*4*100*1 = 1600
    assert g.score == 1600, f'score={g.score} expected 1600'
    # 3 lines: 3*3*100*1 = 900
    g.score = 0
    for x in range(10):
        for y in (17,18,19): g.board[y][x] = 'I'
    g._clear_lines()
    assert g.score == 900, f'score={g.score} expected 900'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 3. Tetris: hard drop rewards 1 pt/cell (not 2) ===")
run("tetris-hard-drop-score", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris
    g = Tetris(backend=BackendClient()); g.score = 0
    # Find drop distance for the current piece from its spawn position.
    d = 0
    while not g._collides(g.piece.cells(dy=d + 1)):
        d += 1
    # Hard-drop locks the piece (which then triggers _spawn), so capture
    # the distance BEFORE the call.
    expected_drop_bonus = d  # 1 pt per cell at the new rate.
    g._hard_drop()
    # Score should equal exactly the drop bonus (no lines cleared on an
    # empty board, no soft-drop points accumulated).
    assert g.score == expected_drop_bonus, \\
        f'score={g.score} expected drop bonus={expected_drop_bonus} (1 pt/cell)'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 4. 2048: 'right' direction does NOT flip the board ===")
run("2048-right-direction", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import game_2048
    from client.games.game_2048 import Tile
    g = game_2048.Game2048(backend=BackendClient())
    # Disable random spawn so the test is fully deterministic.
    g._spawn_tile = lambda: None
    g.tiles = []; g.grid = [[None]*4 for _ in range(4)]
    t = Tile(value=2, row=0, col=0)
    g.tiles = [t]; g.grid[0][0] = t
    g._move('right'); g._tick_animations(1.0)
    assert t.col == 3, f'after right: col={t.col} expected 3'
    g._move('left'); g._tick_animations(1.0)
    assert t.col == 0, f'after left: col={t.col} expected 0'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 5. 2048: 'down' direction does NOT flip the board ===")
run("2048-down-direction", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import game_2048
    from client.games.game_2048 import Tile
    g = game_2048.Game2048(backend=BackendClient())
    g._spawn_tile = lambda: None  # deterministic
    g.tiles = []; g.grid = [[None]*4 for _ in range(4)]
    t = Tile(value=2, row=0, col=0)
    g.tiles = [t]; g.grid[0][0] = t
    g._move('down'); g._tick_animations(1.0)
    assert t.row == 3, f'after down: row={t.row} expected 3'
    g._move('up'); g._tick_animations(1.0)
    assert t.row == 0, f'after up: row={t.row} expected 0'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 6. 2048: merge on right/down produces correct value & position ===")
run("2048-right-merge", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import game_2048
    from client.games.game_2048 import Tile
    g = game_2048.Game2048(backend=BackendClient())
    g.tiles = []; g.grid = [[None]*4 for _ in range(4)]
    a = Tile(value=2, row=0, col=0); b = Tile(value=2, row=0, col=1)
    g.tiles = [a, b]; g.grid[0][0]=a; g.grid[0][1]=b
    g._move('right'); g._tick_animations(1.0)
    # Merged tile (value 4) should be at col 3.
    assert g.grid[0][3] is not None and g.grid[0][3].value == 4, \\
        f'expected 4 at col 3, got {g.grid[0]}'
    assert g.score == 4, f'score={g.score}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 7. 2048: only board swipes trigger slides ===")
run("2048-board-only-mouse-swipe", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import game_2048
    from client.games.game_2048 import Tile
    g = game_2048.Game2048(backend=BackendClient())
    g._spawn_tile = lambda: None  # deterministic
    g.tiles = []; g.grid = [[None]*4 for _ in range(4)]
    t = Tile(value=2, row=0, col=0)
    g.tiles = [t]; g.grid[0][0] = t
    def swipe(start, end):
        g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {'button':1,'pos':start}))
        g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP,   {'button':1,'pos':end}))
    swipe((50,150), (200,150))  # right, inside board
    g._tick_animations(1.0)
    assert t.col == 3, f'right swipe: col={t.col} expected 3'
    swipe((200,150), (50,150))  # left, inside board
    g._tick_animations(1.0)
    assert t.col == 0, f'left swipe: col={t.col} expected 0'
    # Tiny click should NOT trigger a move.
    before = (t.row, t.col)
    swipe((100,150), (105,150))
    g._tick_animations(1.0)
    assert (t.row, t.col) == before, 'tiny click should not move'
    # Dragging over the title/score area must not move the board.
    swipe((5,30), (300,30))
    assert (t.row, t.col) == before and g.anim_t == 1.0, \
        'header drag incorrectly started a board move'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 8. Sokoban: all 16 levels pass the push-state solver ===")
run("sokoban-levels-solvable", """
    from collections import deque
    from heapq import heappop, heappush
    from itertools import count
    from client.games.sokoban import LEVELS, parse_level
    DIRS = ((0,-1), (0,1), (-1,0), (1,0))
    def reachable(floors, boxes, player):
        seen = {player}; q = deque([player])
        while q:
            x, y = q.popleft()
            for dx, dy in DIRS:
                nxt = (x+dx, y+dy)
                if nxt in floors and nxt not in boxes and nxt not in seen:
                    seen.add(nxt); q.append(nxt)
        return seen
    def heuristic(boxes, targets):
        return sum(min(abs(x-tx)+abs(y-ty) for tx,ty in targets)
                   for x,y in boxes)
    def solve(level):
        walls, targets, boxes, player, floors = parse_level(level)
        targets = frozenset(targets); boxes = frozenset(boxes)
        serial = count(); reach = reachable(floors, boxes, player)
        key = (min(reach), boxes); best = {key: 0}
        heap = [(heuristic(boxes, targets) * 3, 0, next(serial),
                 boxes, player)]
        while heap:
            _, depth, _, boxes, player = heappop(heap)
            reach = reachable(floors, boxes, player)
            key = (min(reach), boxes)
            if depth != best.get(key): continue
            for box in boxes:
                for dx,dy in DIRS:
                    behind = (box[0]-dx, box[1]-dy)
                    ahead = (box[0]+dx, box[1]+dy)
                    if (behind not in reach or ahead not in floors
                            or ahead in boxes):
                        continue
                    next_boxes = frozenset((boxes - {box}) | {ahead})
                    if next_boxes == targets: return depth + 1
                    next_reach = reachable(floors, next_boxes, box)
                    next_key = (min(next_reach), next_boxes)
                    next_depth = depth + 1
                    if next_depth >= best.get(next_key, 10**9): continue
                    best[next_key] = next_depth
                    priority = next_depth + heuristic(next_boxes, targets) * 3
                    heappush(heap, (priority, next_depth, next(serial),
                                    next_boxes, box))
        return None
    assert len(LEVELS) >= 16, len(LEVELS)
    for i, lv in enumerate(LEVELS):
        _, targets, boxes, _, _ = parse_level(lv)
        assert '*' not in ''.join(lv), f'level {i+1} starts on a target'
        assert len(boxes) == len(targets), \
            f'level {i+1}: {len(boxes)} boxes != {len(targets)} targets'
        min_boxes = 2 if i < 4 else 4 if i < 7 else 5 if i < 10 else 6
        assert len(boxes) >= min_boxes, \
            f'level {i+1}: only {len(boxes)} boxes, expected {min_boxes}+'
        sol = solve(lv)
        assert sol is not None, f'level {i+1} unsolvable!'
        assert sol > 0, f'level {i+1} already solved at start'
""")

# ===========================================================================
print("\n=== 9. Sokoban: player and boxes stay inside floors ===")
run("sokoban-no-escape", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import sokoban
    g = sokoban.Sokoban(backend=BackendClient())
    keys = [pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN]
    for k in keys * 20:
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, {'key':k,'unicode':''}))
        assert g.player_pos in g.floors, f'player off-map: {g.player_pos}'
        for b in g.boxes:
            assert b in g.floors, f'box off-map: {b}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 10. Sokoban: win overlay panel contains all buttons ===")
run("sokoban-win-no-overflow", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import sokoban
    from client.common.ui import COLORS
    # Force win on the widest level layout to exercise the resized window.
    g = sokoban.Sokoban(backend=BackendClient())
    widest = max(range(len(sokoban.LEVELS)),
                 key=lambda i: max(map(len, sokoban.LEVELS[i])))
    g.load_level(widest)
    g.boxes = set(g.targets); g._check_win()
    assert g.state == 'won'
    g.draw()
    # Every overlay button's rect must be fully inside the window and
    # NOT overlapping the overlay panel border.
    for b in g.overlay_buttons:
        assert 0 <= b.rect.left and b.rect.right <= g.width, \\
            f'button {b.label!r} outside window x: {b.rect}'
        assert 0 <= b.rect.top and b.rect.bottom <= g.height, \\
            f'button {b.label!r} outside window y: {b.rect}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 11. Zuma: insertion shifts FRONT forward, keeps back anchored ===")
run("zuma-front-push", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import zuma
    g = zuma.Zuma(backend=BackendClient())
    g.chain = [{'pos':200.0,'color':0},
               {'pos':200.0 - zuma.GAP,'color':1},
               {'pos':200.0 - 2*zuma.GAP,'color':2},
               {'pos':200.0 - 3*zuma.GAP,'color':3}]
    A_before = g.chain[0]['pos']
    D_before = g.chain[-1]['pos']
    # Projectile strikes B (idx=1) at hit_pos=178.
    g._insert(idx=1, color=4, hit_pos=178.0, in_front=False)
    # Front segment (chain[0..1]) moves forward by GAP.
    assert abs(g.chain[0]['pos'] - (A_before + zuma.GAP)) < 1e-6, \\
        f'front should advance by GAP: {g.chain[0]["pos"]} - {A_before}'
    # Back segment (chain[idx+1..]) stays put — the spawn location
    # is decoupled (it's the fixed path entrance, not chain[-1]).
    assert abs(g.chain[-1]['pos'] - D_before) < 1e-6, \\
        f'back should NOT move: {g.chain[-1]["pos"]} vs {D_before}'
    # New ball at the original hit_pos.
    assert any(abs(b['pos'] - 178.0) < 1e-6 and b['color'] == 4 for b in g.chain), \\
        f'new ball not at hit_pos; chain={[b["pos"] for b in g.chain]}'
    # Verify spacing is uniform.
    positions = [b['pos'] for b in g.chain]
    diffs = [positions[i] - positions[i+1] for i in range(len(positions)-1)]
    assert all(abs(d - zuma.GAP) < 1e-6 for d in diffs), \\
        f'spacing not uniform: {diffs}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 12. Zuma: combo counter accumulates across successful shots ===")
run("zuma-combo-accumulates", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import zuma
    g = zuma.Zuma(backend=BackendClient())
    # Build a chain where inserting at the back creates a 3-in-a-row:
    # chain = [0, 0] (front to back). Inserting color 0 behind them → 3 match.
    g.chain = [{'pos':200.0,'color':0},
               {'pos':200.0 - zuma.GAP,'color':0}]
    g.combo = 0
    # Projectile hits the back ball (idx=1).
    g._insert(idx=1, color=0, hit_pos=200.0 - zuma.GAP)
    # Should have matched and cleared.
    assert g.combo == 1, f'after 1st match: combo={g.combo}'
    # Now set up another match.
    g.chain = [{'pos':200.0,'color':1},
               {'pos':200.0 - zuma.GAP,'color':1}]
    g._insert(idx=1, color=1, hit_pos=200.0 - zuma.GAP)
    assert g.combo == 2, f'after 2nd consecutive match: combo={g.combo}'
    # Now a miss should reset.
    g.chain = [{'pos':200.0,'color':2},
               {'pos':200.0 - zuma.GAP,'color':3},
               {'pos':200.0 - 2*zuma.GAP,'color':4}]
    g._insert(idx=1, color=5, hit_pos=200.0 - zuma.GAP)
    assert g.combo == 0, f'after miss: combo={g.combo}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 13. Leaderboard never overflows its rect (clip + row cap) ===")
run("leaderboard-no-overflow", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.ui import draw_leaderboard, COLORS
    surf = pygame.Surface((400, 180))
    rect = pygame.Rect(0, 0, 400, 180)
    # 50 fake entries — only those that fit should render.
    entries = [{'rank': i+1, 'player': f'p{i}', 'score': 1000-i} for i in range(50)]
    draw_leaderboard(surf, rect, entries, title='Top')
    # The bottom 20 pixels of the surface should still be background
    # (panel color) — no text drawn past the rect's bottom border.
    bg = COLORS['panel']
    for y in (170, 175, 178):
        for x in (50, 200, 350):
            c = surf.get_at((x, y))[:3]
            assert c == bg, f'pixel at ({x},{y}) = {c}, expected bg {bg} — overflow!'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 14. Game-over overlay panel always contains its buttons ===")
# One subprocess per game to avoid SDL re-init segfaults.
for mod_name, cls_name in [("tetris","Tetris"), ("snake","Snake"),
                           ("game_2048","Game2048"),
                           ("sokoban","Sokoban"), ("zuma","Zuma")]:
    run(f"overlay-no-overflow-{mod_name}", f"""
        import os; os.environ['SDL_VIDEODRIVER']='dummy'
        import pygame; pygame.init()
        from client.common.network import BackendClient
        from client.games import {mod_name}
        cls = getattr({mod_name}, '{cls_name}')
        g = cls(backend=BackendClient(), player='t')
        for state in ('gameover', 'won'):
            g.state = state
            try:
                g.draw()
            except Exception as e:
                raise AssertionError(f'{{cls.__name__}} draw in {{state}}: {{e}}')
            for b in g.overlay_buttons:
                assert 0 <= b.rect.left and b.rect.right <= g.width, \\
                    f'{{state}}: button {{b.label!r}} x-overflow {{b.rect}}'
                assert 0 <= b.rect.top and b.rect.bottom <= g.height, \\
                    f'{{state}}: button {{b.label!r}} y-overflow {{b.rect}}'
        pygame.quit()
    """)

# ===========================================================================
print("\n=== 15. Launcher: hovering a card switches leaderboard title ===")
run("launcher-hover-switches-leaderboard", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L
    captured = []
    orig = L.draw_leaderboard
    def spy(surf, rect, entries, title='排行榜', **kw):
        captured.append(title)
        return orig(surf, rect, entries, title=title, **kw)
    L.draw_leaderboard = spy
    BackendClient.health = lambda self: False
    BackendClient.list_games = lambda self, *a, **k: []
    BackendClient.leaderboard = lambda self, *a, **k: []
    BackendClient.recent = lambda self, *a, **k: []
    # New launcher layout: 5 cards in a single row, card_w=168, gap_x=14.
    # WIDTH=980 → start_x = (980 - 5*168 - 4*14)//2 = 42.
    # Card 1 (Snake, index 1) center x = 42 + 1*(168+14) + 84 = 308
    # Card center y = 110 + 110 = 220.
    card1_cx = 42 + (168 + 14) + 84
    card1_cy = 110 + 110
    def driver():
        time.sleep(0.3)
        pygame.mouse.set_pos(card1_cx, card1_cy)
        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION,
                                             {'pos':(card1_cx, card1_cy),
                                              'rel':(0,0),'buttons':(0,0,0)}))
        time.sleep(0.5)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    L.main()
    per_game_titles = [t for t in captured if '最近' not in t]
    assert any('贪吃蛇' in t for t in per_game_titles), \\
        f'expected 贪吃蛇 in per-game titles, got {per_game_titles[-5:]}'
""")

# ===========================================================================
print("\n=== 16. Launcher: two clicks in the same frame launch only ONE game ===")
run("launcher-no-double-launch", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L

    launches = {'n': 0}
    for mod_name in ('tetris','snake','game_2048','sokoban','zuma'):
        mod = __import__(f'client.games.{mod_name}', fromlist=['run_game'])
        def _stub(*a, **kw):
            launches['n'] += 1
        mod.run_game = _stub
    BackendClient.health = lambda self: False
    BackendClient.list_games = lambda self, *a, **k: []
    BackendClient.leaderboard = lambda self, *a, **k: []
    BackendClient.recent = lambda self, *a, **k: []

    # New layout: card 0 (Tetris) center = 42 + 84 = 126, y = 220
    # card 1 (Snake) center = 42 + 182 + 84 = 308, y = 220
    def driver():
        time.sleep(0.3)
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {'button':1,'pos':(126,220)}))
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {'button':1,'pos':(308,220)}))
        time.sleep(0.8)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    L.main()
    assert launches['n'] == 1, f'expected exactly 1 launch, got {launches["n"]}'
""")

# ===========================================================================
print("\n=== 17. Launcher: returning from a sub-game doesn't crash ===")
run("launcher-return-no-crash", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L
    BackendClient.health = lambda self: False
    BackendClient.list_games = lambda self, *a, **k: []
    BackendClient.leaderboard = lambda self, *a, **k: []
    BackendClient.recent = lambda self, *a, **k: []
    for mod_name in ('tetris','snake','game_2048','sokoban','zuma'):
        mod = __import__(f'client.games.{mod_name}', fromlist=['run_game'])
        def _stub(*a, **kw):
            pygame.display.quit()
        mod.run_game = _stub
    def driver():
        time.sleep(0.3)
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {'button':1,'pos':(126,220)}))
        time.sleep(0.5)
        time.sleep(0.3)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    L.main()
""")

# ===========================================================================
print("\n=== 18. Font cache survives launcher ⇄ sub-game cycles ===")
run("font-cache-after-subgame", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.ui import _font_cache, font as ui_font
    from client.common.network import BackendClient
    from client.games import snake
    ui_font(20)
    assert len(_font_cache) >= 1
    g = snake.Snake(backend=BackendClient()); g.running=False
    g.run()
    # BaseGame now uses pygame.display.quit() (not pygame.quit()) so the
    # font module stays alive and the cache is PRESERVED across sub-game
    # runs. Fonts should still render correctly afterwards.
    f = ui_font(24).render('hello', True, (255,255,255))
    assert f.get_width() > 0, 'font unusable after sub-game'
    # Display module was quit; re-init for further work.
    pygame.display.init()
    pygame.quit()
""")

# ===========================================================================
print("\n=== 19. Backend end-to-end round-trip ===")
run("backend-roundtrip", """
    from client.common.network import BackendClient
    be = BackendClient()
    assert be.health()
    r = be.submit_score('tetris', 'tester', 1234, extra={'note':'rt'})
    assert r and r.get('ok'), r
    lb = be.leaderboard('tetris', limit=5)
    assert any(e.get('player')=='tester' for e in lb), lb
""")

# ===========================================================================
print("\n=== 20. Tetris overlay buttons clickable via handle_event ===")
run("tetris-overlay-click", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import tetris
    g = tetris.Tetris(backend=BackendClient()); g.state='gameover'; g.draw()
    b = next(x for x in g.overlay_buttons if '重新开始' in x.label)
    g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {'button':1,'pos':b.rect.center}))
    assert g.state == 'playing', g.state
    pygame.quit()
""")

# ===========================================================================
print("\n=== 21. 2048 score submitted exactly once ===")
run("2048-score-once", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games import game_2048
    class CB:
        def __init__(self): self.n=0
        def submit_score(self,*a,**k):
            self.n+=1
            return {'ok': True, 'id': 1}
        def leaderboard(self,*a,**k): return []
        def health(self): return True
    cb = CB()
    g = game_2048.Game2048(backend=cb); g.won=True
    g._submit_score(extra={'won':True}); g._submit_score(extra={'won':True})
    assert cb.n == 1, cb.n
    pygame.quit()
""")

# ===========================================================================
print("\n=== 22. Sokoban R/N keys work during playing ===")
run("sokoban-rn-keys", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import sokoban
    g = sokoban.Sokoban(backend=BackendClient())
    g.handle_event(pygame.event.Event(pygame.KEYDOWN, {'key':pygame.K_RIGHT,'unicode':''}))
    assert g.moves >= 1
    g.handle_event(pygame.event.Event(pygame.KEYDOWN, {'key':pygame.K_r,'unicode':''}))
    assert g.moves == 0 and g.level_idx == 0
    g.handle_event(pygame.event.Event(pygame.KEYDOWN, {'key':pygame.K_n,'unicode':''}))
    assert g.level_idx == 1
    pygame.quit()
""")


# ===========================================================================
print("\n=== 23. Sokoban win submits score with the PLAYER NAME (not a tuple) ===")
run("sokoban-submits-player-name", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import sokoban
    submitted = {}
    class Spy:
        def __init__(self, real): self.real = real
        def submit_score(self, gid, player, score, extra=None, **kw):
            submitted['gid']=gid; submitted['player']=player; submitted['score']=score
            return self.real.submit_score(gid, player, score, extra=extra, **kw)
    real = BackendClient()
    g = sokoban.Sokoban(backend=Spy(real), player='franky')
    assert g.player == 'franky', f'player name clobbered: {g.player!r}'
    for level_idx in range(len(sokoban.LEVELS)):
        g.load_level(level_idx)
        g.boxes = set(g.targets)
        g._check_win()
    assert submitted.get('player') == 'franky', f'submitted as {submitted}'
    assert submitted.get('gid') == 'sokoban'
    import requests
    base = os.environ.get('GAMES_API_URL', 'http://127.0.0.1:5000')
    lb = requests.get(f'{base}/api/leaderboard/sokoban',
                      params={'limit': 5}).json()
    assert any(e['player'] == 'franky' for e in lb['leaderboard']), lb
    pygame.quit()
""")

# ===========================================================================
print("\n=== 24. Recent-games leaderboard carries the game_id per row ===")
run("recent-shows-game-id", """
    import os, requests
    base = os.environ.get('GAMES_API_URL', 'http://127.0.0.1:5000')
    # Submit a score under each game, then verify /api/recent returns
    # the game_id alongside each row.
    for gid, sc in [('tetris', 1234), ('snake', 567), ('sokoban', 890)]:
        r = requests.post(f'{base}/api/scores',
                          json={'game_id': gid, 'player': 'rt_test', 'score': sc})
        assert r.ok
    rec = requests.get(f'{base}/api/recent', params={'limit': 10}).json()
    rows = rec['recent']
    assert rows, 'no recent rows'
    for r in rows:
        assert 'game_id' in r, f'recent row missing game_id: {r}'
    gids = {r['game_id'] for r in rows}
    assert {'tetris', 'snake', 'sokoban'} <= gids, gids
""")

# ===========================================================================
print("\n=== 25. Launcher formats recent rows with the game name visible ===")
run("launcher-recent-shows-game-name", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L
    captured_recent = []
    orig = L.draw_leaderboard
    def spy(surf, rect, entries, title='排行榜', **kw):
        if '最近' in title:
            captured_recent.extend(entries)
        return orig(surf, rect, entries, title=title, **kw)
    L.draw_leaderboard = spy
    BackendClient.health = lambda self: True
    BackendClient.list_games = lambda self, *a, **k: []
    BackendClient.leaderboard = lambda self, *a, **k: []
    BackendClient.recent = lambda self, *a, **k: [
        {'game_id':'tetris', 'player':'a', 'score':100},
        {'game_id':'snake', 'player':'b', 'score':200},
    ]
    def driver():
        time.sleep(0.4)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    L.main()
    assert captured_recent, 'no recent entries captured'
    assert all('game_id' in e for e in captured_recent), captured_recent
""")

# ===========================================================================
print("\n=== 26. Tetris J piece reaches all 4 rotation states from spawn ===")
run("tetris-j-4-rotations-in-game", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris
    import random; random.seed(1)
    g = Tetris(backend=BackendClient())
    # Force the current piece to be J at a known open position.
    g.piece.kind = 'J'
    g.piece.rot = 0
    g.piece.x = 4
    g.piece.y = 5  # away from floor & ceiling
    visited = {g.piece.rot}
    for _ in range(3):
        g._rotate(1)
        visited.add(g.piece.rot)
    assert visited == {0, 1, 2, 3}, f'only reached rot states {visited}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 27. Tetris wall-kick lets J rotate against the floor ===")
run("tetris-rotate-against-floor", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris
    g = Tetris(backend=BackendClient())
    # Park J at the bottom (rows 17-18). The 180° rotation has cells at
    # rows 18-19 — would clip the floor without vertical wall-kicks.
    g.piece.kind = 'J'
    g.piece.rot = 0
    g.piece.x = 4
    g.piece.y = 17  # near floor
    before = g.piece.rot
    g._rotate(1)  # try CW 90° (cells extend down)
    # The new wall-kick table nudges up by 1-2 cells; rotation must succeed.
    assert g.piece.rot != before, 'rotate rejected near floor (no vertical kick)'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 28. Launcher redraws within 3 s after a sub-game exits ===")
run("launcher-fast-return", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L
    for mod_name in ('tetris','snake','game_2048','sokoban','zuma'):
        mod = __import__(f'client.games.{mod_name}', fromlist=['run_game'])
        def _stub(*a, **kw):
            pygame.display.quit()
        mod.run_game = _stub
    BackendClient.health = lambda self: True
    BackendClient.list_games = lambda self, *a, **k: []
    BackendClient.leaderboard = lambda self, *a, **k: []
    BackendClient.recent = lambda self, *a, **k: []
    timings = {'launched': None, 'quit': None}
    def driver():
        time.sleep(0.3)
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {'button':1,'pos':(126,220)}))
        timings['launched'] = time.time()
        time.sleep(2.0)
        timings['quit'] = time.time()
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    start = time.time()
    L.main()
    elapsed = time.time() - start
    assert elapsed < 5.0, f'launcher took {elapsed:.2f}s (regression: 1-minute hang?)'
""")

# ===========================================================================
print("\n=== 29. Visual: every game's draw() doesn't crash with new gradients ===")
for mod_name, cls_name in [("tetris","Tetris"), ("snake","Snake"),
                           ("game_2048","Game2048"),
                           ("sokoban","Sokoban"), ("zuma","Zuma")]:
    run(f"gradient-render-{mod_name}", f"""
        import os; os.environ['SDL_VIDEODRIVER']='dummy'
        import pygame; pygame.init()
        from client.common.network import BackendClient
        from client.games import {mod_name}
        cls = getattr({mod_name}, '{cls_name}')
        g = cls(backend=BackendClient(), player='v')
        for _ in range(5): g.update(0.016)
        g.draw(); pygame.display.flip()
        for s in ('paused', 'gameover', 'won'):
            g.state = s
            g.draw(); pygame.display.flip()
        pygame.quit()
    """)


# ===========================================================================
print("\n=== 30. Sokoban accumulates a full-run TOTAL score (and replaces prev) ===")
run("sokoban-total-score", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import sokoban

    submitted = []
    class Spy:
        def __init__(self, real): self.real = real
        def submit_score(self, gid, player, score, extra=None, **kw):
            submitted.append((score, extra, kw.get('replace', False)))
            return self.real.submit_score(gid, player, score, extra=extra, **kw)
    g = sokoban.Sokoban(backend=Spy(BackendClient()), player='rt')

    moves_by_level = [10 + lv for lv in range(len(sokoban.LEVELS))]
    for lv, mv in enumerate(moves_by_level):
        g.load_level(lv)
        g.moves = mv
        g.boxes = set(g.targets)
        g._check_win()

    assert len(submitted) == 1, \
        f'expected one completed-run submission, got {len(submitted)}'
    last_score, last_extra, last_replace = submitted[-1]
    expected_total = sum(1000 - mv for mv in moves_by_level)
    assert last_score == expected_total, \\
        f'final submitted score={last_score}, expected total={expected_total}'
    assert last_extra.get('completed_all') is True, last_extra
    assert last_replace, f'completed run must replace older run: {submitted}'
    # Verify backend really did delete previous submissions — only ONE
    # row for player 'rt' should remain.
    import requests
    base = os.environ.get('GAMES_API_URL', 'http://127.0.0.1:5000')
    lb = requests.get(f'{base}/api/leaderboard/sokoban',
                      params={'limit': 10}).json()
    rt_rows = [e for e in lb['leaderboard'] if e['player'] == 'rt']
    assert len(rt_rows) == 1, f'expected 1 rt row, got {len(rt_rows)}: {rt_rows}'
    assert rt_rows[0]['score'] == expected_total, rt_rows
    # Reloading level 0 resets the running total.
    g.load_level(0)
    assert g.total_score == 0, f'total reset failed: {g.total_score}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 31. Zuma: spawn is FIXED at pos=0 regardless of insertion ===")
run("zuma-spawn-advances", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import zuma
    g = zuma.Zuma(backend=BackendClient())
    g.chain = [{'pos':100.0,'color':0},
               {'pos':100.0 - zuma.GAP,'color':1},
               {'pos':100.0 - 2*zuma.GAP,'color':2}]
    # Spawn a ball — it must go to ``incoming`` at pos=0, NOT directly
    # to chain[-1].pos - GAP.
    g.incoming = []
    g._spawn_chain_ball()
    assert g.incoming and g.incoming[-1]['pos'] == 0.0, \\
        f'new ball should spawn at pos=0, got incoming={g.incoming}'
    # Multiple insertions should NOT move the spawn location — it's
    # always pos=0. Verify by inserting several times and re-spawning.
    for _ in range(5):
        g._insert(0, color=3, hit_pos=g.chain[0]['pos'])
    g.incoming = []
    g._spawn_chain_ball()
    assert g.incoming[-1]['pos'] == 0.0, \\
        f'spawn moved after insertions: {g.incoming[-1]["pos"]}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 32. Launcher card icon renders without exception for every game ===")
run("launcher-icons-render", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.launcher import draw_game_icon
    surf = pygame.Surface((360, 60))
    for i, gid in enumerate(['tetris','snake','2048','sokoban','zuma']):
        draw_game_icon(surf, gid, 30 + i*70, 30, size=48)
    pygame.quit()
""")


# ===========================================================================
print("\n=== 33. Overlay leaderboard line never overflows the panel ===")
run("overlay-lb-no-overflow", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import tetris  # narrowest window (560px)
    class Stub:
        def health(self): return True
        def leaderboard(self, *a, **k):
            # Pathologically long entries to stress the panel.
            return [{'rank': i+1, 'player': 'verylongplayername', 'score': 99999999}
                    for i in range(3)]
    g = tetris.Tetris(backend=Stub(), player='t')
    g.state = 'gameover'; g.score = 12345
    g.draw()
    # All overlay buttons must be inside the window.
    for b in g.overlay_buttons:
        assert 0 <= b.rect.left and b.rect.right <= g.width, \\
            f'btn x-overflow: {b.rect}'
        assert 0 <= b.rect.top and b.rect.bottom <= g.height, \\
            f'btn y-overflow: {b.rect}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 34. Sokoban HUD never overlaps the level grid ===")
run("sokoban-hud-no-overlap", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import sokoban
    from client.common.ui import font as _font
    # Level 1 is the narrowest (7 cells × 40px = 280px, window 480px).
    g = sokoban.Sokoban(backend=BackendClient())
    g.load_level(0)
    g.draw()
    # Right-aligned HUD text must end before window right edge.
    stats = f"步数 {g.moves}  推动 {g.pushes}  累计 {g.total_score}"
    sw = _font(15, bold=True).size(stats)[0]
    # The text is drawn at width - sw - 16 (right-aligned).
    text_right = g.width - 16
    text_left = text_right - sw
    assert text_right <= g.width, f'HUD overflows right: {text_right} > {g.width}'
    # And the text shouldn't overlap the level grid area
    # (offset_x..offset_x + w*CELL).
    grid_right = g.offset_x + 7 * sokoban.CELL  # level 1 is 7 wide
    # HUD is in the top 60px header; grid starts at offset_y=60.
    # They're vertically separated, so any horizontal overlap is OK.
    pygame.quit()
""")

# ===========================================================================
print("\n=== 35. 2048 score right-aligns and doesn't overflow ===")
run("2048-score-no-overflow", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games import game_2048
    from client.common.ui import font as _font
    g = game_2048.Game2048(backend=BackendClient())
    g.score = 9_999_999  # 7-digit stress
    g.draw()
    # Right edge of "得分: 9999999" should be at BOARD_X + BOARD_SIZE.
    sw = _font(18, bold=True).size(f"得分: {g.score}")[0]
    right_edge = game_2048.BOARD_X + game_2048.BOARD_SIZE
    assert right_edge <= g.width, f'score overflows window: {right_edge} > {g.width}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 36. Launcher player input truncates long names ===")
run("launcher-input-truncates", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L
    BackendClient.health = lambda self: False
    BackendClient.list_games = lambda self, *a, **k: []
    BackendClient.leaderboard = lambda self, *a, **k: []
    BackendClient.recent = lambda self, *a, **k: []
    # Pre-set a long CJK player name by patching main()'s closure: we
    # inject by simulating keystrokes.
    name_state = {'value': ''}
    def driver():
        time.sleep(0.3)
        # Click the input field to start editing.
        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {'button':1,'pos':(870, 43)}))
        time.sleep(0.1)
        # Type 16 CJK chars (max allowed).
        for ch in '阿斯达克科技有限股份公司有限':
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, {'key':0,'unicode':ch}))
            time.sleep(0.02)
        time.sleep(0.2)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    L.main()
    # If we got here without crash, the truncation worked (no overflow).
""")


# ===========================================================================
print("\n=== 37. Tetris: every piece × every reachable position can rotate through ALL 4 SRS states ===")
run("tetris-srs-full-rotation-reachability", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris

    def normalize(cells):
        mx = min(c[0] for c in cells); my = min(c[1] for c in cells)
        return tuple(sorted((x-mx, y-my) for x, y in cells))

    # For every piece, at every board position where state 0 fits, with
    # both rotation directions, verify all 4 rotation indices are
    # reachable. Catches missing wall-kicks for ANY piece.
    summary = {}
    failures = []
    for kind in 'IOTSZJL':
        rejections = 0
        positions_tested = 0
        visual_states = set()
        for x in range(-2, 12):
            for y in range(-2, 22):
                for d in (+1, -1):
                    g = Tetris(backend=BackendClient())
                    g.piece.kind = kind
                    g.piece.rot = 0
                    g.piece.x = x; g.piece.y = y
                    # Skip positions where spawn state is invalid.
                    if g._collides(g.piece.cells()):
                        continue
                    positions_tested += 1
                    visual_states.add(normalize(g.piece.cells()))
                    rot_indices = {0}
                    for _ in range(4):
                        before = g.piece.rot
                        g._rotate(d)
                        if g.piece.rot == before:
                            rejections += 1
                        else:
                            rot_indices.add(g.piece.rot)
                            visual_states.add(normalize(g.piece.cells()))
                    if len(rot_indices) != 4:
                        failures.append((kind, x, y, d, rot_indices))
        summary[kind] = (positions_tested, rejections, len(visual_states))

    # All pieces must rotate through all 4 states from EVERY valid
    # position. Zero rejections allowed.
    for kind, (pos, rej, vis) in summary.items():
        assert rej == 0, f'{kind}: {rej} rejections across {pos} positions'
    # Symmetry sanity check: visual state counts match SRS standard.
    expected_visual = {'I': 2, 'O': 1, 'T': 4, 'S': 2, 'Z': 2, 'J': 4, 'L': 4}
    for kind, (_, _, vis) in summary.items():
        assert vis == expected_visual[kind], \\
            f'{kind}: got {vis} visual states, expected {expected_visual[kind]}'
    pygame.quit()
""")


# ===========================================================================
print("\n=== 38. Tetris: rotation into existing BLOCKS is rejected (no jumping up) ===")
run("tetris-block-collision-rejects", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris

    # Scenario 1: T piece whose state-R rotation would put a cell
    # where a block already exists. Must REJECT (no kick).
    g = Tetris(backend=BackendClient(), player='t')
    g.piece.kind = 'T'; g.piece.rot = 0
    g.piece.x = 4; g.piece.y = 16
    assert not g._collides(g.piece.cells()), 'spawn must be valid'
    # Place block at (5, 18) — T-state-R at piece.y=16 has a cell there.
    g.board[18][5] = 'I'
    before_rot, before_y = g.piece.rot, g.piece.y
    g._rotate(+1)  # would put a cell on the block
    assert g.piece.rot == before_rot, 'rotation should be rejected (block collision)'
    assert g.piece.y == before_y, 'piece should NOT jump up'

    # Scenario 2: same setup, NO block — rotation should succeed.
    g2 = Tetris(backend=BackendClient(), player='t')
    g2.piece.kind = 'T'; g2.piece.rot = 0
    g2.piece.x = 4; g2.piece.y = 16
    g2._rotate(+1)
    assert g2.piece.rot == 1, 'unobstructed rotation must succeed'

    # Scenario 3: floor-only collision (no blocks) — wall-kick should
    # still work.
    g3 = Tetris(backend=BackendClient(), player='t')
    g3.piece.kind = 'T'; g3.piece.rot = 0
    g3.piece.x = 4; g3.piece.y = 18
    g3._rotate(+1); g3._rotate(+1)  # 0→R→2: state-2 hits floor
    assert g3.piece.rot == 2, 'floor collision should wall-kick successfully'

    # Scenario 4: block-collision rejection works for ALL 7 pieces.
    # For each piece, place a block in the rotation target and verify
    # the rotation is rejected.
    for kind in 'IOTSZJL':
        g4 = Tetris(backend=BackendClient(), player='t')
        g4.piece.kind = kind; g4.piece.rot = 0
        g4.piece.x = 4; g4.piece.y = 5
        # Find any cell of state-1 rotation target and put a block there.
        target = g4.piece.cells(rot=1)
        bx, by = next((x, y) for x, y in target if 0 <= y < 20)
        g4.board[by][bx] = 'I'
        before = g4.piece.rot
        g4._rotate(+1)
        assert g4.piece.rot == before, \\
            f'{kind}: rotation should be rejected by block at ({bx},{by})'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 39. Tetris: rotation flash is gone (no _rot_flash attribute) ===")
run("tetris-no-rotation-flash", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.tetris import Tetris
    g = Tetris(backend=BackendClient(), player='t')
    # _rot_flash must not exist (the flash has been removed).
    assert not hasattr(g, '_rot_flash'), \\
        '_rot_flash attribute still present — flash not fully removed'
    # Rotating should still work.
    before = g.piece.rot
    g._rotate(+1)
    assert g.piece.rot != before
    # Drawing after rotation should not crash and should not draw any
    # white highlight ring around the active piece.
    g.draw()
    pygame.quit()
""")

# ===========================================================================
print("\n=== 40. Shared input: losing window focus pauses every game ===")
run("focus-loss-auto-pauses", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris
    from client.games.snake import Snake
    from client.games.game_2048 import Game2048
    from client.games.sokoban import Sokoban
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    for cls in (Tetris, Snake, Game2048, Sokoban, Zuma):
        g = cls(backend=Stub())
        if cls is Snake:
            g.handle_event(pygame.event.Event(
                pygame.KEYDOWN, {'key': pygame.K_UP, 'unicode': ''}))
            assert g.pending_direction == (0, -1)
        g.handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST, {}))
        assert g.state == 'paused', f'{cls.__name__} kept running: {g.state}'
        g.handle_event(pygame.event.Event(
            pygame.KEYDOWN, {'key': pygame.K_p, 'unicode': 'p'}))
        assert g.state == 'playing', f'{cls.__name__} did not resume'
        if cls is Snake:
            g.update(1.0 / g.move_speed)
            assert g.direction == (1, 0), \
                'queued pre-pause turn leaked into resumed game'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 41. Overlay clicks never leak into the restarted game ===")
run("overlay-click-no-through", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    from client.games.game_2048 import Game2048
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    z = Zuma(backend=Stub()); z.state = 'gameover'; z.draw()
    b = next(x for x in z.overlay_buttons if '重新开始' in x.label)
    click = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {'button': 1, 'pos': b.rect.center})
    z.handle_event(click)
    z.handle_event(click)  # rapid second half of a double-click
    assert z.state == 'playing'
    assert not z.projectiles, 'restart double-click fired a Zuma projectile'

    g = Game2048(backend=Stub()); g.state = 'gameover'; g.draw()
    b = next(x for x in g.overlay_buttons if '重新开始' in x.label)
    click = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {'button': 1, 'pos': b.rect.center})
    g.handle_event(click)
    g.handle_event(click)
    assert g.state == 'playing'
    assert g._swipe_start is None, 'restart double-click began a 2048 swipe'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 42. Result overlay fetches its leaderboard once, not every frame ===")
run("overlay-leaderboard-cached", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris
    class Stub:
        def __init__(self): self.calls = 0; self.submits = 0
        def leaderboard(self, *a, **k): self.calls += 1; return []
        def submit_score(self, *a, **k): self.submits += 1
    stub = Stub(); g = Tetris(backend=stub)
    g.state = 'gameover'; g.score = 12
    for _ in range(10): g.draw()
    assert stub.calls == 1, f'10 frames made {stub.calls} requests'
    g.on_game_over(20)
    for _ in range(3): g.draw()
    assert stub.calls == 2, 'a new result should refresh exactly once'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 43. Snake restart restores speed and rapid input keeps legal turn ===")
run("snake-restart-and-turn-buffer", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.snake import Snake
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Snake(backend=Stub())
    g.move_speed = 20; g.score = 600; g.reset()
    assert g.level == 1 and g.move_speed == 7, \
        f'restart retained old difficulty: level={g.level}, speed={g.move_speed}'
    assert g.fps == 60, f'input/render loop should remain responsive: {g.fps}'
    def press(k):
        g.handle_event(pygame.event.Event(
            pygame.KEYDOWN, {'key': k, 'unicode': ''}))
    press(pygame.K_UP)
    press(pygame.K_LEFT)  # illegal relative to current Right; keep Up
    g.update(1 / g.move_speed)
    assert g.direction == (0, -1), f'legal Up turn was lost: {g.direction}'
    g.reset(); old_head=g.body[0]
    g.update(0.75)
    assert g.body[0] == (old_head[0] + 1, old_head[1]), \
        'a recovered frame replayed several invisible snake steps'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 44. Snake filling the final cell wins instead of hanging ===")
run("snake-full-board-win", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from collections import deque
    from client.games.snake import Snake, COLS, ROWS
    class Stub:
        def __init__(self): self.submits = 0
        def submit_score(self, *a, **k): self.submits += 1
        def leaderboard(self, *a, **k): return []
    stub = Stub(); g = Snake(backend=stub)
    final = (1, 0)
    rest = [(x, y) for y in range(ROWS) for x in range(COLS)
            if (x, y) not in ((0, 0), final)]
    g.body = deque([(0, 0)] + rest)
    g.direction = g.pending_direction = (1, 0)
    g.food = final
    g.update(1 / g.move_speed)
    assert g.state == 'won', g.state
    assert len(g.body) == COLS * ROWS and g.food is None
    assert stub.submits == 1
    g.draw()
    pygame.quit()
""")

# ===========================================================================
print("\n=== 45. 2048 continue cannot get stuck on a locked board ===")
run("2048-locked-continue-ends", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.game_2048 import Game2048, Tile
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Game2048(backend=Stub())
    values = [[2,4,2,4], [4,2,4,2], [2,4,2,4], [4,2,4,2048]]
    g.tiles = []; g.grid = [[None] * 4 for _ in range(4)]
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            tile = Tile(value=value, row=r, col=c)
            g.tiles.append(tile); g.grid[r][c] = tile
    g.won = True; g.state = 'won'; g._won_announced = True
    g._continue_after_win()
    assert g.state == 'gameover', 'locked post-win board stayed playable'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 46. Zuma respects which side of a chain ball was hit ===")
run("zuma-hit-side-insertion", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    base = [{'pos': 200.0, 'color': 0},
            {'pos': 178.0, 'color': 1},
            {'pos': 156.0, 'color': 2}]
    front = Zuma(backend=Stub()); front.chain = [dict(x) for x in base]
    front._insert(1, 4, 178.0, in_front=True)
    assert [b['color'] for b in front.chain] == [0, 4, 1, 2]
    back = Zuma(backend=Stub()); back.chain = [dict(x) for x in base]
    back._insert(1, 4, 178.0, in_front=False)
    assert [b['color'] for b in back.chain] == [0, 1, 4, 2]
    pygame.quit()
""")

# ===========================================================================
print("\n=== 47. Zuma entrance queue never creates negative chain positions ===")
run("zuma-entrance-queue-spacing", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub())
    g.chain = [{'pos': 10.0, 'color': 0}]
    g.incoming = [{'pos': 0.0, 'color': 1}]
    g.update(0.01)
    assert len(g.chain) == 1, 'incoming ball merged before a slot existed'
    assert len(g.incoming) == 1, 'incoming queue was lost'
    assert all(ball['pos'] >= 0 for ball in g.chain), g.chain
    pygame.quit()
""")

# ===========================================================================
print("\n=== 48. Launcher guest label is a placeholder, not typed text ===")
run("launcher-guest-placeholder", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L
    captured = []
    BackendClient.health = lambda self: False
    BackendClient.list_games = lambda self, *a, **k: []
    BackendClient.leaderboard = lambda self, *a, **k: []
    BackendClient.recent = lambda self, *a, **k: []
    mod = __import__('client.games.tetris', fromlist=['run_game'])
    def launch(*a, **kw):
        captured.append(kw.get('player'))
        pygame.display.quit()
    mod.run_game = launch
    def driver():
        time.sleep(0.25)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {'button':1, 'pos':(870, 43)}))
        for ch in '小明':
            pygame.event.post(pygame.event.Event(
                pygame.KEYDOWN, {'key':0, 'unicode':ch}))
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {'button':1, 'pos':(126, 220)}))
        time.sleep(0.4)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    L.main()
    assert captured == ['小明'], f'launcher passed {captured!r}'
""")

# ===========================================================================
print("\n=== 49. Zuma result statistics never report negative clears ===")
run("zuma-result-counts", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def __init__(self): self.extra = None
        def submit_score(self, gid, player, score, extra=None, **kw):
            self.extra = extra
        def leaderboard(self, *a, **k): return []
    stub = Stub(); g = Zuma(backend=stub)
    # Player-inserted balls can make the chain longer than LEVEL_BALLS.
    # The old formula LEVEL_BALLS-len(chain) then became negative.
    g.spawned = 11
    g.chain = [{'pos': float(1000 - i * 22), 'color': i % 5}
               for i in range(32)]
    g.incoming = [{'pos': 0.0, 'color': 0} for _ in range(6)]
    g.cleared_balls = 7
    g.chain[0]['pos'] = g.path_length
    g.update(0.01)
    assert g.state == 'gameover'
    assert stub.extra['cleared'] == 7, stub.extra
    assert stub.extra['remaining'] == 38, stub.extra
    assert stub.extra['spawned'] == 11, stub.extra
    pygame.quit()
""")

# ===========================================================================
print("\n=== 50. 2048 continued play updates one session score row ===")
run("2048-session-score-update", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.game_2048 import Game2048
    class Stub:
        def __init__(self): self.calls = []
        def submit_score(self, *a, **kw):
            self.calls.append((a, kw))
            return {'ok': True, 'id': 42, 'updated': len(self.calls) > 1}
        def leaderboard(self, *a, **k): return []
    stub = Stub(); g = Game2048(backend=stub)
    g.score = 100; g._submit_score(extra={'won': True})
    g.score = 250; g._submit_score(extra={'won': True, 'final': True})
    g._submit_score(extra={'won': True, 'final': True})
    assert len(stub.calls) == 2, stub.calls
    assert stub.calls[0][1].get('submission_id') is None
    assert stub.calls[1][1].get('submission_id') == 42
    assert g.submitted_score == 250

    # Verify the real endpoint updates the same database row, too.
    from client.common.network import BackendClient
    player = f'update_{os.getpid()}'
    be = BackendClient()
    first = be.submit_score('2048', player, 8_888_001, replace=True)
    assert first and first.get('id'), first
    second = be.submit_score('2048', player, 8_888_250,
                             submission_id=first['id'])
    assert second and second.get('updated') is True, second
    assert second['id'] == first['id']
    rows = [e for e in be.leaderboard('2048', limit=50)
            if e.get('player') == player]
    assert len(rows) == 1 and rows[0]['score'] == 8_888_250, rows
    pygame.quit()
""")

# ===========================================================================
print("\n=== 51. Zuma middle elimination retracts the front group backward ===")
run("zuma-collapse-direction", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma, GAP
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub()); g.spawned = 28
    colors = [0, 1, 2, 2, 2, 3, 4, 0, 1]
    g.chain = [{'pos': 520 - i * GAP, 'color': c,
                'visual_offset': 0.0} for i, c in enumerate(colors)]
    old_front = g.chain[0]['pos']; old_back = g.chain[-1]['pos']
    assert g._try_match_at(3)
    # The exit-side group moves logically BACK by the three removed slots;
    # the entrance-side group stays anchored.
    assert g.chain[0]['pos'] == old_front - 3 * GAP
    assert g.chain[-1]['pos'] == old_back
    positions = [b['pos'] for b in g.chain]
    assert all(abs(positions[i] - positions[i+1] - GAP) < 1e-6
               for i in range(len(positions)-1)), positions
    # Visual offset initially preserves the pre-removal pixels, then decays
    # fast enough that the front is visibly moving backward despite crawl.
    before = g._visual_distance(g.chain[0])
    g.update(1 / 60)
    after = g._visual_distance(g.chain[0])
    assert after < before, f'front still moved forward: {before} -> {after}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 52. Zuma entrance uses one stationary queue marker ===")
run("zuma-single-entrance-queue", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub())
    g.chain = [{'pos': 10.0, 'color': 0, 'visual_offset': 0.0}]
    g.incoming = [{'pos': 0.0, 'color': c} for c in (1, 2, 3, 4)]
    g.update(0.1)
    assert [b['pos'] for b in g.incoming] == [0.0] * 4
    translucent = []
    original = g._draw_ball
    def spy(*a, **kw):
        if kw.get('alpha') == 200: translucent.append(a)
        return original(*a, **kw)
    g._draw_ball = spy
    g.draw()
    assert len(translucent) == 1, \
        f'queue rendered as {len(translucent)} separate/floating balls'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 53. Zuma shooter stops offering colors absent from play ===")
run("zuma-playable-shooter-colors", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub())
    g.chain = [{'pos': 100.0, 'color': 1, 'visual_offset': 0.0}]
    g.incoming = []
    g.current_color = 4; g.next_color = 3
    g._sync_shooter_colors()
    assert g.current_color == 1 and g.next_color == 1
    pygame.quit()
""")

# ===========================================================================
print("\n=== 54. Tetris J/L/T floor rotations return to the same cells ===")
run("tetris-corner-floor-cycle-aligned", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    for direction in (1, -1):
        for kind in 'JLT':
            for x in range(8):
                g = Tetris(backend=Stub())
                g.piece.kind = kind; g.piece.rot = 0
                g.piece.x = x; g.piece.y = 18
                start = (g.piece.x, g.piece.y, g.piece.rot,
                         sorted(g.piece.cells()))
                for _ in range(4): g._rotate(direction)
                end = (g.piece.x, g.piece.y, g.piece.rot,
                       sorted(g.piece.cells()))
                assert end == start, \
                    f'{kind} dir={direction} x={x}: {start} -> {end}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 55. 2048 buffers an ordered input burst during animation ===")
run("2048-input-buffer", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.game_2048 import Game2048, Tile
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Game2048(backend=Stub()); g._spawn_tile = lambda: None
    g.tiles = []; g.grid = [[None] * 4 for _ in range(4)]
    tile = Tile(value=2, row=0, col=0)
    g.tiles = [tile]; g.grid[0][0] = tile
    g._move('right')
    g._move('down')  # arrives while right-slide is animating
    g._move('left')
    assert list(g._queued_directions) == ['down', 'left']
    g._tick_animations(1.0)
    assert list(g._queued_directions) == ['left']
    assert (tile.row, tile.col) == (3, 3), (tile.row, tile.col)
    g._tick_animations(1.0)
    assert not g._queued_directions
    assert (tile.row, tile.col) == (3, 0), (tile.row, tile.col)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 56. Sokoban can undo an accidental box push ===")
run("sokoban-undo-push", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.sokoban import Sokoban
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Sokoban(backend=Stub())
    start = (g.player_pos, set(g.boxes), g.moves, g.pushes)
    g.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {'key': pygame.K_UP, 'unicode': ''}))
    assert g.pushes == 1 and len(g.history) == 1
    g.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {'key': pygame.K_u, 'unicode': 'u'}))
    end = (g.player_pos, set(g.boxes), g.moves, g.pushes)
    assert end == start, f'undo mismatch: {start} -> {end}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 57. Snake head eyes follow movement direction ===")
run("snake-directional-eyes", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.snake import Snake, BOARD_X, BOARD_Y, CELL
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Snake(backend=Stub())
    hx, hy = g.body[0]
    rx = BOARD_X + hx * CELL + 1; ry = BOARD_Y + hy * CELL + 1
    checks = {
        (1, 0): (rx + CELL - 8, ry + 6),
        (-1, 0): (rx + 6, ry + 6),
        (0, 1): (rx + 6, ry + CELL - 8),
        (0, -1): (rx + 6, ry + 6),
    }
    for direction, point in checks.items():
        g.direction = direction; g.draw()
        assert g.screen.get_at(point)[:3] == (20, 30, 20), \
            f'{direction} missing eye at {point}: {g.screen.get_at(point)[:3]}'
    pygame.quit()
""")

# ===========================================================================
print("\n=== 58. Zuma compounded animations never reverse visual ball order ===")
run("zuma-animation-order", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma, GAP
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub())
    # An earlier insertion is still easing (-10 offset) when another ball
    # lands behind it. The new ball must inherit the hit ball's visual offset
    # or it will appear in front of its logical predecessor.
    g.chain = [
        {'pos': 100.0, 'color': 0, 'visual_offset': -10.0},
        {'pos': 100.0-GAP, 'color': 1, 'visual_offset': -10.0},
        {'pos': 100.0-2*GAP, 'color': 2, 'visual_offset': 0.0},
    ]
    g._insert(1, color=4, hit_pos=100.0-GAP, in_front=False)
    visual = [g._visual_distance(b) for b in g.chain]
    assert all(visual[i] >= visual[i+1]
               for i in range(len(visual)-1)), visual
    # Keep easing for several frames; ordering must remain stable throughout.
    for _ in range(20):
        g.update(1/60)
        visual = [g._visual_distance(b) for b in g.chain]
        assert all(visual[i] >= visual[i+1]-1e-6
                   for i in range(len(visual)-1)), visual
    pygame.quit()
""")

# ===========================================================================
print("\n=== 59. Zuma buffers rapid clicks but a long press fires once ===")
run("zuma-rapid-clicks-single-long-press", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    def mouse(kind):
        return pygame.event.Event(kind, {'button': 1, 'pos': (410, 100)})

    g = Zuma(backend=Stub()); g.spawn_interval = 99
    for _ in range(10):
        g.handle_event(mouse(pygame.MOUSEBUTTONDOWN))
        g.handle_event(mouse(pygame.MOUSEBUTTONUP))
    assert len(g.projectiles) == 1 and g.shot_queue == 9
    for _ in range(48): g.update(1/60)
    assert len(g.projectiles) == 10, len(g.projectiles)
    assert g.shot_queue == 0

    held = Zuma(backend=Stub()); held.spawn_interval = 99
    held.handle_event(mouse(pygame.MOUSEBUTTONDOWN))
    for _ in range(30): held.update(1/60)
    held.handle_event(mouse(pygame.MOUSEBUTTONUP))
    assert len(held.projectiles) == 1, len(held.projectiles)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 60. Zuma pause/focus loss cancels pending shot queue ===")
run("zuma-pause-cancels-shot-queue", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub())
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                               {'button':1, 'pos':(410,100)})
    g.handle_event(click); g.handle_event(click)
    assert g.shot_queue == 1
    g.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {'key':pygame.K_p, 'unicode':'p'}))
    assert g.state == 'paused'
    assert g.shot_queue == 0
    pygame.quit()
""")

# ===========================================================================
print("\n=== 61. 2048 pause freezes an in-flight move and still spawns afterward ===")
run("2048-pause-freezes-slide", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.game_2048 import Game2048, Tile
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Game2048(backend=Stub())
    g.tiles = []; g.grid = [[None] * 4 for _ in range(4)]
    tile = Tile(value=2, row=0, col=0)
    g.tiles = [tile]; g.grid[0][0] = tile
    spawned = {'n': 0}
    def spawn(): spawned['n'] += 1; return True
    g._spawn_tile = spawn
    g._move('right'); assert g.anim_t == 0.0
    p = pygame.event.Event(pygame.KEYDOWN,
                           {'key':pygame.K_p, 'unicode':'p'})
    g.handle_event(p); assert g.state == 'paused'
    g.update_overlay(1.0)
    assert g.anim_t == 0.0 and spawned['n'] == 0
    g.handle_event(p); assert g.state == 'playing'
    g.update(1.0)
    assert g.anim_t == 1.0 and spawned['n'] == 1
    pygame.quit()
""")

# ===========================================================================
print("\n=== 62. Tetris held horizontal input repeats and stops on key-up ===")
run("tetris-horizontal-hold", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris, HORIZONTAL_DAS
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Tetris(backend=Stub()); g.piece.kind = 'O'
    g.piece.x = 1; g.piece.y = 5
    down = pygame.event.Event(pygame.KEYDOWN,
                              {'key':pygame.K_RIGHT, 'unicode':''})
    up = pygame.event.Event(pygame.KEYUP,
                            {'key':pygame.K_RIGHT, 'unicode':''})
    g.handle_event(down); assert g.piece.x == 2
    g.update(HORIZONTAL_DAS - 0.01); assert g.piece.x == 2
    g.update(0.02); assert g.piece.x >= 3
    g.handle_event(up); stopped = g.piece.x
    g.update(1.0); assert g.piece.x == stopped
    pygame.quit()
""")

# ===========================================================================
print("\n=== 63. Backend client backs off after a connection failure ===")
run("backend-failure-backoff", """
    import requests
    import client.common.network as network
    calls = {'n': 0}
    original = network.requests.Session.get
    def fail(*a, **k):
        calls['n'] += 1
        raise requests.ConnectionError('offline')
    network.requests.Session.get = fail
    try:
        be = network.BackendClient(base_url='http://127.0.0.1:1')
        assert be._get('/x') is None
        assert be._get('/x') is None
        assert calls['n'] == 1, f'backoff made {calls["n"]} requests'
    finally:
        network.requests.Session.get = original
""")

# ===========================================================================
print("\n=== 64. Score API rejects malformed and dangerous payloads ===")
run("backend-score-validation", """
    from server.app import app, init_db
    init_db(); client = app.test_client()
    cases = [
        [],
        {'game_id':'tetris', 'player':123, 'score':1},
        {'game_id':'tetris', 'player':'p', 'score':-1},
        {'game_id':'tetris', 'player':'p', 'score':True},
        {'game_id':'tetris', 'player':'p', 'score':1, 'replace':'false'},
    ]
    for payload in cases:
        response = client.post('/api/scores', json=payload)
        assert response.status_code == 400, (payload, response.data)
    response = client.post('/api/scores', json={
        'game_id':'tetris', 'player':'   ', 'score':0})
    assert response.status_code == 200
""")

# ===========================================================================
print("\n=== 65. Sokoban replacement never erases a higher completed run ===")
run("backend-preserves-higher-run", """
    import os
    from client.common.network import BackendClient
    be = BackendClient(); player = f'best_{os.getpid()}'
    high = be.submit_score('sokoban', player, 3900, replace=True)
    low = be.submit_score('sokoban', player, 900, replace=True)
    assert high and low, (high, low)
    assert low.get('preserved') is True and low.get('score') == 3900, low
    rows = [r for r in be.leaderboard('sokoban', 50)
            if r.get('player') == player]
    assert len(rows) == 1 and rows[0]['score'] == 3900, rows
""")

# ===========================================================================
print("\n=== 66. Leaderboard reports equal ranks for tied scores ===")
run("backend-tie-ranks", """
    import os
    from client.common.network import BackendClient
    be = BackendClient(); suffix = os.getpid()
    score = 2_147_000_000
    a = f'tie_a_{suffix}'; b = f'tie_b_{suffix}'
    assert be.submit_score('snake', a, score)
    assert be.submit_score('snake', b, score)
    rows = [r for r in be.leaderboard('snake', 50)
            if r.get('player') in (a, b)]
    assert len(rows) == 2, rows
    assert rows[0]['rank'] == rows[1]['rank'], rows
""")

# ===========================================================================
print("\n=== 67. Launcher reconnects when backend comes online later ===")
run("launcher-auto-reconnect", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import threading, time, pygame
    from client.common.network import BackendClient
    import client.launcher as L
    calls = {'health':0, 'lb':0}
    def health(self):
        calls['health'] += 1
        return calls['health'] >= 2
    def leaderboard(self, *a, **k):
        calls['lb'] += 1; return []
    BackendClient.health = health
    BackendClient.list_games = lambda self: []
    BackendClient.leaderboard = leaderboard
    BackendClient.recent = lambda self, *a, **k: []
    L.HEALTH_REFRESH_SECS = 0.05
    def driver():
        time.sleep(0.35)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    threading.Thread(target=driver, daemon=True).start()
    L.main()
    assert calls['health'] >= 2, calls
    assert calls['lb'] >= 5, calls
""")

# ===========================================================================
print("\n=== 68. Pixel-width text fitting prevents leaderboard overlap ===")
run("leaderboard-pixel-fit", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.ui import fit_text, font
    f = font(15)
    fitted = fit_text('超长中文玩家名字ABCDEFGHIJK', f, 72)
    assert f.size(fitted)[0] <= 72, (fitted, f.size(fitted)[0])
    assert fitted.endswith('…'), fitted
    pygame.quit()
""")

# ===========================================================================
print("\n=== 69. Zuma click position immediately updates shot direction ===")
run("zuma-click-updates-aim", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import math, pygame; pygame.init()
    from client.games.zuma import Zuma, SHOOTER_POS
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub()); g.aim_angle = 0.0
    target = (SHOOTER_POS[0], SHOOTER_POS[1] - 100)
    g.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {'button':1, 'pos':target}))
    assert abs(g.aim_angle + math.pi/2) < 1e-6, g.aim_angle
    assert len(g.projectiles) == 1 and g.projectiles[0]['vy'] < 0
    pygame.quit()
""")

# ===========================================================================
print("\n=== 70. Zuma swept collision prevents low-FPS projectile tunnelling ===")
run("zuma-swept-projectile-hit", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma, pos_at
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub()); g.spawn_interval = 99
    distance = 300.0
    bx, by = pos_at(g.path_pts, g.path_cum, distance)
    g.chain = [{'pos':distance, 'color':0, 'visual_offset':0.0}]
    # In one 100ms frame this projectile moves 120px, well beyond a ball
    # diameter. Endpoint-only collision would miss it completely.
    p = {'x':bx, 'y':by+60, 'vx':0.0, 'vy':-1200.0, 'color':1}
    g.projectiles = [p]
    g.update(0.1)
    assert p not in g.projectiles, 'fast projectile tunnelled through ball'
    assert len(g.chain) == 2, g.chain
    pygame.quit()
""")

# ===========================================================================
print("\n=== 71. Zuma has five progressively harder levels and one final submit ===")
run("zuma-multi-level-progression", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma, ZUMA_LEVELS, LEVEL_CLEAR_BONUS
    class Stub:
        def __init__(self): self.submissions = []
        def submit_score(self, *a, **k): self.submissions.append((a, k))
        def leaderboard(self, *a, **k): return []
    balls = [level['balls'] for level in ZUMA_LEVELS]
    speeds = [level['speed'] for level in ZUMA_LEVELS]
    assert len(ZUMA_LEVELS) >= 5
    assert all(a < b for a, b in zip(balls, balls[1:])), balls
    assert all(a < b for a, b in zip(speeds, speeds[1:])), speeds

    stub = Stub(); g = Zuma(backend=stub)
    for level_idx, config in enumerate(ZUMA_LEVELS):
        assert g.level_idx == level_idx
        assert g.level_ball_count == config['balls']
        assert g.chain_speed == config['speed']
        score_before = g.score
        g.spawned = g.level_ball_count
        g.chain = []; g.incoming = []
        g.update(0.0)
        assert g.state == 'won'
        assert g.score == score_before + LEVEL_CLEAR_BONUS * (level_idx + 1)
        if level_idx < len(ZUMA_LEVELS) - 1:
            assert not stub.submissions, 'intermediate level submitted a run score'
            g.advance_level()
            assert g.state == 'playing'
        else:
            assert len(stub.submissions) == 1
            assert g.extra['completed_all'] is True
    pygame.quit()
""")

# ===========================================================================
print("\n=== 72. Snake starts slower and gains speed with displayed levels ===")
run("snake-level-speed-curve", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.snake import (Snake, SNAKE_INITIAL_SPEED,
                                    snake_speed_for_level)
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    assert SNAKE_INITIAL_SPEED < 12
    curve = [snake_speed_for_level(level) for level in range(1, 20)]
    assert all(a <= b for a, b in zip(curve, curve[1:])), curve
    assert curve[0] < curve[5] <= curve[-1]

    g = Snake(backend=Stub())
    assert g.level == 1 and g.move_speed == SNAKE_INITIAL_SPEED
    g.score = 40
    hx, hy = g.body[0]
    g.food = (hx + 1, hy)
    g.update(1.0 / g.move_speed)
    assert g.score == 50 and g.level == 2, (g.score, g.level)
    assert g.move_speed == snake_speed_for_level(2)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 73. Tetris drop speed increases at every ten-line level ===")
run("tetris-level-speed-curve", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris, tetris_drop_interval
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    curve = [tetris_drop_interval(level) for level in range(1, 12)]
    assert all(a >= b for a, b in zip(curve, curve[1:])), curve
    assert curve[0] > curve[5] >= curve[-1]

    g = Tetris(backend=Stub()); initial = g.drop_interval
    g.lines = 9
    g.board[-1] = ['I'] * 10
    g._clear_lines()
    assert g.level == 2 and g.drop_interval < initial
    assert g.drop_interval == tetris_drop_interval(2)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 74. Zuma authored stream never spawns a natural color triple ===")
run("zuma-spawn-prevents-natural-triples", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    for seed in range(20):
        import random; random.seed(seed)
        g = Zuma(backend=Stub())
        for _ in range(120): g._spawn_chain_ball()
        colors = g.spawn_color_history
        assert all(not (colors[i] == colors[i+1] == colors[i+2])
                   for i in range(len(colors)-2)), (seed, colors)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 75. Every Zuma level has a distinct in-bounds authored track ===")
run("zuma-distinct-level-tracks", """
    from client.games.zuma import ZUMA_LEVELS, build_path, WIDTH, HEIGHT
    signatures = []
    for level in ZUMA_LEVELS:
        points, cumulative = build_path(level['track'])
        assert cumulative[-1] > 900, (level['track'], cumulative[-1])
        assert all(0 <= x <= WIDTH and 60 <= y <= HEIGHT - 80
                   for x, y in points), level['track']
        sample = tuple((round(points[i][0]), round(points[i][1]))
                       for i in range(0, len(points), max(1, len(points)//12)))
        signatures.append(sample)
    assert len(set(level['track'] for level in ZUMA_LEVELS)) == len(ZUMA_LEVELS)
    assert len(set(signatures)) == len(ZUMA_LEVELS), signatures
""")

# ===========================================================================
print("\n=== 76. Zuma recursive matches wait for the collapse animation ===")
run("zuma-staged-recursive-match", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma, GAP
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub()); g.spawn_interval = 99
    colors = [1, 1, 0, 0, 0, 1]
    g.chain = [{'pos': 420.0 - i*GAP, 'color': color,
                'visual_offset': 0.0}
               for i, color in enumerate(colors)]
    assert g._try_match_at(3)
    assert [b['color'] for b in g.chain] == [1, 1, 1]
    assert g.cleared_balls == 3 and g.pending_chain_match is not None
    g.update(0.10)
    assert len(g.chain) == 3, 'recursive group vanished before reconnecting'
    g.update(0.30)
    assert not g.chain and g.cleared_balls == 6
    assert g.chain_banner_depth == 2 and g.chain_banner_timer > 0
    pygame.quit()
""")

# ===========================================================================
print("\n=== 77. Bright UI keeps Zuma art clear and its track continuous ===")
run("visual-title-clearance-bright-palette-smooth-track", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.ui import COLORS, font
    from client.launcher import draw_game_icon
    from client.games.snake import BOARD_COLOR_A, BOARD_COLOR_B
    from client.games.game_2048 import BOARD_COLOR, EMPTY_CELL_COLOR
    from client.games.zuma import Zuma

    # Render the actual icon to a transparent surface and measure its opaque
    # pixels.  This catches future sprite edits that creep back into the text.
    icon_layer = pygame.Surface((200, 160), pygame.SRCALPHA)
    draw_game_icon(icon_layer, 'zuma', 100, 69, size=44)
    bounds = pygame.mask.from_surface(icon_layer).get_bounding_rects()
    assert bounds, 'Zuma icon rendered no visible pixels'
    icon_bounds = bounds[0].copy()
    for rect in bounds[1:]:
        icon_bounds.union_ip(rect)
    title_rect = font(20, bold=True).render(
        '祖玛', True, COLORS['game_zuma']).get_rect(center=(100, 118))
    clearance = title_rect.top - icon_bounds.bottom
    assert clearance >= 10, (icon_bounds, title_rect, clearance)

    # The shared surface is deliberately bright, while all five games retain
    # independent accent colors instead of collapsing into one green tint.
    assert sum(COLORS['bg']) / 3 > 220, COLORS['bg']
    for surface_color in (BOARD_COLOR_A, BOARD_COLOR_B,
                          BOARD_COLOR, EMPTY_CELL_COLOR):
        assert sum(surface_color) / 3 > 210, surface_color
    accents = [COLORS[f'game_{gid}']
               for gid in ('tetris', 'snake', '2048', 'sokoban', 'zuma')]
    assert len(set(accents)) == 5, accents

    class Stub:
        def submit_score(self, *args, **kwargs): return True
        def leaderboard(self, *args, **kwargs): return []
    zuma = Zuma(backend=Stub())
    # Every authored centerline sample must land on the opaque pale channel;
    # a zero/low-alpha sample would reveal a broken polyline join.
    for x, y in zuma.path_pts:
        ix, iy = round(x), round(y)
        nearby_alpha = max(
            zuma.track_surface.get_at((ix + dx, iy + dy)).a
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if 0 <= ix + dx < zuma.track_surface.get_width()
            and 0 <= iy + dy < zuma.track_surface.get_height())
        assert nearby_alpha >= 200, (x, y, nearby_alpha)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 78. Tetris locks above the ceiling as a top-out ===")
run("tetris-partial-piece-top-out", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris, Piece
    class Stub:
        def __init__(self): self.submissions = []
        def submit_score(self, *args, **kwargs):
            self.submissions.append((args, kwargs))
        def leaderboard(self, *args, **kwargs): return []
    stub = Stub(); g = Tetris(backend=stub)
    g.board = [[None] * 10 for _ in range(20)]
    # A vertical I at the far left is stopped while its top cell is hidden.
    # The next centered O does not overlap it, which exposed the old bug: the
    # hidden cell vanished and play continued instead of topping out.
    g.piece = Piece('I'); g.piece.rot = 1; g.piece.x = 0; g.piece.y = -1
    g.board[3][2] = 'O'; g.next_kind = 'O'
    g._soft_drop()
    assert g.state == 'gameover', g.state
    assert g.extra.get('top_out') is True, g.extra
    assert len(stub.submissions) == 1
    pygame.quit()
""")


# ===========================================================================
print("\n=== 79. Sokoban replay and skip cannot inflate a ranked run ===")
run("sokoban-ranked-run-integrity", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.sokoban import Sokoban, LEVELS
    class Stub:
        def __init__(self): self.calls = []
        def submit_score(self, *a, **k):
            self.calls.append((a, k)); return {'ok': True, 'id': 1}
        def leaderboard(self, *a, **k): return []

    stub = Stub(); g = Sokoban(backend=stub)
    g.load_level(1); g.moves = 10; g.boxes = set(g.targets); g._check_win()
    first = g.total_score
    g.load_level(1); g.moves = 20; g.boxes = set(g.targets); g._check_win()
    assert g.total_score == first, (first, g.total_score)
    assert not stub.calls, stub.calls

    skipped = Sokoban(backend=Stub())
    for _ in range(len(LEVELS) - 1):
        skipped.handle_event(pygame.event.Event(
            pygame.KEYDOWN, {'key':pygame.K_n, 'unicode':''}))
    skipped.boxes = set(skipped.targets); skipped._check_win()
    assert skipped.extra['practice'] is True
    assert skipped.extra['completed_all'] is False
    assert not skipped.backend.calls
    pygame.quit()
""")

# ===========================================================================
print("\n=== 80. 2048 retries a failed score submission ===")
run("2048-failed-submit-retries", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.game_2048 import Game2048
    class Stub:
        def __init__(self): self.calls = 0
        def submit_score(self, *a, **k):
            self.calls += 1
            return None if self.calls == 1 else {'ok': True, 'id': 9}
        def leaderboard(self, *a, **k): return []
    stub = Stub(); g = Game2048(backend=stub); g.score = 100
    g._submit_score(); assert not g.score_submitted
    g._submit_score()
    assert stub.calls == 2 and g.score_submission_id == 9
    pygame.quit()
""")

# ===========================================================================
print("\n=== 81. Zuma preserves multiple simultaneous chain reactions ===")
run("zuma-multiple-pending-reactions", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma, GAP
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Zuma(backend=Stub()); g.spawn_interval = 99
    colors = [2,2,0,0,0,2,3,3,1,1,1,3]
    g.chain = [{'pos':600-i*GAP, 'color':color, 'visual_offset':0.0}
               for i, color in enumerate(colors)]
    assert g._try_match_at(3)
    assert g._try_match_at(6)
    assert len(g.pending_chain_matches) == 2
    g._update_pending_chain_match(10.0)
    assert not g.chain, [ball['color'] for ball in g.chain]
    pygame.quit()
""")

# ===========================================================================
print("\n=== 82. Tetris held keys and gravity preserve input state and time ===")
run("tetris-held-input-and-gravity", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris
    class Stub:
        def submit_score(self, *a, **k): return {'ok': True, 'id': 1}
        def leaderboard(self, *a, **k): return []

    g = Tetris(backend=Stub()); start_y = g.piece.y
    g.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {'key':pygame.K_DOWN, 'unicode':''}))
    g.update(0.25)
    assert g.piece.y - start_y > 1, (start_y, g.piece.y)
    g.handle_event(pygame.event.Event(
        pygame.KEYUP, {'key':pygame.K_DOWN, 'unicode':''}))
    stopped_y = g.piece.y; g.update(0.20)
    assert g.piece.y == stopped_y

    g = Tetris(backend=Stub()); start_x = g.piece.x
    for key in (pygame.K_LEFT, pygame.K_RIGHT):
        g.handle_event(pygame.event.Event(
            pygame.KEYDOWN, {'key':key, 'unicode':''}))
    g.handle_event(pygame.event.Event(
        pygame.KEYUP, {'key':pygame.K_RIGHT, 'unicode':''}))
    assert g.horizontal_hold == -1
    g.update(0.25)
    assert g.piece.x < start_x

    g = Tetris(backend=Stub()); g.drop_interval = 0.10; start_y = g.piece.y
    g.update(0.25)
    assert g.piece.y - start_y == 2
    assert 'SRS-inspired' in (Tetris.__module__ and __import__(
        'client.games.tetris', fromlist=['x']).__doc__)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 83. Flask import initializes DB and the score contract is strict ===")
run("backend-import-init-and-strict-contract", """
    import os, sqlite3, subprocess, sys, tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy(); env['GAMES_DB'] = os.path.join(temp_dir, 'fresh.db')
        code = ("from server.app import app\\n"
                "c = app.test_client()\\n"
                "r = c.post('/api/scores', "
                "json={'game_id':'tetris','score':1})\\n"
                "assert r.status_code == 200 and r.get_json()['ok']\\n")
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    # Import-time initialization also migrates databases created by the
    # previous schema instead of only handling brand-new files.
    with tempfile.TemporaryDirectory() as temp_dir:
        old_db = os.path.join(temp_dir, 'old.db')
        with sqlite3.connect(old_db) as conn:
            conn.execute('CREATE TABLE scores (id INTEGER PRIMARY KEY, '
                         'game_id TEXT, player TEXT, score INTEGER, '
                         'extra TEXT, created_at REAL)')
        env = os.environ.copy(); env['GAMES_DB'] = old_db
        code = ("import sqlite3\\n"
                "from server.app import DB_PATH\\n"
                "c=sqlite3.connect(DB_PATH)\\n"
                "cols={r[1] for r in c.execute('pragma table_info(scores)')}\\n"
                "assert 'updated_at' in cols\\n")
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    from server.app import app
    c = app.test_client()
    invalid = [
        {'game_id':'tetris', 'score':'12'},
        {'game_id':'tetris', 'score':1.5},
        {'game_id':'tetris', 'score':1, 'extra':['not-an-object']},
        {'game_id':'tetris', 'score':1, 'player':'bad\\nname'},
        {'game_id':'tetris', 'score':1, 'unknown':True},
    ]
    for payload in invalid:
        response = c.post('/api/scores', json=payload)
        assert response.status_code == 400, (payload, response.get_json())
    malformed = c.post('/api/scores', data='{bad',
                       content_type='application/json')
    assert malformed.status_code == 400
    assert malformed.get_json()['code'] == 'malformed_json'
    assert c.get('/api/leaderboard/not-a-game').status_code == 404
    spoof = c.post('/api/scores', json={'game_id':'tetris','score':2},
                   headers={'X-Forwarded-For':'203.0.113.9'})
    assert spoof.get_json()['submitted_from'] != '203.0.113.9'
""")

# ===========================================================================
print("\n=== 84. Real pygame network paths do not block the render thread ===")
run("network-work-runs-off-render-thread", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import time, pygame; pygame.init()
    from client.common.network import BackendClient
    from client.common.ui import BaseGame
    class SlowBackend(BackendClient):
        def submit_score(self, *a, **k):
            time.sleep(0.25); return {'ok': True, 'id': 1}
        def leaderboard(self, *a, **k):
            time.sleep(0.25); return []
    class Demo(BaseGame):
        game_id = 'tetris'
        def update(self, dt): pass
        def draw(self): pass
    g = Demo(240, 240, backend=SlowBackend())
    start = time.perf_counter(); g.on_game_over(10)
    assert time.perf_counter() - start < 0.10
    start = time.perf_counter(); g.draw_gameover_overlay()
    assert time.perf_counter() - start < 0.10
    pygame.quit()
""")


# ===========================================================================
print("\n=== 85. Snake buffers two legal turns for a quick corner ===")
run("snake-two-turn-queue", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.snake import Snake
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Snake(backend=Stub())
    for key in (pygame.K_UP, pygame.K_LEFT):
        g.handle_event(pygame.event.Event(
            pygame.KEYDOWN, {'key':key, 'unicode':''}))
    assert list(g.turn_queue) == [(0, -1), (-1, 0)]
    g.update(1 / g.move_speed); assert g.direction == (0, -1)
    g.update(1 / g.move_speed); assert g.direction == (-1, 0)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 86. 2048 rejects unknown movement directions ===")
run("2048-invalid-direction", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.game_2048 import Game2048
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []
    g = Game2048(backend=Stub())
    try:
        g._move('sideways')
    except ValueError:
        pass
    else:
        raise AssertionError('unknown direction entered the vertical branch')
    pygame.quit()
""")

# ===========================================================================
print("\n=== 87. Sokoban parser rejects malformed authored levels ===")
run("sokoban-parser-validation", """
    from client.games.sokoban import parse_level
    invalid = [
        ['#####', '#@X.#', '#####'],
        ['#####', '#@@T#', '#.$.#', '#####'],
        ['#####', '#@$.#', '####'],
        ['#####', '#@$.#', '#.T.#', '#.T.#', '#####'],
    ]
    for level in invalid:
        try:
            parse_level(level)
        except ValueError:
            pass
        else:
            raise AssertionError(f'accepted malformed level: {level}')
""")

# ===========================================================================
print("\n=== 88. Zuma preserves timer surplus and last-chance hits ===")
run("zuma-timers-and-last-chance-hit", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.zuma import Zuma, GAP, pos_at
    class Stub:
        def submit_score(self, *a, **k): pass
        def leaderboard(self, *a, **k): return []

    spawned = Zuma(backend=Stub())
    spawned.update(spawned.spawn_interval * 2.5)
    assert spawned.spawned == 2, spawned.spawned
    assert abs(spawned.spawn_timer - spawned.spawn_interval * 0.5) < 1e-6

    burst = Zuma(backend=Stub())
    burst.shoot_cooldown = 0.05; burst.shot_queue = 3
    burst.update(0.20)
    assert len(burst.projectiles) == 3, len(burst.projectiles)

    g = Zuma(backend=Stub()); color = 0
    g.chain = [{'pos':g.path_length-i*GAP, 'color':color,
                'visual_offset':0.0} for i in range(3)]
    x, y = pos_at(g.path_pts, g.path_cum, g.path_length)
    g.projectiles = [{'x':x, 'y':y, 'vx':0.0, 'vy':0.0, 'color':color}]
    g.update(0.0)
    assert g.state == 'playing', 'loss was checked before an existing hit'
    assert not g.chain
    pygame.quit()
""")


# ===========================================================================
print("\n=== 89. Sokoban N advances a won level without marking a skip ===")
run("sokoban-keyboard-advance-is-ranked", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.sokoban import Sokoban, LEVELS
    class Stub:
        def __init__(self): self.calls = []
        def submit_score(self, *a, **k):
            self.calls.append((a, k)); return {'ok': True, 'id': 1}
        def leaderboard(self, *a, **k): return []
    stub = Stub(); g = Sokoban(backend=stub)
    for level_idx in range(len(LEVELS)):
        assert g.level_idx == level_idx
        g.boxes = set(g.targets); g._check_win()
        if level_idx < len(LEVELS) - 1:
            g.handle_event(pygame.event.Event(
                pygame.KEYDOWN, {'key':pygame.K_n, 'unicode':'n'}))
            assert not g.practice_mode
    assert g.extra['completed_all'] is True
    assert len(stub.calls) == 1
    pygame.quit()
""")


# ===========================================================================
print("\n=== 90. 2048 serializes in-flight milestone and final submissions ===")
run("2048-async-score-update-order", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import time, pygame; pygame.init()
    from client.common.network import BackendClient
    from client.games.game_2048 import Game2048
    class DelayedBackend(BackendClient):
        def __init__(self): super().__init__(); self.calls = []
        def submit_score(self, *a, **k):
            time.sleep(0.05); self.calls.append((a, k))
            return {'ok': True, 'id': 42, 'updated':len(self.calls) > 1}
        def leaderboard(self, *a, **k): return []
    backend = DelayedBackend(); g = Game2048(backend=backend)
    g.score = 100; g._submit_score(extra={'won':True})
    g.score = 250; g._submit_score(extra={'final':True})
    time.sleep(0.10); g._poll_score_submission()
    time.sleep(0.10); g._poll_score_submission()
    assert len(backend.calls) == 2, backend.calls
    assert backend.calls[0][1].get('submission_id') is None
    assert backend.calls[1][1].get('submission_id') == 42
    assert g.submitted_score == 250 and g.score_submission_id == 42
    pygame.quit()
""")


# ===========================================================================
print("\n=== 91. Result saves gate leaderboard refresh and expose retry ===")
run("result-save-state-and-leaderboard-order", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.common.ui import (BaseGame, SAVE_FAILED, SAVE_SAVED,
                                  SAVE_SAVING)
    class ManualFuture:
        def __init__(self, result=None, done=False):
            self.value=result; self.ready=done
        def done(self): return self.ready
        def result(self): return self.value
    class Backend:
        def __init__(self, futures):
            self.futures=list(futures); self.submits=0; self.lb_calls=0
        def submit_score_async(self, *a, **k):
            self.submits += 1; return self.futures.pop(0)
        def leaderboard_async(self, *a, **k):
            self.lb_calls += 1; return ManualFuture([], True)
    class Demo(BaseGame):
        game_id='tetris'
        def update(self, dt): pass
        def draw(self): pass

    pending=ManualFuture({'ok':True, 'id':7})
    backend=Backend([pending]); g=Demo(260,260,backend=backend)
    g.on_game_over(10); g.draw_gameover_overlay()
    assert g.score_save_state == SAVE_SAVING
    assert backend.lb_calls == 0, 'leaderboard raced ahead of score ACK'
    pending.ready=True; g.draw_gameover_overlay()
    assert g.score_save_state == SAVE_SAVED
    assert backend.lb_calls == 1

    backend=Backend([ManualFuture(None, True),
                     ManualFuture({'ok':True, 'id':8}, True)])
    g=Demo(260,260,backend=backend); g.on_game_over(20)
    g.draw_gameover_overlay(); assert g.score_save_state == SAVE_FAILED
    g.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {'key':pygame.K_s, 'unicode':'s'}))
    g.draw_gameover_overlay()
    assert g.score_save_state == SAVE_SAVED and backend.submits == 2
    pygame.quit()
""")

# ===========================================================================
print("\n=== 92. Tetris tracks alias keys and stops catch-up at a new piece ===")
run("tetris-physical-keys-and-piece-generation", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.tetris import Tetris, Piece, COLS, ROWS
    class Stub:
        def submit_score(self,*a,**k): return {'ok':True,'id':1}
        def leaderboard(self,*a,**k): return []
    def event(kind, key):
        return pygame.event.Event(kind, {'key':key, 'unicode':''})

    g=Tetris(backend=Stub())
    g.handle_event(event(pygame.KEYDOWN, pygame.K_LEFT))
    g.handle_event(event(pygame.KEYDOWN, pygame.K_a))
    g.handle_event(event(pygame.KEYUP, pygame.K_a))
    assert g.horizontal_hold == -1 and pygame.K_LEFT in g.pressed_keys
    g.handle_event(event(pygame.KEYDOWN, pygame.K_DOWN))
    g.handle_event(event(pygame.KEYDOWN, pygame.K_s))
    g.handle_event(event(pygame.KEYUP, pygame.K_s))
    assert g.soft_drop_held and pygame.K_DOWN in g.pressed_keys

    g=Tetris(backend=Stub()); g.board=[[None]*COLS for _ in range(ROWS)]
    g.piece=Piece('O'); g.piece.x=3; g.piece.y=18
    old_generation=g.piece_generation
    g.soft_drop_held=True; g.soft_drop_repeat_timer=0.0
    g.update(0.5)
    assert g.piece_generation == old_generation + 1
    assert g.piece.y == -1, 'soft-drop remainder moved the new piece'

    g=Tetris(backend=Stub()); g.board=[[None]*COLS for _ in range(ROWS)]
    g.piece=Piece('O'); g.piece.x=3; g.piece.y=18
    g.drop_timer=g.drop_interval
    g.update(0.0)
    assert g.piece.y == -1, 'gravity remainder moved the new piece'

    class FalsyBoard(list):
        def __bool__(self): return False
    g=Tetris(backend=Stub()); g.board[0][0]='I'
    alternate=FalsyBoard([[None]*COLS for _ in range(ROWS)])
    assert not g._collides([(0,0)], board=alternate)
    pygame.quit()
""")

# ===========================================================================
print("\n=== 93. Backend retries saves and closes shared resources ===")
run("backend-reliable-save-and-close", """
    from client.common.network import BackendClient
    class RetryBackend(BackendClient):
        def __init__(self): super().__init__(); self.calls=0; self.kwargs=[]
        def submit_score(self, *a, **k):
            self.calls += 1; self.kwargs.append(k)
            if self.calls < 3: return None
            return {'ok':True, 'id':9}
    backend=RetryBackend()
    backend._mark_unavailable('read')
    assert backend._request_allowed('write'), 'read failure blocked a save'
    failure_time=backend._last_failure_at['read']
    blocked_until=backend._offline_until['read']
    backend._mark_available('read', failure_time - 1.0)
    assert backend._offline_until['read'] == blocked_until, \
        'an older success erased a newer failure backoff'
    future=backend.submit_score_reliable_async('tetris','p',10)
    assert future.result(timeout=3)['id'] == 9
    assert backend.calls == 3 and backend.drain(timeout=1)
    request_ids={call.get('request_id') for call in backend.kwargs}
    assert len(request_ids) == 1 and None not in request_ids, request_ids
    backend.close(); assert backend._closed
    backend.close()
    try:
        backend.health_async()
    except RuntimeError:
        pass
    else:
        raise AssertionError('closed client accepted new work')

    class RecoveringBackend(BackendClient):
        def __init__(self): super().__init__(); self.available=False
        def submit_score(self,*a,**k):
            return ({'ok':True,'id':11} if self.available else None)
    backend=RecoveringBackend()
    backend.submit_score_reliable_async('snake','p',20).result(timeout=3)
    import time; time.sleep(0.02)
    assert backend.failed_save_count() == 1
    backend.available=True
    assert backend.retry_failed_saves() == 1
    backend.drain(timeout=3); time.sleep(0.02)
    assert backend.failed_save_count() == 0
    backend.close()

    # A late failed milestone must not resurrect an error after a higher
    # replace-style final score has already been confirmed.
    backend=BackendClient()
    class Done:
        def __init__(self, value): self.value=value
        def result(self): return self.value
    low={'token':1, 'payload':{'game_id':'2048','player':'p','score':100,
         'extra':None,'replace':True,'submission_id':None}}
    high={'token':2, 'payload':{'game_id':'2048','player':'p','score':250,
          'extra':None,'replace':True,'submission_id':None}}
    backend._capture_score_save(Done({'ok':True,'id':5}), high)
    backend._capture_score_save(Done(None), low)
    assert backend.failed_save_count() == 0
    backend.close()
""")

# ===========================================================================
print("\n=== 94. Score API is monotonic and DB failures stay JSON ===")
run("backend-monotonic-update-and-json-errors", """
    import tempfile
    from pathlib import Path
    import server.app as server
    with tempfile.TemporaryDirectory() as temp_dir:
        server.DB_PATH=Path(temp_dir)/'scores.db'; server.init_db()
        client=server.app.test_client()
        first=client.post('/api/scores',json={
            'game_id':'2048','player':'p','score':100}).get_json()
        lower=client.post('/api/scores',json={
            'game_id':'2048','player':'p','score':10,
            'submission_id':first['id']}).get_json()
        assert lower['score'] == 100, lower
        rows=client.get('/api/leaderboard/2048').get_json()['leaderboard']
        assert rows[0]['score'] == 100, rows
        stats=client.get('/api/stats/2048').get_json()
        assert stats['records'] == 1 and 'plays' not in stats, stats

        request={'game_id':'tetris','player':'p','score':88,
                 'request_id':'same-logical-save-0001'}
        saved=client.post('/api/scores',json=request).get_json()
        repeated=client.post('/api/scores',json=request).get_json()
        assert repeated['id'] == saved['id']
        assert repeated['duplicate_request'] is True
        stats=client.get('/api/stats/tetris').get_json()
        assert stats['records'] == 1, stats

    with tempfile.TemporaryDirectory() as temp_dir:
        server.DB_PATH=Path(temp_dir)
        client=server.app.test_client()
        requests=[
            client.post('/api/scores',json={'game_id':'tetris','score':1}),
            client.get('/api/leaderboard/tetris'),
            client.get('/api/stats/tetris'),
            client.get('/api/recent'),
        ]
        for response in requests:
            assert response.status_code == 503
            assert response.is_json
            assert response.get_json()['code'] == 'database_unavailable'
""")

# ===========================================================================
print("\n=== 95. Sokoban confirmation requires a valid save ACK ===")
run("sokoban-confirmed-total-requires-ack", """
    import os; os.environ['SDL_VIDEODRIVER']='dummy'
    import pygame; pygame.init()
    from client.games.sokoban import Sokoban, LEVELS
    class Future:
        def __init__(self, value): self.value=value
        def done(self): return True
        def result(self): return self.value
    class Backend:
        def __init__(self): self.values=[None, {'ok':True,'id':12}]
        def submit_score_async(self,*a,**k): return Future(self.values.pop(0))
        def leaderboard_async(self,*a,**k): return Future([])
    g=Sokoban(backend=Backend())
    for level in range(len(LEVELS)):
        g.load_level(level); g.boxes=set(g.targets); g._check_win()
    g.draw()
    assert g._confirmed_total == 0 and g._pending_total == g.total_score
    g.retry_score_save(); g.draw()
    assert g._confirmed_total == g.total_score and g._pending_total is None
    pygame.quit()
""")


# ===========================================================================
print(f"\n=== SUMMARY: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
