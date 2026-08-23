# Classic Games Hub

基于 pygame 的经典小游戏合集，包含俄罗斯方块、贪吃蛇、2048、推箱子和祖玛。
项目提供统一启动器，并使用 Flask 和 SQLite 保存得分与排行榜。

## 游戏列表

- 俄罗斯方块：支持旋转、软降、硬降和等级加速
- 贪吃蛇：随得分提升等级和移动速度
- 2048：支持键盘与鼠标滑动
- 推箱子：16 个关卡，支持撤销和重置
- 祖玛：5 个关卡，每关使用不同轨道和速度

后端未启动时，游戏会自动进入离线模式。

## 环境要求

- Python 3.11
- pygame
- Flask
- requests

推荐使用 Conda：

```bash
conda env create -f environment.yml
conda activate games_env
```

也可以使用 pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 启动

同时启动后端和游戏菜单：

```bash
./run.sh
```

分别启动：

```bash
# 终端 1
./run_server.sh

# 终端 2
./run_launcher.sh
```

单独启动某个游戏：

```bash
python -m client.games.tetris
python -m client.games.snake
python -m client.games.game_2048
python -m client.games.sokoban
python -m client.games.zuma
```

## 操作说明

| 游戏 | 操作 | 其他按键 |
| --- | --- | --- |
| 俄罗斯方块 | ←/→ 移动，↑/X 顺时针旋转，Z 逆时针旋转，↓ 软降，空格硬降 | P 暂停，R 重开，Esc 返回菜单 |
| 贪吃蛇 | 方向键或 WASD | P 暂停，R 重开，Esc 返回菜单 |
| 2048 | 方向键、WASD 或鼠标滑动 | R 重开，C 在达成 2048 后继续，Esc 返回菜单 |
| 推箱子 | 方向键或 WASD | U/退格撤销，R 重置，N 跳关（练习），Esc 返回菜单 |
| 祖玛 | 鼠标瞄准，左键发射，右键或 S 切换备弹 | N/回车下一关，R 重开，P 暂停，Esc 返回菜单 |

## 测试

```bash
./run_tests.sh
```

测试脚本会启动一个临时后端，并使用独立数据库运行客户端和接口测试。

## 目录结构

```text
classic_games/
├── client/
│   ├── common/        # 公共 UI 与网络客户端
│   ├── games/         # 五款游戏
│   └── launcher.py    # 游戏菜单
├── server/
│   └── app.py         # Flask API
├── tests/
│   └── regression.py
├── environment.yml
├── requirements.txt
└── run.sh
```

运行时会自动创建以下内容，它们不会提交到仓库：

- `data/scores.db`
- `logs/server.log`

## 后端 API

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/games` | 游戏列表 |
| POST | `/api/scores` | 提交分数 |
| GET | `/api/leaderboard/<game_id>` | 排行榜 |
| GET | `/api/stats/<game_id>` | 游戏统计 |
| GET | `/api/recent` | 最近记录 |

提交分数示例：

```json
{
  "game_id": "tetris",
  "player": "franky",
  "score": 4200,
  "extra": {"lines": 38}
}
```
