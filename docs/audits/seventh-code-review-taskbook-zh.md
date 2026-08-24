# Classic Games Hub 第七次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 当前基线：`main` 分支 commit `e75c20656e851a6bd101691a1e1733e2b73ad4bb`（`e75c206`）
> 核心实现提交：`e0393b0eca162f0eb4ee4a6b4c6cd90c8903f957`
> 对比基线：上次审查 commit `e99e6ac28086a7d7a9eccb0f9ebb1aa47ac2442c`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 审查范围：`client/`、`game_service/`、`server/`、测试、迁移、CI、脚本和仓库治理

---

## 0. 执行摘要

本轮修复总体是有效的，而且完成度高于上一轮：

- SQLite `FULL/BUSY/READONLY/IOERR/CORRUPT` 等开始按错误码分类；
- 磁盘满、只读和 I/O 故障时，成绩 mutation 默认保留；
- 旧 pending 的 NaN、Infinity、深层嵌套和大文件路径得到限制；
- quarantine 具有降级序列化；
- `SaveEvent` 可以把 durable pending 的最终状态回写给游戏；
- 手动重试和目录扫描移到后台线程；
- pending 重放保留原始结算时间；
- schema 升至 v4，增加行不变量检查、隔离表、回执索引和分批清理；
- profile 使用独立 UUID；
- nickname、IME、settings、progress、save slots 已有实际入口；
- 2048 增加自动存档；
- 推箱子、祖玛开始写入进度；
- `ScorePolicy` 已变为枚举；
- Sokoban 0 分完整通关可以提交；
- 并列奖牌和最近记录的展示语义得到修正；
- 三平台 GitHub Actions、coverage、CHANGELOG、CONTRIBUTING 和 SECURITY 已加入。

这些都说明项目已经形成清晰、合理的本地优先主架构：

```text
pygame
  → GameDataService
      → Local read/write workers
          → SQLite
          → durable pending spool

Flask
  → 仅作为可选调试适配器
```

**不建议再推倒重来，也不建议转向账号、公网排行榜、赛季、匹配或反作弊平台。**

不过，本轮审查仍发现三项发布阻断问题：

1. **Windows 上的 request lock 使用 `os.kill(pid, 0)` 探测进程。Python 官方文档说明，Windows 上除两个控制台事件之外的信号会通过 `TerminateProcess` 无条件终止目标进程；同 request 竞争时可能杀死另一个游戏进程。**
2. **上一版本已经生成的 per-request pending 文件仍使用 spool schema 1，但其中 `profile_id` 是显示名；当前 schema 1 parser 却要求 32 位十六进制 UUID。升级后，原本有效的待保存成绩会被隔离而非自动恢复。**
3. **schema v2 的 `settings/progress/save_slots` 表结构与 v4 不兼容。v4 使用 `CREATE TABLE IF NOT EXISTS`，不会修改旧表；直接从 v2 升级可能在 profile 迁移时报 `no such column: profile_id`，或形成永久“结构不当前—备份—再次失败”循环。**

此外，新加入的本地数据功能仍有几项高优先级问题：

- 启动器在 profile 查询完成前允许启动游戏，可能把记录写入临时 UUID；
- progress 当前是覆盖式快照，新一局可把较高进度覆盖成较低进度；
- 2048 在 autosave 尚未加载完成时已经接受输入，晚到的存档会覆盖玩家刚进行的移动；
- profile、progress、settings 和 save slot 写入没有 durable spool，失败结果通常被调用方忽略；
- 2048 存档不包含 attempt UUID/revision，恢复后可能把同一次长局拆成两次 attempt；
- 终局 autosave 未清理，下一次启动可能恢复一个已经无法移动的棋盘；
- standalone 游戏和 legacy 导入可以产生没有 `profiles` 行的 profile UUID；
- 改名后榜单仍显示 attempt 中的旧昵称；
- request lock 若在 PID 元数据写完前崩溃，会留下无法自动回收的坏锁；
- schema repair 类 pending 被显示为普通 durable pending，且可能每两秒反复撞库；
- settings/progress/save slot 中损坏 JSON 缺少隔离与恢复；
- 当前 head 虽已有 CI workflow，但本次审查未取得该 head 的已完成远端状态检查结果。

本轮结论：

> **存储主链路已趋于成熟；下一步应优先修复跨版本升级和 Windows 锁，再完成 profile/progress/autosave 的一致性闭环，之后把主要投入转向测试工程、可访问性、桌面发行和五款游戏自身。**

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的检查

- 锁定当前 `main`；
- 对比 `e99e6ac..e75c206` 的全部变更；
- 阅读当前：
  - 游戏注册表；
  - mutation；
  - service contracts；
  - local backend；
  - SQLite store；
  - BaseGame；
  - launcher；
  - 2048、Sokoban、Zuma 的新增本地数据路径；
  - Tetris、Snake 的 profile 接入；
  - optional Flask；
  - HTTP debug client；
  - schema v4 tests；
  - regression/stress/CI runner；
  - workflow、README、pyproject 和项目治理文件；
- 逐项对照第六轮审查意见；
- 使用 SQLite 最小模型验证 schema v2 表不会被 `CREATE TABLE IF NOT EXISTS` 自动升级；
- 查阅 Python 3.11 官方 `os.kill()` 文档核对 Windows 行为；
- 查询当前 commit 的 GitHub 状态检查；未得到可见的 completed status。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题的主要路径和关键边界均已闭环 |
| **基本到位** | 原缺陷已关闭，但仍有跨平台或升级边界 |
| **部分到位** | 数据表/API 已有，但产品语义或可靠性尚不完整 |
| **代码路径确定** | 可从当前控制流直接确定 |
| **最小模型复现** | 用等价 SQLite/JSON 场景实际复现 |
| **官方平台证据** | 依据 Python/系统官方文档 |
| **待真机验证** | 需要 Windows/macOS/Linux 实际运行证明 |
| **产品任务** | 不是当前 Bug，而是符合单机定位的完善方向 |

### 1.3 限制

当前执行环境没有安装 `pygame` 和 `Flask`，也无法联网安装依赖，所以没有独立完整运行：

- pygame headless 回归；
- optional Flask 测试；
- 20,000 步 stress；
- 渲染 benchmark；
- wheel smoke。

仓库 `task.md` 报告：

- 107 项功能检查通过；
- 60 项存储、迁移和生命周期测试通过；
- 20,000 步固定输入；
- 五款游戏渲染 p95 约 1.1–4.5 ms；
- 本地保存 p95 约 2.1 ms；
- 持锁 async 调用 p99 约 0.034 ms；
- 240 次 SQLite 并发写入完成。

这些属于项目自测证据，本报告不将其表述为本次环境的独立复现结果。

---

## 2. 第六轮问题修复验收矩阵

| 第六轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| `SQLITE_FULL` 被当成永久错误 | **修复到位** | 使用 SQLite 基础错误码，FULL 保留 pending |
| 坏 legacy pending 可阻断启动 | **修复到位** | 严格 JSON constant、大小、深度、节点和降级 quarantine |
| durable pending 最终状态不回写 | **修复到位** | `SaveEvent/get_save_status` 已接入 BaseGame |
| 手动 retry 同步扫目录 | **修复到位** | 返回 Future，scan 在 read worker |
| 外部 legacy import 与 schema 耦合 | **基本到位** | schema current 不再依赖外部 marker；失败有退避 |
| 移动旧库后重复导入 | **修复到位** | 内容 hash + semantic row fingerprint |
| pending 重放改变结算时间 | **修复到位** | envelope `created_at` 进入 attempt `finished_at` |
| current schema 不检查坏行 | **基本到位** | invalid rows 隔离 + trigger；仍有旧表结构升级缺口 |
| SQLite 错误依赖英文消息 | **修复到位** | error code 为主，消息 fallback |
| legacy extra 无大小/深度限制 | **修复到位** | raw bytes、depth、nodes、string 限制 |
| 单个 spool OSError 中断扫描 | **修复到位** | 每文件隔离处理 |
| store reopen 高频失败 | **基本到位** | transient/repair/permanent + backoff |
| receipt 无 expires index | **修复到位** | 索引和 500 条分批删除 |
| request lock 固定时间判 stale | **产生跨平台新问题** | 改 PID 探测，但 `os.kill(pid,0)` 不可用于 Windows |
| score policy 普通字符串 | **修复到位** | Enum + catalog contract |
| Sokoban 0 分不提交 | **修复到位** | `None` sentinel |
| tie medal/recent 奖牌错误 | **修复到位** | medal 按真实 rank；recent 非竞争列表 |
| profile/IME 未实现 | **数据层到位** | UUID profile、nickname、IME 已有；存在启动竞态与归属问题 |
| BaseGame/2048 双保存控制流 | **明显改善** | 2048 复用 BaseGame 提交；仍有独立 queued revision 逻辑 |
| 启动同步迁移/scan | **明显改善** | deferred store + background scan；legacy shared file 仍同步迁移 |
| Flask query/JSON 边界 | **修复到位** | endpoint-specific query + JSON 404/405/500 |
| 工程门禁不完整 | **部分到位** | CI/coverage/治理文件已加入；无可见 head 状态、LICENSE 和阈值 |

---

## 3. 发布阻断问题

## 3.1 CG7-F01：Windows request lock 的进程探测可能终止另一个进程

- **优先级**：P0
- **证据**：代码路径确定 + Python 官方平台证据
- **位置**：
  - `game_service/local_backend.py::_request_lock`
  - `game_service/local_backend.py::_pid_exists`

当前：

```python
os.kill(pid, 0)
```

这是 Unix 常见的“进程是否存在”探测方式，但 Python 3.11 官方文档明确说明：

> Windows 上除 `CTRL_C_EVENT` 和 `CTRL_BREAK_EVENT` 外，其他 signal 值会通过 `TerminateProcess` 无条件终止目标进程。

`0` 并不是 Windows 的安全存在性探测。

### 可能场景

1. 两个本地游戏进程同时操作同一个 request；
2. 进程 A 已创建 `.request.lock`；
3. 进程 B 看到锁文件，调用 `_pid_exists(A)`；
4. Windows `os.kill(A, 0)` 可能终止进程 A；
5. B 仍可能认为 owner 存在并等待超时；
6. 最终既破坏进程，也没有正确接管锁。

现有 32 进程测试使用不同 request ID，不会触发这个竞争场景。

### 修复要求

二选一：

#### 方案 A：使用真正跨平台文件锁

- POSIX：`fcntl.flock`
- Windows：`msvcrt.locking` 或可靠封装
- 可考虑小型依赖 `portalocker`，但需评估核心依赖成本

#### 方案 B：避免锁文件进程探测

- 使用 spool SQLite；
- 或 request directory + atomic claim；
- 不对其他 PID 发送任何信号。

### 验收

- Windows 两个进程竞争同一 request，不结束任何进程；
- owner 正常退出、崩溃、强制终止均可恢复；
- PID 重用不会错误接管；
- malformed lock 不形成永久死锁。

---

## 3.2 CG7-F02：上一版本的有效 per-request pending 会被当前版本隔离

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - 旧版 `AttemptContext.for_game`
  - 当前 `normalize_score_mutation`
  - 当前 `PendingSaveEnvelope.parse`
  - `SPOOL_SCHEMA_VERSION = 1`

上一版本已经使用逐 request spool，payload 中：

```text
profile_id = 玩家显示名
```

当前版本要求：

```text
profile_id = 32 位十六进制 UUID
```

但 spool schema version 仍然是 1，没有迁移分支。

### 升级结果

```text
旧 pending 文件
→ envelope schema 1 被视为当前 schema
→ payload normalize
→ invalid_profile_id
→ 文件进入 quarantine
→ 原成绩不会自动写入数据库
```

数据文件没有物理删除，但升级用户需要人工理解 quarantine 才能恢复；这与“pending 跨版本可靠恢复”的产品承诺不一致。

### 修复要求

- 将 spool schema 升为 2；
- schema 1 parser 使用旧语义；
- 将显示名 profile 映射为：
  ```python
  uuid5(NAMESPACE_URL, f"classic-games-local-profile:{display_name}")
  ```
- 重算 canonical payload hash；
- 以新 envelope 原子替换旧文件；
- 保留原文件备份或迁移报告；
- 增加真实 e99 spool fixture。

---

## 3.3 CG7-F03：schema v2 无法安全直接升级到 schema v4

- **优先级**：P0
- **证据**：代码路径确定 + SQLite 最小模型复现
- **位置**：
  - schema v2 的 `settings/progress/save_slots`
  - schema v4 `CREATE TABLE IF NOT EXISTS`
  - `_migrate_profiles`
  - `_schema_is_current`

schema v2 表结构：

```text
settings(key, value_json, updated_at)

progress(
  profile_id, game_id,
  ruleset_version, progress_json, updated_at
)

save_slots(
  profile_id, game_id, slot,
  ruleset_version, state_json, updated_at
)
```

schema v4 表结构：

```text
settings(profile_id, key, value_json, updated_at)

progress(profile_id, game_id, key, value_json, updated_at)

save_slots(profile_id, game_id, slot_id, state_json,
           ruleset_version, updated_at)
```

SQLite：

```sql
CREATE TABLE IF NOT EXISTS settings(...)
```

不会为已有表补列，也不会改主键。

### 已验证的最小行为

对 v2 `settings` 执行 v4 `CREATE TABLE IF NOT EXISTS` 后，列仍然只有：

```text
key, value_json, updated_at
```

之后：

```sql
UPDATE settings SET profile_id=...
```

会报：

```text
OperationalError: no such column: profile_id
```

### 后果

- 有旧 attempt/profile 时，迁移可能立即失败；
- 无旧 profile 时，数据库可能被写成 schema version 4，但三张表仍是旧结构；
- 下次启动 `_schema_is_current()` 继续失败；
- 反复创建备份并重复修复；
- settings/progress/save slot API 永远不可用。

### 修复要求

建立显式迁移：

```text
v1 → v2
v2 → v3
v3 → v4
```

对三张表采用：

```sql
ALTER TABLE old RENAME TO legacy_...
CREATE TABLE new ...
转换并 INSERT
校验
DROP/保留旧表
```

转换规则必须明确：

- v2 全局 setting 映射到哪个 profile；
- v2 progress 的 `progress_json` 如何映射到 `key/value_json`；
- `slot` 如何改为 `slot_id`；
- 冲突时保留双方数据，不静默覆盖。

### 验收

必须提供真实结构 fixture：

```text
schema 0/1/2/3 → 4
```

并验证：

- 分数不丢；
- settings/progress/save slot 可读；
- 迁移失败回滚；
- 原数据库备份存在；
- 重复初始化幂等。

---

## 4. 高优先级残留与新增问题

## 4.1 CG7-F04：profile 尚未解析完成时即可启动游戏

- **优先级**：P1
- **位置**：`client/launcher.py`

启动器先生成随机 profile UUID，再异步读取 `last_profile()`。卡片立即可点击。

### 场景

1. 磁盘较慢或数据库正在迁移；
2. 玩家立即点击游戏；
3. 游戏使用临时随机 UUID；
4. score/progress/save slot 写入临时 UUID；
5. 玩家返回菜单后，异步结果才把 launcher 切回真实 last profile；
6. 刚完成的记录不属于当前可见档案。

### 修复要求

- profile 未 resolved 时：
  - 禁止启动卡片；或
  - 先原子 `ensure_profile()` 再启动；
- UI 显示“正在载入本机档案”；
- profile save Future 失败时不得静默继续；
- 游戏启动参数必须来自一个已确认存在的 profile row。

---

## 4.2 CG7-F05：旧 pending 的 profile 兼容与 schema v4 profile 迁移没有统一身份服务

- **优先级**：P1
- **性质**：F02 的架构根因

当前 profile UUID 算法散落在：

- mutation；
- AttemptContext；
- profile migration；
- legacy row import。

应提取：

```python
ProfileIdentity.from_legacy_name(name)
ProfileIdentity.validate_uuid(value)
```

否则后续再次变更时容易出现相同升级断层。

---

## 4.3 CG7-F06：standalone 游戏和 legacy 导入可产生没有 `profiles` 行的记录

- **优先级**：P1
- **位置**：
  - BaseGame 默认 profile；
  - `_insert_legacy_rows`
  - profiles 无外键

`python -m client.games.tetris` 不经过 launcher 的 `ensure_profile()`。

legacy import 发生在 `_migrate_profiles()` 之后，也不会为新导入 profile UUID 创建 profile row。

### 影响

- leaderboard 有成绩，但 `list_profiles/last_profile` 没有该档案；
- settings/progress/save slot 可以引用不存在的 profile；
- 后续 profile UI 无法完整列出历史归属。

### 修复要求

- `record_mutation` 在同一事务中 `INSERT OR IGNORE profiles`；
- legacy import 同步创建 profile；
- settings/progress/save_slots 增加 FK；
- standalone 入口在启动时确保默认 profile。

---

## 4.4 CG7-F07：修改昵称后，旧榜单记录仍显示旧名字

- **优先级**：P1/P2
- **位置**：
  - attempts 保存 `player`
  - leaderboard/recent 查询 attempts.player
  - profiles.display_name

当前 UUID 身份稳定，但显示名仍是 attempt 快照。

### 产品决策

二选一：

1. **显示当前档案名**：查询 join profiles；
2. **显示当时昵称**：UI 明确称“结算时昵称”。

对于家庭本机档案，建议：

- 默认榜单显示当前 profile display name；
- 历史详情可显示当时昵称。

---

## 4.5 CG7-F08：progress 是覆盖式快照，会发生进度回退

- **优先级**：P1
- **位置**：
  - `LocalGameStore.set_progress`
  - `Sokoban._check_win`
  - `Zuma.update`

### Sokoban

完整进度 10 关后开始新 run，只完成第 1 关，会写：

```text
completed_levels = [0]
unlocked_level = 2
```

覆盖之前更高进度。

练习跳关也可能写入 campaign key。

### Zuma

完成全部五关后，新 run 清第 1 关会写：

```text
completed_all = false
unlocked_level = 2
highest_score = 当前低分
```

覆盖之前的完整通关。

### 修复要求

- 进度仓储使用 game-specific merge：
  - unlocked level 取 max；
  - completed set 取 union；
  - best score 取 max；
  - completed_all 只允许 false→true；
- practice/campaign 使用不同 key 或 mode；
- progress 加 `ruleset_version`；
- 游戏启动时读取并应用进度；
- 写失败可观察、可重试。

---

## 4.6 CG7-F09：profile/settings/progress/save-slot 写入没有 durable 保障

- **优先级**：P1
- **位置**：
  - `ensure_profile_async`
  - `set_setting_async`
  - `set_progress_async`
  - `save_slot_async`
  - 游戏调用方

这些写入走 worker，但不进入 durable spool。

调用方通常：

```python
try:
    future = write_async(...)
except:
    pass
```

不会轮询 Future，也不会提示失败。

### 后果

- 昵称可能没保存；
- 进度可能回退或丢失；
- 2048 autosave 可能丢失；
- 磁盘满/锁冲突后不会自动补写。

### 修复要求

建立通用本地 mutation journal：

```text
score
profile
setting
progress
save_slot
```

或至少为非成绩数据提供：

- typed Future result；
- retry queue；
- last-write-wins key；
- 退出前 flush；
- UI 错误状态。

---

## 4.7 CG7-F10：2048 存档尚未加载完成时已经接受输入

- **优先级**：P1
- **位置**：`Game2048.__init__`、事件循环、`_poll_slot_load`

BaseGame 每帧顺序：

```text
处理事件
→ update()
```

而存档是在 `update()` 中才应用。

第一帧用户输入可能先改变新棋盘，随后异步存档覆盖整个棋盘。

### 修复要求

- 引入：
  ```text
  loading_save
  ready
  load_failed
  ```
- `loading_save` 时不接受移动；
- 显示轻量加载提示；
- 加载完成后再启用输入；
- 用户选择“新游戏”时取消/忽略旧存档；
- 增加 first-frame input race 测试。

---

## 4.8 CG7-F11：2048 恢复存档后没有恢复 attempt 身份

- **优先级**：P1
- **位置**：2048 autosave state

存档只包含：

```text
version
score
won
grid
```

不包含：

```text
attempt_uuid
revision
submission_id/confirmed score
```

如果玩家已达成 2048 并保存过 milestone，然后退出：

- 恢复棋盘会创建新 attempt UUID；
- 最终 gameover 形成另一条 attempt；
- 一次长局被拆成两条历史记录。

### 修复要求

存档增加：

```text
attempt_uuid
revision
confirmed_score
won_announced
ruleset_version
```

并验证：

- 旧 slot migration；
- attempt 与 profile/game/ruleset 匹配；
- final revision 不重复创建。

---

## 4.9 CG7-F12：2048 终局存档没有清除或标记终态

- **优先级**：P1
- **位置**：gameover/win/reset autosave

gameover 后最后一个 slot 仍可能是一个无法移动的“playing board”。

下次启动：

- 恢复死局；
- 第一次输入才重新触发 gameover；
- 可能形成新 attempt 或重复结算。

### 修复要求

- slot 加 `state=playing/won/gameover`；
- gameover commit 后删除 autosave，或显示“查看终局/开始新局”；
- reset 原子覆盖旧 slot；
- 终局保存和 attempt 提交顺序明确。

---

## 4.10 CG7-F13：存档加载校验仍不足

- **优先级**：P1/P2

当前校验了：

- 4×4；
- 非负 2 的幂；
- score 非负；
- ruleset。

仍缺：

- score 最大值；
- `won=True` 与最大方块一致；
- tile 数量上限；
- 状态和 attempt context；
- JSON row 损坏隔离；
- grid 与 score 的合理性检查。

本地文件不是不可信网络输入，但损坏存档不应静默覆盖新棋盘。

---

## 4.11 CG7-F14：request lock 在 PID 元数据写入前崩溃会永久阻塞

- **优先级**：P1
- **位置**：`_request_lock`

流程：

```text
O_EXCL 创建锁文件
→ 写 PID JSON
```

若进程在两步之间终止，会留下空文件。

其他进程解析失败后只等待，不会基于 owner 或安全规则回收；每次均超时。

### 修复要求

- 使用 OS 锁；
- 或先写完整 temp，再以目录/原子机制取得所有权；
- malformed lock 进入安全恢复；
- Windows/POSIX 都要有 crash-before-metadata 测试。

---

## 4.12 CG7-F15：legacy shared pending 迁移不是“全成功后提交”

- **优先级**：P1
- **位置**：`_migrate_legacy_file`

循环结束后，无论某项是否因：

- 磁盘满；
- permission；
- MemoryError；
- quarantine 写失败；

都会尝试将原文件改名为 `.migrated-*`。

原数据通常仍在 migrated 文件中，但不会自动重试，用户也未必知道它不是成功迁移。

### 修复要求

- 每个 item 记录结果；
- 只有全部成功/明确隔离后才标 migrated；
- transient failure 保留原文件；
- 迁移报告列出：
  - migrated
  - quarantined
  - retry pending
  - failed to preserve。

---

## 4.13 CG7-F16：schema repair pending 的状态和重试节奏不准确

- **优先级**：P1
- **位置**：`StorageFailure`、`_save_mutation`、`_retry_all`

`SCHEMA_REPAIR_REQUIRED`：

```text
retryable = false
quarantine = false
```

但 durable 时仍被映射为 `DURABLE_PENDING`。

同时 `_retry_all` 只为 retryable failure 设置退避，因此这种记录可被每两秒再次提交。

### 修复要求

- state 映射按 `kind`：
  - BUSY/FULL/READONLY → durable pending/recovery required；
  - SCHEMA_REPAIR_REQUIRED → recovery required；
  - CORRUPT/CONSTRAINT → quarantined；
- repair-required 不自动高频重试；
- UI 显示“需要修复数据库”，而不是普通待保存。

---

## 4.14 CG7-F17：系统时间回拨可能把合法 pending 变成永久请求错误

- **优先级**：P1
- **位置**：`_validate_occurred_at`

允许：

```text
occurred_at <= now + 300
```

若保存时系统时钟错误地快了很多，之后纠正时间，再重放：

```text
invalid_occurred_at
→ StoreError
→ 视作永久请求错误
→ pending 被移除
```

### 修复要求

内部本地 timestamp 不应导致删除成绩：

- 超前时 clamp；
- 同时保存 monotonic offset 或 `recorded_at`;
- 标记 `clock_adjusted=true`；
- 将时间异常作为元数据问题，而非成绩请求非法。

---

## 4.15 CG7-F18：profiles/settings/progress/save_slots 缺少外键和损坏数据恢复

- **优先级**：P1

当前没有 FK，因此可产生 orphan row。

getter 直接：

```python
json.loads(value_json)
```

损坏 JSON 会抛异常。

### 修复要求

- `FOREIGN KEY(profile_id) REFERENCES profiles`;
- profile 删除策略明确；
- value JSON 有 quarantine/默认恢复；
- 每表增加结构版本；
- progress/save slot 加 ruleset/version；
- DB migration fixture 覆盖损坏 row。

---

## 4.16 CG7-F19：当前 profile 改名和家庭成员切换仍缺少完整 UI

- **优先级**：P2，产品任务

数据层已有 list/ensure profile，但 launcher 只恢复“最后一个 profile”，没有：

- 新建档案；
- 切换档案；
- 重命名；
- 删除；
- 合并旧显示名；
- 查看每档案进度。

这不需要账号系统，只需要本机档案页。

---

## 4.17 CG7-F20：HTTP 调试模式没有等价 profile/progress/save-slot 能力

- **优先级**：P2

HTTP client 的：

```text
poll_save_events = []
get_save_status = None
```

也没有 profile/settings/progress/slot API。

显式使用 `GAMES_USE_HTTP=1` 时，新本地功能会退化。

建议：

- README 明确 HTTP 模式只测试成绩 API；
- 或补齐调试接口；
- 不要让 optional HTTP 反向限制默认桌面架构。

---

## 4.18 CG7-F21：CI 已加入，但远端质量门禁尚未闭环

- **优先级**：P1/P2

优点：

- Linux/macOS/Windows；
- Ruff；
- storage coverage；
- regression；
- stress；
- artifact upload。

剩余问题：

1. 当前 head 未取得本次审查可见的 completed status；
2. 无 branch protection/required checks 证据；
3. coverage 只包裹 storage unittest，游戏 regression/stress 不进入 coverage；
4. 无 coverage 最低阈值；
5. pytest 已安装但主套件仍是 unittest + 自定义 runner；
6. 性能 p95 在共享 runner 上可能波动；
7. 无 mypy、Hypothesis、依赖审计；
8. 无失败截图和 server log artifact；
9. Python 声明 `>=3.11`，CI 只测试 3.11；
10. 无 LICENSE。

---

## 4.19 CG7-F22：仓库根目录仍堆放多轮审查材料

- **优先级**：P2

建议移动为：

```text
docs/audits/
docs/adr/
docs/development/
```

根目录保留：

- README
- LICENSE
- CHANGELOG
- CONTRIBUTING
- SECURITY
- pyproject
- 运行入口

---

## 5. 五款游戏专项评价

## 5.1 Tetris

上一轮输入和大 dt 修复未见回退。

后续适合单机体验：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- 游戏内规则页；
- RNG 注入；
- 静态网格缓存；
- 同义物理键按“逻辑动作边沿”处理；
- 继续明确它是自定义辅助旋转，不宣称严格标准 SRS。

## 5.2 Snake

已有修复保持：

- 双转向队列；
- 长停顿保护；
- 吃食物后重新计算间隔。

后续：

- 速度选择；
- 穿墙；
- 障碍；
- 双人同屏；
- RNG 注入；
- 色弱纹理；
- 棋盘缓存。

## 5.3 2048

本轮新增 autosave 是正确方向，但先修：

- load race；
- durable slot；
- attempt context；
- terminal slot；
- corruption recovery。

之后再做：

- 撤销；
- 多存档槽；
- 棋盘尺寸；
- 本地最高分详情；
- RNG 注入；
- 背景缓存。

## 5.4 Sokoban

核心计分与 0 分通关已正确。

先修：

- progress merge；
- practice/campaign 分离；
- progress load；
- durable progress；
- ruleset dimension。

后续：

- 选关和解锁；
- 最少移动/推动；
- 是否使用撤销；
- 星级；
- 死锁检测；
- 提示；
- 地图闭合与可达；
- XSB 导入；
- 关卡编辑器；
- 固定逻辑窗口。

## 5.5 Zuma

核心连锁和碰撞修复保持。

先修：

- progress merge；
- practice/campaign 分离；
- progress load；
- durable progress。

后续：

- reaction FSM；
- 多重反应属性测试；
- RNG 注入；
- `incoming` 改 deque；
- 训练和选关；
- 色弱符号；
- 原创道具；
- 轨道编辑器。

---

## 6. 明确非目标

不建议建设：

- 注册登录；
- 云端账号；
- 公网排行榜；
- 匹配；
- 赛季；
- 实时联机；
- 服务端权威判定；
- 反作弊；
- replay 审核；
- 在线商城；
- 强制联网；
- 默认遥测；
- 复杂权限系统。

Flask 继续定位为：

- 教学；
- 调试；
- 本机 API 示例；
- 可选数据查看适配器。

---

## 7. 推荐增量架构

现有主架构保留，只补齐以下边界：

```text
Launcher
  ├── ProfileController
  │     ├── resolved profile UUID
  │     ├── rename/switch
  │     └── startup gate
  │
  └── Game
        ├── AttemptContext
        ├── AttemptSaveController
        ├── ProgressController
        └── SaveSlotController

GameDataService
  ├── Score mutation journal
  ├── Keyed local-state journal
  ├── SaveEvent
  ├── Profile repository
  ├── Progress repository
  ├── Save slot repository
  └── Settings repository

SQLite + durable spool
```

### 7.1 Keyed local-state journal

非成绩写入应使用：

```text
kind
profile_id
game_id
key/slot
ruleset_version
revision
payload
```

同 key：

- profile/display name：last-write-wins；
- settings：last-write-wins；
- save slot：last-write-wins；
- progress：game-specific monotonic merge。

### 7.2 ProfileController

必须保证：

```text
profile resolved
→ profile row exists
→ 才允许启动游戏
```

---

## 8. 完整优化任务清单

### 优先级

- **P0**：可能杀死进程、丢失待保存成绩或使升级失败；发布阻断。
- **P1**：本地数据可靠性、迁移和正式发行基础。
- **P2**：输入、UI、性能、可访问性和维护性。
- **P3**：玩法内容和桌面发行。
- **S/M/L/XL**：相对工作量。

---

## 8.1 P0：跨平台锁和升级安全

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG7-P0-01 | 移除 Windows `os.kill(pid,0)` | process probe abstraction | Windows 不向 owner 发送终止信号 | S |
| CG7-P0-02 | 实现跨平台 request lock | flock/msvcrt/portalocker 或替代设计 | 同 request 双进程竞争不杀进程、不丢数据 | L |
| CG7-P0-03 | malformed/orphan lock 恢复 | lock protocol v2 | crash-before-metadata 可恢复 | M |
| CG7-P0-04 | spool schema 升级为 2 | versioned parser | 新旧 profile 语义可区分 | M |
| CG7-P0-05 | 迁移 e99 per-request spool | v1→v2 migrator | 显示名 profile 转 UUID，成绩自动恢复 | L |
| CG7-P0-06 | 保留 spool 迁移原件 | backup/report | 迁移失败不把文件误标完成 | M |
| CG7-P0-07 | 建立 schema v2→v4 迁移 | explicit migration | settings/progress/save slots 正确转换 | XL |
| CG7-P0-08 | schema 0/1/2/3 fixtures | migration tests | 所有已发布结构可直升 v4 | L |
| CG7-P0-09 | 迁移事务与回滚 | failure injection | 任一步失败，原库可恢复 | M |
| CG7-P0-10 | Windows 同 request 并发测试 | process integration | owner 存活，最终一份 canonical payload | M |
| CG7-P0-11 | P0 真实升级包测试 | old data bundle | 旧 DB + 旧 pending 一次升级通过 | L |
| CG7-P0-12 | 发布门禁 | required CI job | P0 失败禁止 tag/release | S |

---

## 8.2 P1：本地数据一致性和工程基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG7-P1-01 | profile startup gate | ProfileController | profile resolved 前不能启动游戏 | M |
| CG7-P1-02 | profile ensure 与游戏启动原子化 | launch transaction | 不产生临时 orphan UUID | M |
| CG7-P1-03 | attempt 自动创建 profile row | store transaction | standalone score 也出现在 profiles | S |
| CG7-P1-04 | legacy import 创建 profile | migration fix | 导入记录有可见档案归属 | S |
| CG7-P1-05 | profiles/child tables 外键 | schema migration | 无 orphan settings/progress/slots | L |
| CG7-P1-06 | 定义 nickname 显示策略 | ADR + query | 当前名/结算时名语义明确 | S |
| CG7-P1-07 | leaderboard join profile | optional query | 改名后显示符合 ADR | M |
| CG7-P1-08 | progress 加 ruleset | schema/API | 规则升级不复用旧进度 | M |
| CG7-P1-09 | Sokoban progress merge | repository method | completed union、unlock max、best max | L |
| CG7-P1-10 | Zuma progress merge | repository method | completed_all 单调、unlock/best max | L |
| CG7-P1-11 | practice/campaign 分键 | mode-aware progress | 练习不覆盖闯关 | M |
| CG7-P1-12 | 游戏启动加载 progress | controller | UI 真正使用保存进度 | L |
| CG7-P1-13 | durable profile write | keyed journal | 满盘/锁冲突后可补写 | L |
| CG7-P1-14 | durable settings write | keyed journal | last-write-wins，退出可 flush | L |
| CG7-P1-15 | durable progress write | keyed journal | 失败可见、自动重试 | L |
| CG7-P1-16 | durable save-slot write | keyed journal | 2048 最新棋盘不静默丢失 | L |
| CG7-P1-17 | local-state SaveEvent | typed events | UI 可显示进度/存档保存失败 | M |
| CG7-P1-18 | 2048 load gate | loading state | 首帧输入不被晚到存档覆盖 | M |
| CG7-P1-19 | 2048 slot 保存 attempt context | slot schema v2 | resume 继续同一 attempt | L |
| CG7-P1-20 | 2048 terminal slot policy | state/delete | gameover 不恢复为 playing | M |
| CG7-P1-21 | 2048 slot corruption recovery | quarantine/default | 坏存档不覆盖新棋盘 | M |
| CG7-P1-22 | slot migration v1→v2 | migrator | 已有 autosave 可升级 | M |
| CG7-P1-23 | request lock protocol tests | crash/pid reuse | malformed、PID 重用均安全 | L |
| CG7-P1-24 | legacy shared pending 原子迁移 | migration ledger | transient failure 保留原文件 | L |
| CG7-P1-25 | storage state 分类修正 | state mapper | schema repair 不显示普通 pending | M |
| CG7-P1-26 | repair-required 退避 | scheduler | 不每 2 秒固定撞库 | S |
| CG7-P1-27 | clock correction policy | timestamp normalization | 时钟回拨不删除成绩 | M |
| CG7-P1-28 | settings JSON recovery | typed read | 损坏值隔离并回默认 | M |
| CG7-P1-29 | progress JSON recovery | typed read | 损坏不阻断游戏 | M |
| CG7-P1-30 | save-slot JSON recovery | typed read | 损坏 slot 可导出/清除 | M |
| CG7-P1-31 | profile/input 统一 Unicode 校验 | shared validator | UI/API/store 规则一致 | S |
| CG7-P1-32 | SaveEvent 状态持久/可重建 | status strategy | 内存淘汰不使 pending 永久停留 | M |
| CG7-P1-33 | GameDataService 补齐本地数据协议 | Protocol | profile/progress/slot/settings 可类型检查 | M |
| CG7-P1-34 | HTTP 模式能力声明 | README/feature flags | 调试模式降级明确 | S |
| CG7-P1-35 | schema trigger 版本验证 | exact SQL/version | 旧 trigger 不误判 current | M |
| CG7-P1-36 | 数据导出/导入 | service | 原子、校验、可回滚 | L |
| CG7-P1-37 | quarantine 导出 | recovery API | 用户可保存原始问题数据 | M |
| CG7-P1-38 | 数据清理策略 | retention | 不误删 pending 和当前存档 | M |
| CG7-P1-39 | 结构化日志 | rotating logs | migration/worker/game 有 traceback | M |
| CG7-P1-40 | 恢复页面 | UI | 可查看 DB/spool/quarantine 状态 | L |
| CG7-P1-41 | pytest 迁移 | fixtures/markers | 任一测试可单独运行 | XL |
| CG7-P1-42 | core-only 测试 | no-api CI | 无 Flask/requests 可验证核心 | M |
| CG7-P1-43 | CI required checks | branch protection | main 合并必须通过三平台 | S |
| CG7-P1-44 | coverage 全套件 | coverage config | regression/game logic 进入覆盖率 | M |
| CG7-P1-45 | coverage 阈值 | gate | 核心规则 ≥90%，全项目 ≥80% | S |
| CG7-P1-46 | JUnit/失败日志/截图 | CI artifacts | 失败可诊断 | M |
| CG7-P1-47 | Python 版本矩阵 | 3.11/3.12/3.13 或限制 | 声明与测试一致 | M |
| CG7-P1-48 | 类型检查 | mypy/pyright | service/store/client 边界通过 | M |
| CG7-P1-49 | 属性测试 | Hypothesis | 2048/Tetris/Sokoban/Zuma 不变量 | L |
| CG7-P1-50 | 依赖锁定 | constraints/lock | 发布构建可复现 | M |
| CG7-P1-51 | LICENSE/NOTICE | legal files | 代码与素材许可明确 | M |
| CG7-P1-52 | CHANGELOG 与 schema/ruleset 治理 | release policy | 每次兼容变化可追踪 | S |
| CG7-P1-53 | 文档目录整理 | `docs/audits/adr/dev` | 根目录保持清晰 | S |

---

## 8.3 P2：单机体验、维护性和性能

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG7-P2-01 | 本机档案选择页 | profiles UI | 新建、切换、重命名 | L |
| CG7-P2-02 | 家庭成员档案 | local-only profiles | 无账号、进度分离 | M |
| CG7-P2-03 | GameState Enum | state model | 无散落魔法字符串 | L |
| CG7-P2-04 | 统一 AttemptSaveController | common controller | BaseGame/2048 不再平行维护 | XL |
| CG7-P2-05 | InputManager | action map | 五款游戏统一输入状态 | L |
| CG7-P2-06 | 完善 IME 光标/选择 | text widget | 组合输入、退格、焦点稳定 | M |
| CG7-P2-07 | 键位重映射 | binding UI | 冲突检测、恢复默认 | L |
| CG7-P2-08 | 键盘菜单导航 | focus model | 不用鼠标可完整操作 | L |
| CG7-P2-09 | 手柄支持 | controller layer | launcher/五款游戏可用 | L |
| CG7-P2-10 | 音频系统 | BGM/SFX | 无设备不崩，音量持久 | L |
| CG7-P2-11 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG7-P2-12 | 高 DPI | DPI handling | 字体和图形清晰 | M |
| CG7-P2-13 | 字体 fallback | licensed font chain | 缺系统 CJK 字体仍可读 | M |
| CG7-P2-14 | 色弱符号 | shape/pattern | 颜色不是唯一信息 | L |
| CG7-P2-15 | 高对比/降低动态 | accessibility | 脉冲、抖动可关闭 | L |
| CG7-P2-16 | Clock/RNG 注入 | deterministic services | 相同 seed+输入可复现 | L |
| CG7-P2-17 | 纯规则 Engine | gradual extraction | 核心测试无需 SDL | XL |
| CG7-P2-18 | launcher 拆分 | app/state/render/data | `main()` 职责清晰 | L |
| CG7-P2-19 | 首页改为本地进度中心 | best/recent/progress/continue | 不以 Top 10 为核心 | L |
| CG7-P2-20 | 静态 Surface 缓存 | profiler-driven | 有尺寸/主题失效 | L |
| CG7-P2-21 | Zuma reaction FSM | explicit model | 重叠反应可属性测试 | L |
| CG7-P2-22 | 可重复 benchmark | CLI | 带环境、版本和 seed | M |
| CG7-P2-23 | soak 测试 | 30–60 分钟 | 线程/FD/内存稳定 | M |
| CG7-P2-24 | 游戏内规则页 | controls/rules/version | ruleset 可查看 | M |
| CG7-P2-25 | 崩溃恢复页 | crash UI | 返回菜单并显示日志 | M |

---

## 8.4 P3：游戏内容和桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG7-P3-01 | Tetris 7-bag | comfort mode | 独立 ruleset | M |
| CG7-P3-02 | Tetris ghost/hold/lock delay | comfort features | 规则测试完整 | L |
| CG7-P3-03 | Snake 速度/穿墙/障碍 | local modes | 模式最佳分开 | L |
| CG7-P3-04 | Snake 双人同屏 | local multiplayer | 不依赖网络 | L |
| CG7-P3-05 | 2048 撤销 | undo model | 存档和 attempt 语义清晰 | L |
| CG7-P3-06 | 2048 多存档槽 | save UI | 可继续/删除/查看时间 | L |
| CG7-P3-07 | 2048 棋盘尺寸 | modes | ruleset 分离 | M |
| CG7-P3-08 | Sokoban 选关/解锁 | progress UI | 练习与 campaign 分开 | L |
| CG7-P3-09 | Sokoban 星级/最佳推动 | metrics | 规则明确 | M |
| CG7-P3-10 | Sokoban 死锁/提示 | analysis | 提示可关闭 | XL |
| CG7-P3-11 | Sokoban 编辑器 | XSB import/export | 地图验证和预览 | L |
| CG7-P3-12 | Zuma 训练/选关 | practice mode | 不混入完整通关 | L |
| CG7-P3-13 | Zuma 色弱辅助 | symbols | 球色可独立辨认 | M |
| CG7-P3-14 | Zuma 原创道具/轨道 | content | 每项有确定性测试 | XL |
| CG7-P3-15 | Zuma 轨道编辑器 | path tool | 合法、版本化、可预览 | L |
| CG7-P3-16 | 本机成就 | local achievements | 无账号、无遥测 | L |
| CG7-P3-17 | 离线每日挑战 | date seed | 完全离线 | L |
| CG7-P3-18 | 本地 replay | command log | 用于复盘/调试 | L |
| CG7-P3-19 | 中英文 | localization | 长文本布局测试 | L |
| CG7-P3-20 | Windows 桌面包 | installer/portable | 无 Python 可运行 | XL |
| CG7-P3-21 | macOS app bundle | package | 数据目录和关闭 smoke | XL |
| CG7-P3-22 | Linux package | AppImage/等效 | XDG 路径正确 | L |
| CG7-P3-23 | 自动发布 | tagged builds | smoke 通过才发布 | L |
| CG7-P3-24 | 截图/GIF/项目主页 | showcase | README 首屏展示玩法 | M |
| CG7-P3-25 | Issue/PR 模板 | community | Bug 含版本、日志和复现 | S |
| CG7-P3-26 | 谨慎新增游戏 | template/contracts | 同时交付规则、记录、输入、测试 | XL |

---

## 9. 必须新增的测试

### 9.1 发布阻断测试

```text
test_windows_request_lock_never_calls_os_kill_zero
test_same_request_two_processes_keep_both_processes_alive
test_crash_before_lock_metadata_is_recoverable
test_e99_spool_profile_name_migrates_to_uuid
test_e99_spool_payload_hash_is_rebuilt
test_schema_v2_settings_progress_slots_upgrade_to_v4
test_schema_v2_with_attempts_does_not_fail_profile_migration
test_direct_v1_v2_v3_to_v4_is_idempotent
```

### 9.2 profile

```text
test_game_launch_waits_for_profile_resolution
test_profile_save_failure_blocks_or_warns_before_launch
test_standalone_game_creates_profile_row
test_legacy_import_creates_profile_row
test_rename_policy_is_reflected_in_leaderboard
test_profile_foreign_keys_reject_orphans
```

### 9.3 progress/save slots

```text
test_sokoban_new_run_cannot_reduce_unlocked_level
test_sokoban_practice_does_not_overwrite_campaign
test_zuma_level_one_cannot_clear_completed_all
test_progress_is_separated_by_ruleset
test_progress_write_survives_database_lock
test_2048_input_is_blocked_until_slot_load_finishes
test_2048_resume_preserves_attempt_uuid_and_revision
test_2048_gameover_slot_is_not_restored_as_playing
test_corrupt_save_slot_is_quarantined
test_latest_autosave_survives_process_exit
```

### 9.4 spool/recovery

```text
test_malformed_lock_file_does_not_deadlock_request
test_legacy_pending_transient_failure_keeps_original_file
test_schema_repair_pending_uses_recovery_required_state
test_schema_repair_does_not_retry_every_two_seconds
test_clock_rollback_does_not_delete_pending
test_save_status_survives_status_cache_eviction
```

### 9.5 CI/质量

```text
test_core_install_without_api_extra
test_python_supported_version_matrix
test_coverage_threshold
test_migration_fixtures_run_on_all_three_OS
```

---

## 10. 性能和稳定性门槛

1. pygame 主线程不得执行：
   - SQLite；
   - spool scan；
   - profile migration；
   - save-slot read/write；
2. `submit_score_async()` p99 ≤2 ms；
3. `retry_failed_saves()` 调用本身 p99 ≤2 ms；
4. profile resolve 前首帧仍可渲染，但不可错误启动游戏；
5. 2048 slot load 不丢第一帧输入；
6. 正常游戏目标 60 FPS，基准机 p95 ≤16.7 ms；
7. 保存、进度和存档不得造成 >50 ms 主线程长帧；
8. Windows 同 request 竞争：
   - 不终止任何进程；
   - 不丢 pending；
9. 旧 DB + 旧 pending 升级：
   - 自动恢复；
   - 原文件有备份；
10. 100 次切换：
    - 线程回基线；
    - FD 不增长；
    - Surface/内存稳定；
11. 30–60 分钟：
    - pending 最终 commit、recovery required 或 quarantine；
    - 不无限撞库；
    - 不持续增长内存；
12. 满盘、只读、坏 DB、坏 slot、坏 progress：
    - 游戏仍可玩；
    - 最后一份数据不被静默删除；
    - 有恢复提示。

---

## 11. 稳定版本质量门禁

正式稳定版本至少满足：

- 所有 P0 关闭；
- Windows request lock 真机通过；
- schema 0/1/2/3 直升当前版本；
- 上一版本 pending 自动迁移；
- profile 在游戏启动前 resolved；
- progress 不回退；
- 2048 autosave 不覆盖用户新输入；
- score/profile/progress/save slot 失败可见且可恢复；
- 默认无 Flask/HTTP；
- 数据位于用户目录；
- migration/backup/export 完整；
- GitHub Actions 三平台实际通过；
- required checks 开启；
- core-only 测试；
- pytest/JUnit/coverage；
- formatter/lint/type/dependency audit；
- LICENSE、CHANGELOG、恢复文档；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代升级 fixture、跨进程测试、故障注入和真实玩家测试。

---

## 12. 推荐实施顺序

### M0：关闭升级和 Windows 阻断

1. 跨平台 request lock；
2. spool v1→v2；
3. schema v2→v4；
4. 真实旧数据 fixture；
5. required CI gate。

### M1：完成本地数据闭环

- profile startup gate；
- profile row/FK；
- progress merge；
- durable local-state writes；
- 2048 load/attempt/terminal slot；
- repair state；
- clock correction；
- corruption recovery。

### M2：工程可持续

- pytest；
- 全套 coverage；
- branch protection；
- typing；
- property tests；
- dependency lock；
- LICENSE；
- 文档整理。

### M3：桌面体验

- 档案页；
- InputManager；
- 键位；
- 手柄；
- 音频；
- DPI；
- 字体；
- 可访问性；
- launcher 拆分；
- RNG/Clock；
- 纯规则引擎。

### M4：内容与发行

- 五款游戏舒适功能；
- Sokoban/Zuma 编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 自动发布；
- 基础稳定后再新增游戏。

---

## 13. 最终判断

本轮修复仍然是一次明显进步：

- 前一轮两个 P0 已关闭；
- schema、profile、存档、进度和 CI 不再只是规划；
- 保存状态可以回写；
- 错误分类、迁移和可选 API 更成熟。

但当前改动跨越了多个历史数据格式，新的主要风险已经从“单次保存是否成功”转为：

```text
旧版本能否安全升级
profile 是否在使用前确定
进度是否单调
存档是否与一次 attempt 保持一致
跨平台锁是否真的安全
```

推荐下一步：

> **不再扩大架构范围，先修 Windows request lock、旧 spool 和 schema v2 升级；随后完成 profile/progress/autosave 的一致性。基础稳定后，再投入可访问性、玩法和桌面发行。**

这条路线完全服务于本地单机合集，不需要联网竞技平台。
