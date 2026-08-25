"""Single source of metadata for locally installed games."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class ScorePolicy(str, Enum):
    FINAL_ONLY = "final_only"
    MONOTONIC_REVISION = "monotonic_revision"


@dataclass(frozen=True)
class GameDescriptor:
    id: str
    name: str
    description: str
    module: str
    tag: str
    color_key: str
    ruleset_version: str
    score_policy: ScorePolicy

    def public_dict(self) -> dict:
        data = asdict(self)
        return {key: data[key] for key in ("id", "name", "description")}


GAMES = (
    GameDescriptor("tetris", "俄罗斯方块", "经典下落方块消除游戏",
                   "client.games.tetris", "旋转与消行", "game_tetris",
                   "tetris-assist-3", ScorePolicy.FINAL_ONLY),
    GameDescriptor("snake", "贪吃蛇", "控制蛇吃食物变长",
                   "client.games.snake", "追逐与成长", "game_snake",
                   "snake-classic-1", ScorePolicy.FINAL_ONLY),
    GameDescriptor("2048", "2048", "滑动合并相同数字",
                   "client.games.game_2048", "滑动与合并", "game_2048",
                   "2048-classic-2", ScorePolicy.MONOTONIC_REVISION),
    GameDescriptor("sokoban", "推箱子", "把箱子推到目标点",
                   "client.games.sokoban", "规划与推动", "game_sokoban",
                   "sokoban-campaign-2", ScorePolicy.MONOTONIC_REVISION),
    GameDescriptor("zuma", "祖玛", "发射彩球匹配消除",
                   "client.games.zuma", "瞄准与连锁", "game_zuma",
                   "zuma-classic-2", ScorePolicy.FINAL_ONLY),
)


def validate_catalog(games=GAMES) -> None:
    ids = [game.id for game in games]
    if len(ids) != len(set(ids)):
        raise RuntimeError("game catalog contains duplicate IDs")
    for game in games:
        if (not game.id or not game.module or not game.ruleset_version
                or not isinstance(game.score_policy, ScorePolicy)):
            raise RuntimeError(f"invalid game descriptor: {game.id!r}")


validate_catalog()

GAME_BY_ID = {game.id: game for game in GAMES}
VALID_GAME_IDS = frozenset(GAME_BY_ID)


def public_games() -> list[dict]:
    return [game.public_dict() for game in GAMES]
