# Classic Games Hub

基于 pygame 的经典小游戏合集，包含俄罗斯方块、贪吃蛇、2048、推箱子和祖玛。
项目提供统一启动器，默认直接使用本机 SQLite 保存每次游玩记录和个人最佳；
Flask API 作为可选适配器保留。

## 游戏列表

- 俄罗斯方块：支持旋转、软降、硬降和等级加速
- 贪吃蛇：随得分提升等级和移动速度
- 2048：支持键盘与鼠标滑动
- 推箱子：16 个关卡，支持撤销和重置
- 祖玛：5 个关卡，每关使用不同轨道和速度

正常游玩不需要启动后端，也不需要网络或端口。

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

启动游戏菜单：

```bash
./run.sh
```

`run.sh` 和单独启动的游戏都会使用本机记录。首次启动会只读导入旧的
`data/scores.db`，旧文件不会被修改。

如需调试 Flask API，可分别启动服务端和使用 HTTP 的启动器：

```bash
GAMES_PORT=5010 ./run_server.sh
```

```bash
GAMES_USE_HTTP=1 GAMES_API_URL=http://127.0.0.1:5010 ./run_launcher.sh
```

服务端默认只监听本机。确需局域网调试时可显式指定监听地址：

```bash
GAMES_HOST=0.0.0.0 GAMES_PORT=5010 ./run_server.sh
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

测试脚本会使用临时本地数据库，同时启动一个临时 Flask 适配器检查 API；
不会读写玩家数据或仓库里的旧成绩库。

固定 seed 的玩法、渲染、保存、资源和 SQLite 并发压力检查也可以单独运行：

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python -m tests.stress
```

## 目录结构

```text
classic_games/
├── client/
│   ├── common/        # 公共 UI 与网络客户端
│   ├── games/         # 五款游戏
│   └── launcher.py    # 游戏菜单
├── game_service/      # 游戏注册表、本地仓储和桌面数据服务
├── server/
│   └── app.py         # 可选 Flask 适配器
├── tests/
│   └── regression.py
├── environment.yml
├── requirements.txt
└── run.sh
```

默认数据库位置：

- macOS：`~/Library/Application Support/ClassicGamesHub/games.db`
- Windows：`%LOCALAPPDATA%\ClassicGamesHub\games.db`
- Linux：`$XDG_DATA_HOME/classic-games/games.db`，未设置时使用
  `~/.local/share/classic-games/games.db`

可用 `GAMES_DATA_DIR` 指定数据目录，或用 `GAMES_DB` 指定数据库文件。
同目录下的 `pending_saves.json` 只在本地事务失败时保存待重试记录。

## 可选 Flask API

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
