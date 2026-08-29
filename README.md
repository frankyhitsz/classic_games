# Classic Games Hub

基于 pygame 的经典小游戏合集，包含俄罗斯方块、贪吃蛇、2048、推箱子和祖玛。
项目提供统一启动器，默认直接使用本机 SQLite 保存每次结算记录和个人最佳；
Flask API 作为可选适配器保留。

## 游戏列表

- 俄罗斯方块：支持旋转、软降、硬降和等级加速
- 贪吃蛇：随得分提升等级和移动速度
- 2048：支持键盘与鼠标滑动
- 推箱子：16 个关卡，支持撤销和重置
- 祖玛：5 个关卡，每关使用不同轨道和速度

正常游玩不需要启动后端，也不需要网络或端口。

启动器会保存最后使用的本机档案和昵称，显示名可以修改而不会改变档案 UUID。
“切换档案”会轮换已有的本机档案；按住 Shift 点击可新建档案并立即输入名字。
中文、日文和韩文输入使用系统输入法的组合文本事件。2048 会自动保存有效移动，并把 150 ms
内的连续棋盘变化合并为一次 latest-value 写入；终局与关闭仍立即保存
最近一个完整结算的棋盘。动画中退出不会保存“已加分但尚未合并/生成”的中间状态；slot v6 还保存
RNG state 和 move digest，重启后随机序列可继续复现。推箱子和祖玛会记录关卡进度。若自动存档读取失败或超时，2048 会保持输入门禁；
可按 T 重试、按 N 两次确认新开，或按 Esc 返回菜单，不会把读取故障当成空存档。
终局存档会恢复原棋盘和结果页，不会自动换成随机新局。同一档案已有另一个活动中的 2048
窗口时，新窗口会停止写入；按 K 会重新读取存档，再以 owner epoch、slot revision 和完整内容
hash 做原子接管，ACK 还必须返回相同 revision 与权威 value hash，或可按 Esc 返回。每个档案只有一个
2048 `autosave` 槽，因此同一档案只允许一个活动 2048 窗口。
若启动器读取最近档案失败，点击游戏会重试读取；也可以按 G 明确改用 guest。

## 环境要求

- Python 3.11
- pygame

推荐使用 Conda：

```bash
conda env create -f environment.yml
conda activate games_env
```

也可以使用 pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 启动

启动游戏菜单：

```bash
./run.sh
```

`run.sh` 和单独启动的游戏都会使用本机记录。首次启动会只读导入旧的
`data/scores.db`，旧文件不会被修改。

安装后也可以直接运行：

```bash
classic-games
```

如需调试 Flask API，可分别启动服务端和使用 HTTP 的启动器：

```bash
pip install -e '.[api]'
```

```bash
GAMES_PORT=5010 ./run_server.sh
```

```bash
GAMES_USE_HTTP=1 GAMES_API_URL=http://127.0.0.1:5010 ./run_launcher.sh
```

服务端默认只监听本机。确需局域网调试时可显式指定监听地址：

```bash
GAMES_HOST=0.0.0.0 GAMES_PORT=5010 GAMES_UNSAFE_EXPOSE=1 ./run_server.sh
```

非回环调试必须同时显式确认暴露，服务会生成并在终端显示一次 bearer token；也可提前设置
`GAMES_API_TOKEN`。远端请求使用 `Authorization: Bearer <token>` 或 `X-Games-Token`，app factory 路径
同样执行该边界。它仍只应在可信开发网络中临时使用，并由系统防火墙限制访问。正常游玩
无需开启它。HTTP 模式只用于检查成绩提交、排行和统计接口，不提供本机档案、
关卡进度和 2048 自动存档；这些能力以默认的本机模式为准。

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
| 俄罗斯方块 | ←/→ 移动，↑/X 顺时针旋转，Z 逆时针旋转，↓ 软降，空格硬降 | C 保留/交换，P 暂停，R 重开，Esc 返回菜单 |
| 贪吃蛇 | 方向键或 WASD | P 暂停，R 重开，Esc 返回菜单 |
| 2048 | 方向键、WASD 或鼠标滑动 | R 重开，C 在达成 2048 后继续，Esc 返回菜单 |
| 推箱子 | 方向键或 WASD | U/退格撤销，R 重置，K 前往已解锁最高关，N 跳关（练习），C 返回闯关，Esc 返回菜单 |
| 祖玛 | 鼠标瞄准，左键发射，右键或 S 切换备弹 | N/回车下一关，R 重开，P 暂停，Esc 返回菜单 |

## 测试

先安装开发依赖：

```bash
pip install -e '.[dev]'
```

正式发行验证使用固定的完整依赖闭包：

```bash
pip install -c constraints-release.txt -e '.[dev]'
```

```bash
./run_tests.sh
```

测试脚本会使用临时本地数据库，同时启动一个临时 Flask 适配器检查 API；
不会读写玩家数据或仓库里的旧成绩库。

固定 seed 的玩法、渲染、保存、资源和 SQLite 并发压力检查也可以单独运行：

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python -m tests.stress
```

三层检查也可以通过统一入口运行，并生成 CI 可读取的结果：

```bash
python -m tests.release release --junit release-results.xml --json release-results.json
```

release profile 还会为每个阶段设置超时，在隔离 venv 中分别安装 wheel 和 sdist，从只读工作
目录创建临时用户数据库，并生成 `release-sbom.json` 与实际安装包清单。

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
│   ├── regression.py
│   ├── test_storage_v2.py
│   ├── test_storage_v4.py
│   ├── test_storage_v5.py
│   ├── test_storage_v6.py
│   ├── test_storage_v7.py
│   ├── test_storage_v8.py
│   ├── test_storage_v9.py
│   ├── test_storage_v10.py
│   ├── test_storage_v11.py
│   ├── test_storage_v12.py
│   ├── test_storage_v13.py
│   ├── test_storage_v14.py
│   └── test_storage_v15.py
├── docs/               # 审查记录、设计决策和维护文档
├── pyproject.toml
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
同目录下的 `pending/` 保存尚未写入数据库的记录，每个请求使用一个独立 JSON
文件。文件带版本、payload hash、attempt UUID 和 revision，可由多个进程安全
写入；无法解析的文件会原样移入 `pending-quarantine/`，不会阻止游戏启动。
score 写入使用 256 个固定 stripe lock，不会再为每次成绩永久增加 lock 文件；旧版 lock 可在关闭所有
游戏后用 `classic-games-data cleanup-score-locks --apply` 清理。
单个文件上限为 64 KiB，数量或总大小异常时启动器会提示。运行中的启动器也会
发现其他实例后来写入的待保存文件。
`pending-state/` 使用每个档案或存档键一个文件的状态日志，保存最新昵称、设置、关卡进度和
自动存档。schema 3 日志冻结 ruleset，用跨进程文件锁串行同一 key，以持久 logical revision
防止晚到旧值覆盖新值；`merge_progress` 为每次贡献记录 component ID/hash 后按游戏规则单调合并，
兼容的 `set_progress` 则按完整 revision/operation ID 做 LWW，不会被误当成空 component 增量。
数据库 schema v7 在同一事务保存状态值、业务值 hash 和胜出回执；旧库升级先为既有状态建立
基线，因此 journal 已删除、业务行隔离或时钟损坏后仍能按权威值恢复。数据库解除锁定后会自动
补写，成功后仅在 hash 仍匹配时删除对应日志。损坏的状态文件移入
`pending-state-quarantine/`；v1 升级原件保存在 `pending-state-migration-backup/`。替换同一状态键前会先
持久化 reject v3 prepared marker；数据库永久拒绝新值时，即使中途退出也能恢复上一条 pending。
`pending/`、`pending-state/` 及其 quarantine/migration 目录不能是符号链接或 Windows reparse point。
完整但未发布的 score/state temp 在跨进程写入 grace window 后会提升或合并；冲突 temp 进入 quarantine，
未到期 temp 会让 complete export 明确失败，避免扫描器抢走仍在写入的文件。

查看和迁移本机数据时，可以先使用下列命令。数据维护要求先关闭启动器和游戏；后端的进程级
lease 会阻止导出遗漏尚在 worker 队列中的动作。archive v4 同时包含已提交表和 active
score/state pending，把 active replace 资格和 forensic evidence 完整度分开，并用 manifest hash 检测损坏：

```bash
python -m game_service.data_cli status
python -m game_service.data_cli export classic-games-backup.json --include-recovery
python -m game_service.data_cli inspect-archive classic-games-backup.json
python -m game_service.data_cli verify-archive classic-games-backup.json
python -m game_service.data_cli preview-import classic-games-backup.json
python -m game_service.data_cli transactions
```

0.6.0 生成的 manifest format 2 archive 可先升级，再用于精确替换。更早的 format-less v2 没有
active reject/restore inventory，升级命令会保留为 merge-only 并列出无法证明的部分，不会擅自授予
replace 权限：

```bash
python -m game_service.data_cli upgrade-archive old-v2.json upgraded-v4.json
```

导出默认只做 snapshot，不修复 orphan 或 transaction；需要先恢复再导出时显式增加
`--repair-before-export`。`inspect-archive` 做结构、manifest 和 hash 检查；`verify-archive` 使用临时空库
完成行语义、外键、pending 和 evidence 验证，二者都不打开本机数据库。`preview-import` 则会先恢复目标目录中的
未完成导入事务，再计算合并计划。导出默认不覆盖任何现有文件，也不能写到数据库、SQLite sidecar、
pending 或 recovery 路径。确认
要替换普通 archive 时才使用 `--force`。如果任何 active journal 因损坏或配额无法读取，完整导出
会失败；仅用于取证时可加 `--allow-partial`，manifest 会记录遗漏：

```bash
python -m game_service.data_cli export classic-games-backup.json --force
python -m game_service.data_cli export readable-evidence.json --allow-partial
```

导入会区分完全重复、语义冲突和 alternate identity 冲突；任一冲突都会拒绝整次 apply，而不是
静默跳过。执行前会保留数据库备份，并且必须显式确认：

```bash
python -m game_service.data_cli import classic-games-backup.json --apply
```

普通 `import` 是严格合并。要把数据库和 active pending 恢复到一个完整 archive 的状态，可使用
替换模式；它先在旁路生成并验证全新的当前 schema 数据库，再通过 authenticated transaction 原子替换。
即使目标是非 SQLite 或严重损坏的文件，也会把原始 bytes 保存为 rollback evidence；缺少完整性标记的旧
archive 不允许替换：

```bash
python -m game_service.data_cli restore-replace classic-games-backup.json --apply
```

导入先写 staging 和阶段 journal，再提交数据库并原子发布文件。任一阶段失败会自动恢复导入前的
数据库和 journal；进程在中途退出时，下一次普通客户端、HTTP 服务或维护命令会在打开数据库前
先完成回滚。损坏事务不会被猜测性恢复，可先查看和导出证据，再显式重试：

```bash
python -m game_service.data_cli transactions
python -m game_service.data_cli export-transaction .games.db.import-... import-txn.json
python -m game_service.data_cli recover-transactions --apply
```

transaction v1 没有 staged/before/rollback hash，普通启动不会自动使用这些字节。保存
`export-transaction` 证据并人工核对后，必须把命令返回的 SHA-256 一并提供，才可显式运行：

```bash
python -m game_service.data_cli recover-transactions --apply --allow-legacy-v1 \
  --evidence import-txn.json --evidence-sha256 <sha256>
```

同时出现多份未完成事务时工具不会猜测 rollback 顺序。准备期使用 `.preparing-*`；只有 journal 完整发布
后才改名为 `.import-*`。已发布目录缺 journal 时会保留 rollback evidence 并阻止启动。

事务 v2 对 SQLite rollback image、staged 和 before 文件校验 size/sha256，并直接使用同一次验证返回
的 bytes；发布时还会确认目标自 prepare 后未变化。同一目标的相同内容会去重，不同内容会拒绝整次
计划。完整协议说明见
[`docs/storage-protocol.md`](docs/storage-protocol.md)。

`--include-recovery` 中的 quarantine/backup 是证据，不是 active journal。导入时只恢复到
`imported-recovery/<archive-id>/`，不会覆盖正在使用的 `pending/` 或 `pending-state/`。

每次新游戏生成一个稳定的 attempt UUID。2048 的里程碑、最终分数和失败重放
使用同一 UUID 与递增 revision，因此晚到的旧记录不会覆盖最终分数或生成第二局。
“最近游戏”和“本机最佳”只统计已结算的 classic 记录；中途重开或返回目前不计
作一次结算。

当前规则版本为：俄罗斯方块 `tetris-assist-3`、贪吃蛇 `snake-classic-1`、2048
`2048-classic-2`、推箱子 `sokoban-campaign-2`、祖玛 `zuma-classic-2`。从旧库
导入的记录标为 `legacy-v1`，保留在历史中，但不参与当前规则的默认最佳成绩。
本机档案使用独立 UUID，显示名只负责界面展示；旧显示名身份在 schema v5 迁移时
映射为稳定 UUID。`profiles`、`settings`、`progress` 和 `save_slots` 均保存在同一
本机数据库中，不需要账号或网络。排行和最近记录显示档案的当前名字，旧结算行中
仍保留当时的名字用于恢复和审计。

数据库 schema 升级或同版本结构修复前会创建唯一备份。幂等请求回执保留 180 天；
游戏结算历史不会自动删除。旧库缺字段或包含坏行时，新库仍会启动；坏附加信息只会
丢弃元数据，不会连同基础成绩一起删除。迁移结果会在界面显示。

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
