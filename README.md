# 经典小游戏 · Classic Games Hub

一个包含 **俄罗斯方块 / 贪吃蛇 / 2048 / 推箱子 / 祖玛** 五款经典小游戏的迷你项目，
采用 **客户端 ↔ 服务端** 分离架构：

```
┌──────────────────────┐   HTTP/JSON    ┌─────────────────────────┐
│  pygame 客户端        │ ─────────────▶ │  Flask 后端              │
│  (launcher + 5 games)│ ◀───────────── │  (REST API + SQLite)     │
└──────────────────────┘   leaderboard   └─────────────────────────┘
```

- **后端** (`server/app.py`)：Flask 暴露 `/api/games`、`/api/scores`、
  `/api/leaderboard/<game>`、`/api/stats/<game>`、`/api/recent`、`/api/health`
  等接口，用 SQLite 持久化得分。`POST /api/scores` 会实时计算名次。
- **前端** (`client/`)：基于 pygame，包含一个总启动器 `launcher.py` 和五个
  独立游戏模块。每个游戏通过 `BackendClient` (`client/common/network.py`)
  向后端提交分数、抓取 Top 10 排行榜显示在游戏结束界面。
- **共享 UI** (`client/common/ui.py`)：调色板、字体、按钮、`BaseGame`
  事件循环骨架、暂停/游戏结束弹窗（含实时后端排行榜）等。

> ⚠️ 即使后端不启动，前端也能完整运行，会进入"离线模式"，所有数据不上传。
> 这样调试和演示都很方便。

---

## 目录结构

```
classic_games/
├── README.md
├── environment.yml          # conda 环境（python=3.11 + flask + pygame + requests）
├── requirements.txt
├── run.sh                   # 一键启动（后端 + 启动器）
├── run_server.sh            # 仅后端
├── run_launcher.sh          # 仅启动器
├── data/                    # SQLite 数据库（scores.db）自动生成
├── logs/                    # 后端日志
├── server/
│   └── app.py               # Flask 服务
└── client/
    ├── launcher.py          # 启动器（游戏卡片 + 排行榜 + 玩家名输入）
    ├── common/
    │   ├── network.py       # BackendClient（HTTP 封装）
    │   └── ui.py            # BaseGame / Button / 配色 / 字体 / 排行榜
    └── games/
        ├── tetris.py        # 俄罗斯方块
        ├── snake.py         # 贪吃蛇
        ├── game_2048.py     # 2048
        ├── sokoban.py       # 推箱子（16 关，逐关推动状态求解）
        └── zuma.py          # 祖玛（5 条独立轨道、递增难度）
```

---

## 一键运行

```bash
cd ~/internship/projects/classic_games

# 首次：创建 conda 环境（python=3.11）
conda env create -f environment.yml

# 启动（同时拉起后端 + 启动器）
conda activate games_env
./run.sh
```

或者直接用根目录的 `run.sh`（它会自动 activate 环境、起后端、等 health check、再起前端）。

## 跑回归测试

```bash
conda activate games_env
./run_tests.sh
```

86 项 headless 回归检查（`SDL_VIDEODRIVER=dummy`，每项独立子进程避免 SDL re-init 冲突），覆盖：

- **Tetris**：J 在游戏中能完整跑完 4 个旋转态；新加 SRS 风格 8 方向 wall-kick（含垂直分量），靠墙/靠地也能转；4 行同时消除 = 4×4×100×level 分；每 10 行升一级并缩短自动下落间隔；硬降 1 分/格；半截方块在顶部锁定会正确判负
- **Snake**：初始速度降为 7 格/秒；每吃 5 个食物升一级并逐级加速；渲染/输入保持 60 FPS；暂停或失焦会丢弃尚未执行的转向，恢复后不会突然拐弯
- **2048**：右/下方向不再翻转整盘；左/右/上/下与目标位置精确对应；只有从棋盘内部开始的鼠标拖拽才会移动方块
- **Sokoban**：16 关全部经正向推动状态求解器验证可解；第 5 关起至少 4 个箱子、后段达到 6 个，且没有箱子开局就在目标点；玩家/箱子永远在 `floors` 内；全关卡累计总分
- **Zuma**：5 关分别使用溪谷、折返、双环、回廊、螺旋轨道，球数与链速逐关增加；自然生成的球流不会出现三连同色；递归消除会等待两段球链合拢后再触发下一重；快速点击会逐发缓存，但单次长按只发射一球
- **Launcher**：鼠标悬停卡片切换排行榜；最近游戏面板每条带游戏名；同帧内两次点击只启动一次游戏；**子游戏返回后 <3s 恢复响应**（修复了 1 分钟卡顿）；每张卡片绘制对应游戏的小图标，祖玛图标与标题至少保留 10px 空隙
- **公共**：排行榜按 rect 裁剪不会溢出；overlay 面板宽度自适应按钮总宽；结果页快速双击不会把第二击泄漏到新局；分数只提交一次；后端 API 往返；渐变背景/卡片渲染不崩
- **视觉**：启动器采用明亮的“放学后彩色玩具桌”布局；白色卡片搭配天蓝、珊瑚红、柠檬黄、薄荷绿等独立游戏色；祖玛使用 3× 超采样的连续玻璃滑道，推箱子棋盘使用明快的纸张和玩具质感

---

## 分步运行（调试用）

```bash
# Terminal 1：起后端
conda activate games_env
./run_server.sh

# Terminal 2：起启动器
conda activate games_env
./run_launcher.sh
```

---

## 体验一把某个游戏

每款游戏都能独立运行（不经过 launcher），方便调试：

```bash
conda activate games_env
python -m client.games.tetris
python -m client.games.snake
python -m client.games.game_2048
python -m client.games.sokoban
python -m client.games.zuma
```

---

## 操作说明（汇总）

> 所有游戏里 **Esc 都表示"返回启动器菜单"**，不是退出整个程序。
> 启动器本身按 Esc 才会真正退出。

| 游戏       | 移动/操作                                  | 其他                                        |
|------------|-------------------------------------------|---------------------------------------------|
| 俄罗斯方块 | ← → 左右、↑/X 顺时针、Z 逆时针、↓ 软降、空格 硬降 | P 暂停 · R 重开 · Esc 返回菜单              |
| 贪吃蛇     | ←↑→↓ / WASD                               | P 暂停 · R 重开 · Esc 返回菜单              |
| 2048       | ←↑→↓ / WASD 滑动（有滑动/合并/弹出动画）   | R 重开 · C 继续挑战(达成2048后) · Esc 返回菜单 |
| 推箱子     | ←↑→↓ / WASD                               | U/退格 撤销 · R 重置 · N 下一关 · Esc 返回菜单 |
| 祖玛       | 鼠标瞄准、左键单击发射（可快速连点）、右键/S 切换备弹 | N/回车 下一关 · R 重开 · P 暂停 · Esc 返回菜单 |

---

## 后端 API 速览

| Method | Path                          | 说明                       |
|--------|-------------------------------|----------------------------|
| GET    | `/api/health`                 | 健康检查                   |
| GET    | `/api/games`                  | 游戏元信息                 |
| POST   | `/api/scores`                 | 提交分数（返回当前排名）   |
| GET    | `/api/leaderboard/<game_id>`  | Top-N 排行榜（默认 10）    |
| GET    | `/api/stats/<game_id>`        | 场次/最高分/平均分         |
| GET    | `/api/recent?limit=20`        | 最近 N 条记录              |

`POST /api/scores` Body 示例：

```json
{"game_id": "tetris", "player": "franky", "score": 4200, "extra": {"lines": 38}}
```

返回：

```json
{"ok": true, "id": 12, "rank": 2, "submitted_from": "127.0.0.1"}
```

---

## 设计要点

1. **客户端 / 服务端解耦**：游戏逻辑跑在客户端，得分/排行榜/统计跑在后端，
   两者通过 REST 解耦。这正是面试官喜欢问的"前后端分离"小项目范本。
2. **`BaseGame` 抽象**：所有游戏继承 `client/common/ui.py:BaseGame`，统一
   处理 pygame 初始化、FPS、ESC/P 按键、游戏结束弹窗、自动向后端提交分数。
3. **优雅降级**：`BackendClient` 任何请求失败都返回 `None`/空列表，游戏不阻塞。
4. **SQLite 即开即用**：不需要额外数据库进程，`data/scores.db` 自动创建。
5. **可扩展**：在 `server/app.py:SUPPORTED_GAMES` 加一项、在
   `client/games/` 加一个文件、在 `client/launcher.py:import_game_module`
   注册一行，就能新增一款游戏。

---

## 故障排查

- **`ModuleNotFoundError: client`**：在项目根目录运行命令，不要进入子目录。
- **macOS 上 pygame 窗口起不来**：确保 `conda activate games_env` 后再跑，
  系统 Python 可能没有 pygame。
- **中文显示成方框**：`ui.py:font()` 会自动尝试 PingFang/Hiragino/微软雅黑，
  如果都没有，安装任意一款中文字体即可。
- **想清空排行榜**：`rm data/scores.db`，下次启动后端会自动重建。

### 在 Codex 中启用本地窗口控制

如果希望 Codex 直接打开并实际操作 pygame 窗口，需要配置 ChatGPT/Codex
桌面应用的 Computer Use 服务：

> 当前捆绑的 Computer Use 服务版本 `26.819.1000816` 要求 **macOS 14.4+**。
> 在 macOS 13 上，即使下列开关和权限都已开启，服务仍会在加载
> `libswiftObservation.dylib` 前直接退出；这属于系统版本兼容问题，不是权限不足。

1. 打开 **Plugins → Computer Use**，安装或启用插件。
2. 同时打开其中的 **Computer Use server** 与 **Computer Use skill** 开关，
   然后点击 **Try now**。
3. 在 macOS **系统设置 → 隐私与安全性** 中，为 **Codex Computer Use**
   开启 **屏幕录制** 与 **辅助功能**。
4. 回到应用的 **Settings → Computer use**，打开 **Any App**；首次操作
   Terminal/Python 游戏窗口时选择允许（需要时可选择 Always allow）。
5. 修改 macOS 权限后若服务仍启动失败，彻底退出并重新打开桌面应用，再开启
   一个本地 Codex 任务测试。

官方说明：<https://learn.chatgpt.com/docs/computer-use>
