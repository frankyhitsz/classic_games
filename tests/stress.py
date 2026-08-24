"""Repeatable gameplay, rendering, persistence, and resource stress checks."""

from __future__ import annotations

import os
import random
import sqlite3
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from client.games.game_2048 import Game2048
from client.games.snake import COLS as SNAKE_COLS
from client.games.snake import ROWS as SNAKE_ROWS
from client.games.snake import Snake
from client.games.sokoban import Sokoban
from client.games.tetris import COLS as TETRIS_COLS
from client.games.tetris import ROWS as TETRIS_ROWS
from client.games.tetris import Tetris
from client.games.zuma import Zuma
from game_service.local_backend import LocalBackendClient, completed_future
from game_service.store import LocalGameStore

SEED = 20260824
CI_RENDER_P95_MS = 50.0
LOCAL_RENDER_P95_MS = 16.7
# GitHub's Windows image can pause durable SQLite fsync behind Defender for
# roughly 100 ms. Desktop gameplay uses the asynchronous facade, measured
# separately below; this CI limit catches stalls without pretending shared
# runner storage is a frame-time benchmark.
CI_SAVE_P95_MS = 250.0
LOCAL_SAVE_P95_MS = 16.7
CI_SUBMIT_P99_MS = 10.0
LOCAL_SUBMIT_P99_MS = 2.0


class StubBackend:
    def submit_score(self, *_args, **_kwargs):
        return {"ok": True, "id": 1}

    def submit_score_async(self, *_args, **_kwargs):
        return completed_future({"ok": True, "id": 1})

    submit_score_reliable_async = submit_score_async

    def leaderboard(self, *_args, **_kwargs):
        return []

    def leaderboard_async(self, *_args, **_kwargs):
        return completed_future([])


def exercise_games(rng: random.Random) -> None:
    backend = StubBackend()

    game = Tetris(backend=backend)
    keys = [pygame.K_LEFT, pygame.K_a, pygame.K_RIGHT, pygame.K_d,
            pygame.K_DOWN, pygame.K_s, pygame.K_UP, pygame.K_x, pygame.K_z]
    held = set()
    for _ in range(5000):
        key = rng.choice(keys)
        kind = (pygame.KEYUP if key in held and rng.random() < 0.45
                else pygame.KEYDOWN)
        if kind == pygame.KEYDOWN:
            held.add(key)
        else:
            held.discard(key)
        game.handle_event(pygame.event.Event(
            kind, {"key": key, "unicode": ""}))
        game.update(rng.choice([0.0, 1 / 120, 1 / 60, 0.04, 0.3, 0.8]))
        assert len(game.board) == TETRIS_ROWS
        assert all(len(row) == TETRIS_COLS for row in game.board)
        if game.state == "gameover":
            game.reset()
            held.clear()

    game = Snake(backend=backend)
    keys = [pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN,
            pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s]
    for _ in range(4000):
        if rng.random() < 0.5:
            key = rng.choice(keys)
            game.handle_event(pygame.event.Event(
                pygame.KEYDOWN, {"key": key, "unicode": ""}))
        game.update(rng.choice([1 / 120, 1 / 60, 0.05, 0.24, 0.7]))
        assert len(set(game.body)) == len(game.body)
        assert all(0 <= x < SNAKE_COLS and 0 <= y < SNAKE_ROWS
                   for x, y in game.body)
        if game.state != "playing":
            game.reset()

    game = Game2048(backend=backend)
    for _ in range(4000):
        if rng.random() < 0.7:
            game._move(rng.choice(["left", "right", "up", "down"]))
        game._tick_animations(rng.choice([0.0, 0.02, 0.08, 0.3]))
        live = [tile for tile in game.tiles if not tile.dead]
        assert len({(tile.row, tile.col) for tile in live}) == len(live)
        assert len(game._queued_directions) <= 2
        if game.state == "won":
            game._continue_after_win()
        elif game.state == "gameover":
            game.reset()

    game = Sokoban(backend=backend)
    keys = [pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN,
            pygame.K_u, pygame.K_r]
    for _ in range(2000):
        key = rng.choice(keys)
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, {"key": key, "unicode": ""}))
        assert game.player_pos in game.floors
        assert game.boxes <= game.floors
        if game.state == "won":
            game._advance_after_win()

    game = Zuma(backend=backend)
    for _ in range(5000):
        if rng.random() < 0.12:
            pos = (rng.randrange(game.width), rng.randrange(game.height))
            game.handle_event(pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": pos}))
            game.handle_event(pygame.event.Event(
                pygame.MOUSEBUTTONUP, {"button": 1, "pos": pos}))
        game.update(rng.choice([0.0, 1 / 120, 1 / 60, 0.05, 0.2]))
        positions = [ball["pos"] for ball in game.chain]
        assert all(positions[index] >= positions[index + 1] - 1e-6
                   for index in range(len(positions) - 1))
        if game.state in ("won", "gameover"):
            game.reset()

    print("gameplay-stress: 20000 deterministic steps")


def render_benchmark() -> None:
    backend = StubBackend()
    results = []
    p95_budget = (CI_RENDER_P95_MS if os.environ.get("CI")
                  else LOCAL_RENDER_P95_MS)
    for game_type in (Tetris, Snake, Game2048, Sokoban, Zuma):
        game = game_type(backend=backend)
        for _ in range(10):
            game.draw()
        samples = []
        for _ in range(80):
            started = time.perf_counter()
            game.draw()
            samples.append((time.perf_counter() - started) * 1000)
        ordered = sorted(samples)
        p95 = ordered[int(len(ordered) * 0.95) - 1]
        assert p95 <= p95_budget, (game_type.__name__, p95, p95_budget)
        results.append(
            f"{game_type.__name__} median={statistics.median(samples):.3f}ms "
            f"p95={p95:.3f}ms")
    print("render-benchmark: " + "; ".join(results))


def storage_stress(root: Path) -> None:
    backend = LocalBackendClient(
        db_path=root / "latency.db", outbox_path=root / "latency-outbox.json")
    samples = []
    for index in range(300):
        started = time.perf_counter()
        result = backend.submit_score(
            "tetris", f"p{index % 10}", index,
            request_id=f"benchmark-request-{index:016d}")
        assert result["ok"]
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    p99 = ordered[int(len(ordered) * 0.99) - 1]
    save_budget = (CI_SAVE_P95_MS if os.environ.get("CI")
                   else LOCAL_SAVE_P95_MS)
    assert p95 <= save_budget, (p95, save_budget)
    backend.close()
    print(f"local-save: median={statistics.median(samples):.3f}ms "
          f"p95={p95:.3f}ms p99={p99:.3f}ms")

    locked = LocalBackendClient(
        db_path=root / "locked-latency.db",
        outbox_path=root / "locked-pending")
    blocker = sqlite3.connect(root / "locked-latency.db")
    blocker.execute("BEGIN IMMEDIATE")
    submit_samples = []
    futures = []
    for index in range(20):
        started = time.perf_counter()
        futures.append(locked.submit_score_async(
            "snake", f"locked-{index}", index,
            request_id=f"locked-stress-request-{index:016d}"))
        submit_samples.append((time.perf_counter() - started) * 1000)
    first = futures[0].result(timeout=1)
    assert first["durable_pending"]
    blocker.rollback()
    blocker.close()
    assert locked.drain(5)
    locked.retry_failed_saves().result(timeout=2)
    assert locked.drain(5)
    assert locked.store.attempt_count("snake") == 20
    submit_p99 = sorted(submit_samples)[int(len(submit_samples) * 0.99) - 1]
    submit_budget = (CI_SUBMIT_P99_MS if os.environ.get("CI")
                     else LOCAL_SUBMIT_P99_MS)
    assert submit_p99 <= submit_budget, (submit_p99, submit_budget)
    locked.close()
    print(f"locked-submit: p99={submit_p99:.3f}ms, durable fallback=ok")

    before_threads = {thread.ident for thread in threading.enumerate()}
    fd_path = Path("/dev/fd")
    before_fds = len(list(fd_path.iterdir())) if fd_path.is_dir() else None
    for _ in range(100):
        client = LocalBackendClient(
            db_path=root / "cycles.db", outbox_path=root / "cycles-outbox.json")
        assert client.health()
        client.leaderboard("tetris")
        client.close()
    after_threads = {thread.ident for thread in threading.enumerate()}
    after_fds = len(list(fd_path.iterdir())) if fd_path.is_dir() else None
    assert after_threads == before_threads
    if before_fds is not None and after_fds is not None:
        assert after_fds <= before_fds + 1
    print(f"resource-cycles: 100, fds={before_fds}->{after_fds}")

    # This branch intentionally bypasses the desktop's one-writer executor
    # and starts 16 direct SQLite writers. Give that synthetic contention a
    # larger busy budget than the latency-sensitive desktop facade.
    # Shared CI disks on every OS can pause under unrelated load. The desktop
    # never starts these 16 direct writers; this synthetic integrity check
    # should wait for serialization rather than mistake slow storage for a
    # locking bug. Foreground submission latency has a separate assertion.
    direct_busy_timeout_ms = 60_000 if os.environ.get("CI") else 2_000
    store = LocalGameStore(
        root / "concurrent.db", busy_timeout_ms=direct_busy_timeout_ms)

    def write(index: int):
        game_id = ["tetris", "snake", "2048", "sokoban", "zuma"][index % 5]
        return store.record_score(
            game_id, f"p{index % 17}", index,
            request_id=f"parallel-save-request-{index:016d}")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(write, range(240)))
    assert all(result["ok"] for result in results)
    assert store.attempt_count() == 240
    with closing(sqlite3.connect(root / "concurrent.db")) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("concurrent-writes: 240, integrity_check=ok")


def main() -> None:
    pygame.init()
    try:
        exercise_games(random.Random(SEED))
        render_benchmark()
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_stress(Path(temp_dir))
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
