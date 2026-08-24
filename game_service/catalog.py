"""Single source of metadata for locally installed games."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GameDescriptor:
    id: str
    name: str
    description: str
    module: str
    tag: str
    color_key: str
    ruleset_version: str = "1"

    def public_dict(self) -> dict:
        data = asdict(self)
        return {key: data[key] for key in ("id", "name", "description")}


GAMES = (
    GameDescriptor("tetris", "俄罗斯方块", "经典下落方块消除游戏",
                   "client.games.tetris", "旋转与消行", "game_tetris"),
    GameDescriptor("snake", "贪吃蛇", "控制蛇吃食物变长",
                   "client.games.snake", "追逐与成长", "game_snake"),
    GameDescriptor("2048", "2048", "滑动合并相同数字",
                   "client.games.game_2048", "滑动与合并", "game_2048"),
    GameDescriptor("sokoban", "推箱子", "把箱子推到目标点",
                   "client.games.sokoban", "规划与推动", "game_sokoban"),
    GameDescriptor("zuma", "祖玛", "发射彩球匹配消除",
                   "client.games.zuma", "瞄准与连锁", "game_zuma"),
)

GAME_BY_ID = {game.id: game for game in GAMES}
VALID_GAME_IDS = frozenset(GAME_BY_ID)


def public_games() -> list[dict]:
    return [game.public_dict() for game in GAMES]
