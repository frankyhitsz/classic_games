# Classic Games Hub 第六次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 当前基线：`main` 分支 commit `e99e6ac28086a7d7a9eccb0f9ebb1aa47ac2442c`（`e99e6ac`）
> 核心修复提交：`4d7db8ef5bc1471bdc67668aeac80bd48d160439`
> 对比基线：上次审查 commit `00d9dc11c949d9ae1eb1e16405c52b74ee6d5586`
> 产品定位：**本地运行、单机为主、默认无网络依赖的经典小游戏合集**
> 审查范围：客户端、五款游戏、本地仓储、可选 Flask 适配器、测试、脚本、依赖与仓库治理

---

## 0. 执行摘要

本轮修复总体质量较高。上次审查提出的三个发布阻断方向已经真正进入实现：

1. request ID 与既有 pending payload 冲突时，当前代码会在访问 SQLite 前返回；
2. 旧版 Python `str(dict)` 附加信息可通过受限解析恢复，附加信息损坏时会尽量保留基础成绩；
3. 五款游戏具有明确规则版本，旧库导入记录使用 `legacy-v1`，默认不再混入当前规则的个人最佳。

同时，以下上一轮 P1 问题也有实质改善：

- schema 快路径开始核对命名索引；
- 同版本结构修复前会先备份；
- score policy 区分 `final_only` 与 `monotonic_revision`；
- attempt status 不可在 `practice/completed` 间任意切换；
- 空 request/attempt ID 和超出 SQLite 64 位范围的整数会被拒绝；
- receipt 过期或损坏后可从 attempt 重建；
- spool 的无硬链接 fallback、目录 fsync、错名恢复、单文件限制与批量告警已经实现；
- 运行中的 launcher 能定期发现其他进程新增的 pending；
- 临时初始化失败可尝试重新打开仓储；
- 读写 executor 分离；
- `AttemptContext` 已接入 BaseGame 与 2048；
- 可选 Flask 使用端点级查询参数集合；
- 退出和重置二次确认增加了 3 秒有效期；
- 2048 同分但最终元数据不同的更新不会再被简单丢弃。

因此，项目的主架构已经稳定在合理方向：

> **pygame + 进程内本地服务 + SQLite + durable spool，Flask 仅作为可选调试适配器。**

不建议再进行一次“大重构”，也不应转向账号、云端排行、匹配、赛季或反作弊平台。

不过，本轮仍发现两个发布阻断问题：

### P0-A：磁盘空间耗尽时，当前错误分类可能删除待保存记录

SQLite 在磁盘空间耗尽时会抛：

```text
sqlite3.OperationalError: database or disk is full
```

当前 `_save_mutation()` 的 retryable 关键字没有覆盖该消息，因此会把它分类为永久失败。随后代码会删除 spool、移除 `_non_durable`，UI 也会把它当成不可重试错误。

这会导致：

- spool 已成功时：原 pending 被删除；
- spool 也失败时：内存中的待保存记录也被清掉；
- 用户释放磁盘空间后无法按 S 重试。

这是明确的数据丢失风险。

### P0-B：旧 `pending_saves.json` 含 `NaN/Infinity` 或极端嵌套时仍可阻断启动

Python 的 `json.loads()` 默认接受 `NaN` 和 `Infinity`。旧 pending 项在 mutation 规范化时会被拒绝，随后进入 `_quarantine_value()`；但该函数使用 `canonical_json()` 重新序列化坏值，而当前捕获列表不包括 `MutationError`。

结果是：

```text
坏记录本应被隔离
→ 隔离函数再次因 NaN 失败
→ 异常逃出构造函数
→ launcher 可能无法启动
```

旧 pending 文件也没有读取前的原始大小与最大嵌套深度限制。

除这两项外，剩余工作主要是：

- pending 最终提交结果与 UI 的状态回写；
- 保留真实结算时间；
- legacy source 与 schema migration 解耦；
- 当前 schema 的行级数据不变量；
- profile、进度、存档和设置；
- pytest/CI/覆盖率/LICENSE/桌面打包；
- 五款游戏的单机体验完善。

---

## 1. 审查方法与限制

### 1.1 已完成的检查

- 锁定当前 `main` 最新提交；
- 对比 `00d9dc1..e99e6ac` 的全部变更文件；
- 阅读：
  - `game_service/catalog.py`
  - `game_service/mutation.py`
  - `game_service/service.py`
  - `game_service/local_backend.py`
  - `game_service/store.py`
  - `client/common/ui.py`
  - `client/common/network.py`
  - `client/launcher.py`
  - `client/games/game_2048.py`
  - 当前未再次修改但与问题有关的 Sokoban/Tetris/Snake/Zuma 代码；
  - `server/app.py`
  - `tests/test_storage_v2.py`
  - `tests/regression.py`
  - `tests/stress.py`
  - `README.md`
  - `pyproject.toml`
  - `run_tests.sh`
  - `spec.md`
  - `task.md`
- 逐项核对上一轮 F01–F21；
- 独立执行底层最小验证：
  - 用 SQLite `PRAGMA max_page_count` 触发 `database or disk is full`；
  - 验证当前关键字分类会得到 `retryable=False`；
  - 验证 Python `json.loads()` 接受 `NaN`；
  - 验证 `allow_nan=False` 的重新序列化会失败；
- 检查最新 commit 的 GitHub 状态与 workflow 记录。

### 1.2 限制

当前执行环境没有安装 `pygame` 和 `Flask`，也无法联网安装依赖，因此本次没有独立完整运行：

- pygame headless 回归；
- optional Flask 集成测试；
- 20,000 步压力；
- 渲染 benchmark；
- wheel smoke。

仓库的 `task.md` 报告：

- 107 项功能检查通过；
- 40 项第五轮存储与生命周期测试通过；
- 20,000 步固定输入；
- 五款游戏渲染 p95 约 1.1–4.4 ms；
- 本地保存 p95 约 1.97 ms；
- 持锁 async 调用 p99 约 0.029 ms；
- 240 次 SQLite 并发写入与 `integrity_check=ok`。

这些是项目自测证据，不是本次环境独立复现结果。当前最新 commit 没有 GitHub Actions workflow 运行记录，因此还缺少远端、跨平台的独立自动证明。

---

## 2. 上一轮问题修复验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| spool conflict 后仍可能写 DB | **修复到位** | `StoreError` 在写 SQLite 前立即返回，原 payload hash 与新 hash 随错误返回 |
| 旧 `str(dict)` extra 被整行跳过 | **修复到位** | JSON → 受限 `ast.literal_eval`；坏 extra 保留基础成绩 |
| 旧规则成绩混入当前 best | **修复到位** | 当前五款规则版本显式化，旧库导入为 `legacy-v1` |
| 无 hard-link fallback 暴露半文件 | **基本到位** | request lock + 已 fsync temp + `os.replace()`；仍需 Windows/Linux 真机并发证明 |
| spool 文件名未绑定 request ID | **修复到位** | 错名文件会恢复到 canonical path，冲突项隔离 |
| 长期运行实例看不到外部 pending | **修复到位** | launcher 周期调用后台扫描 |
| 临时初始化失败需重启 | **基本到位** | health/read/write 可尝试 reopen；迁移类错误仍可能重复昂贵尝试 |
| schema 只检查列 | **基本到位** | 已检查命名索引；仍未检查表约束与行级不变量 |
| 空 transport ID 被静默替换 | **修复到位** | 只有 `None` 自动生成 |
| revision/submission ID 无上界 | **修复到位** | 限制到 SQLite 有符号 64 位正整数 |
| score 与 extra 可被低分 revision 混合 | **修复到位** | monotonic policy 拒绝分数回退；status 不可变 |
| receipt 过期后重放不安全 | **修复到位** | 派生 attempt UUID 也参与查询 |
| 坏 receipt 与错误分类 | **部分到位** | receipt 可重建，异常分类增强；`SQLITE_FULL` 等仍误分类 |
| Flask leaderboard `profile_id` 触发 500 | **修复到位** | 返回 JSON 400 |
| 游戏层未显式传维度 | **修复到位** | `AttemptContext` 接入 BaseGame/2048 |
| profile 仍是显示名 | **未做，属产品功能** | README 已明确限制；需与迁移/UI 一起设计 |
| worker 无优先级 | **基本到位** | 读写分离、批量恢复；手动 retry 仍同步扫描，自动 backoff 尚不完整 |
| 退出确认长期有效 | **修复到位** | 3 秒后失效 |
| spool 缺少大小/数量治理 | **基本到位** | 64 KiB、10,000 文件、64 MiB 告警；legacy 文件和 quarantine 仍需硬化 |
| legacy marker 只是一枚全局标记 | **基本到位** | 按路径/size/mtime 分开；项目移动会重复导入同一旧库 |
| 本地 sync API 忽略未知字段 | **修复到位** | 未知关键字会抛 TypeError |

---

## 3. 当前发布阻断问题

## 3.1 CG6-F01：`SQLITE_FULL` 被当成永久错误并清除待保存状态

- **优先级**：P0
- **证据**：实际触发底层 SQLite 错误 + 当前代码路径确定
- **位置**：`game_service/local_backend.py::_save_mutation`

当前 OperationalError 分类依靠消息包含：

```text
locked
busy
unavailable
readonly
read-only
disk i/o
unable to open
```

SQLite 磁盘满的实际消息是：

```text
database or disk is full
```

不匹配上述任何 token。

### 当前后果

#### spool 写成功，DB 写失败

```text
spool 已存在
→ retryable=False
→ 进入永久失败分支
→ outbox.remove(request_id)
→ pending 数据被删
```

#### spool 写也因 ENOSPC 失败

```text
_non_durable 暂存 mutation
→ DB 返回 retryable=False
→ _mark_committed() 又清除 _non_durable
→ 唯一副本只剩游戏对象内存
```

UI 会把它当成不可重试错误，用户释放磁盘后也无法按 S 重试。

### 修复要求

不要按英文字符串做主要分类，应优先使用：

```python
exc.sqlite_errorcode
exc.sqlite_errorname
```

推荐分类：

| SQLite 类别 | 处理 |
|---|---|
| `SQLITE_BUSY` / `SQLITE_LOCKED` | retryable |
| `SQLITE_FULL` | recovery_required，保留 spool/non-durable |
| `SQLITE_READONLY` | recovery_required，保留 |
| `SQLITE_IOERR` | recovery_required 或 retryable，保留 |
| `SQLITE_CANTOPEN` | retryable/recovery_required，保留 |
| `SQLITE_CORRUPT` / `SQLITE_NOTADB` | quarantine，绝不删除原始 mutation |
| `SQLITE_CONSTRAINT` | permanent semantic conflict 或 repair required |
| `no such table` / schema mismatch | repair_required，保留 |
| mutation validation/conflict | permanent，可移除当前新请求，但不得影响既有 pending |

只有**确定属于请求本身永久非法**的错误，才能自动删除 pending。

---

## 3.2 CG6-F02：legacy pending 隔离路径仍可能抛异常并阻断启动

- **优先级**：P0
- **证据**：底层 JSON 行为复现 + 控制流确定
- **位置**：
  - `PersistentSaveOutbox._migrate_legacy_file`
  - `PersistentSaveOutbox._quarantine_value`
  - `canonical_json`

示例旧文件：

```json
[
  {
    "game_id": "snake",
    "player": "p",
    "score": 1,
    "request_id": "legacy-nan-request-0001",
    "extra": {"x": NaN}
  }
]
```

Python 标准 JSON parser 默认会得到 `float("nan")`。

随后：

```text
normalize_score_mutation
→ canonical_json(... allow_nan=False)
→ MutationError
→ _migrate_legacy_file 捕获并调用 _quarantine_value
→ _quarantine_value 再次 canonical_json(value)
→ 再次 MutationError
→ 当前 except 不捕获
→ 构造 LocalBackendClient 失败
```

类似风险还包括：

- `Infinity`、`-Infinity`；
- 极深嵌套导致 `RecursionError`；
- 超大旧 JSON 数组导致启动耗时或内存压力；
- quarantine 目录无读取权限；
- 逐文件 canonical rename/fsync 遇到 OSError 时中断整个扫描。

### 修复要求

- 读取 legacy JSON 时设置严格 `parse_constant`，拒绝非标准数字；
- 对旧文件先检查原始字节大小；
- 设最大 item 数与最大嵌套深度；
- quarantine 失败时不得抛出到启动器；
- 无法重新序列化的 item 应：
  - 移动完整原始 legacy 文件；或
  - 写入安全的 `repr`/base64 摘要；
- 每条记录错误隔离，不能终止后续记录；
- 捕获：
  - `MutationError`
  - `RecursionError`
  - `MemoryError`（记录并停止，不反复解析）
  - `OSError`
- 增加 NaN/Infinity/deep nesting/permission 测试。

---

## 4. 当前高优先级问题

## 4.1 CG6-F03：durable pending 最终成功或隔离后，游戏 UI 不会同步更新

- **优先级**：P1
- **位置**：
  - `client/common/ui.py`
  - `client/games/game_2048.py`
  - `game_service/local_backend.py`

首次保存返回 `durable_pending=True` 后，游戏会显示：

```text
已写入待保存文件
```

后台 worker 之后可能：

- 提交成功；
- stale no-op；
- request conflict；
- database corrupt → quarantine；
- permanent failure。

但 BaseGame 没有按 request ID 查询最终状态，也没有事件回调。

### 影响

- 后台已经提交成功，overlay 仍长期显示“待保存”；
- leaderboard 在 pending 状态读取过旧数据后不会自动失效；
- 后台已隔离，UI 仍可能给出过度乐观提示；
- Sokoban `_confirmed_total` 不会收到后台成功回调；
- 2048 的本地确认字段也可能长期落后。

### 修复要求

在 `GameDataService` 增加：

```python
def poll_save_events() -> list[SaveEvent]: ...
def get_save_status(request_id: str) -> SaveStatus: ...
```

事件至少包含：

```text
request_id
attempt_uuid
revision
state: committed / durable_pending / quarantined / permanent_failure
result
```

BaseGame/launcher 消费事件后：

- 更新保存文案；
- 调用 `on_score_save_succeeded`；
- 清除 pending 标识；
- 失效 leaderboard/recent；
- 显示 quarantine 恢复入口。

## 4.2 CG6-F04：手动重试仍会在 pygame 线程同步扫描目录

- **优先级**：P1
- **位置**：
  - `LocalBackendClient.retry_failed_saves`
  - launcher 的 S 键路径

`retry_failed_saves()` 先同步执行：

```python
self.outbox.list_envelopes()
```

然后才把写操作交给 worker。

周期扫描已经在 read worker 中执行，但用户按 S 时是 launcher 事件线程直接调用。待保存目录很大或文件系统很慢时，仍可能产生明显卡顿。

### 修复要求

- `retry_failed_saves()` 只负责排队并立即返回 Future/handle；
- 目录扫描始终发生在 read worker；
- UI 显示“正在扫描待保存记录”；
- 同时防止重复扫描任务堆积。

## 4.3 CG6-F05：外部旧库导入状态与 schema current 仍然耦合

- **优先级**：P1
- **位置**：
  - `_schema_is_current`
  - `_import_legacy_scores`

外部 legacy marker 缺失或 fingerprint 不匹配，会让整个数据库被判断为“schema 不当前”，从而：

- 备份当前主库；
- 进入 schema migration；
- 再执行 legacy import。

如果旧库损坏或无法打开，import 不写 marker。下一次启动又会：

- 再次备份；
- 再次迁移；
- 再次失败。

这会造成 backup storm，并把一个可选旧数据源的问题放大为当前主库重复维护。

### 修复要求

将两个状态拆开：

```text
schema_current
legacy_import_state
```

外部旧库变化不应触发 schema migration。需要独立的：

```text
legacy_import_pending
legacy_import_failed
legacy_import_completed
next_retry_at
```

不可读旧库写入失败 marker/错误摘要，并使用退避；用户可手动重试导入。

## 4.4 CG6-F06：项目移动路径后，同一个旧库可能被重复导入

- **优先级**：P1
- **位置**：
  - `_legacy_marker`
  - legacy request ID/attempt UUID/source_key 生成

当前身份包含旧库绝对路径。

场景：

```text
第一次在 /Users/a/project/data/scores.db 导入
之后把项目移动到 /Users/a/archive/project/data/scores.db
```

同一个数据库、同一批 row ID 会得到不同：

- marker key；
- source hash；
- request ID；
- attempt UUID；
- source_key。

因此会作为第二个来源再次导入。个人最佳通常不受影响，但：

- attempt 数重复；
- 最近记录重复；
- 历史统计失真。

### 修复要求

增加 source identity 策略：

- schema fingerprint；
- 稳定 row semantic hash；
- 可选整库内容 hash；
- 路径只作为展示信息，不作为唯一身份全部来源。

迁移时用：

```text
legacy_row_fingerprint =
hash(game_id, normalized_player, score, normalized_extra, created_at)
```

辅助去重，并在 UI 中允许用户确认“这是同一旧库的移动副本”。

## 4.5 CG6-F07：待保存记录的真实结算时间在重放时丢失

- **优先级**：P1
- **位置**：
  - `PendingSaveEnvelope.created_at`
  - `LocalGameStore.record_mutation`

spool envelope 已经保存创建时间，但 store 插入 attempt 时使用当前 `time.time()`。

因此一局游戏在 10:00 完成、12:00 才恢复：

```text
score_achieved_at = 12:00
recent time       = 12:00
```

这会改变：

- 同分先后顺序；
- 最近完成时间；
- 历史统计；
- 每日挑战日期归属。

### 修复要求

在内部 mutation 中加入可信本地时间：

```text
started_at
finished_at / occurred_at
```

规则：

- 桌面端在游戏完成时生成；
- spool 原样保留；
- local store 使用该时间；
- optional HTTP 不允许任意伪造，或只在 loopback/debug 下接受；
- 对异常未来/过早时间做范围校验；
- `updated_at` 仍表示实际写库时间。

## 4.6 CG6-F08：current schema 只验证结构，不验证行级不变量

- **优先级**：P1
- **位置**：`_schema_is_current`

当前会检查列和索引，但不检查已有数据是否满足：

- attempt UUID 非空；
- profile/game/mode/ruleset/status 非空且合法；
- revision > 0；
- score 在范围内；
- timestamp 有限；
- status 属于允许集合；
- final-only attempt 没有异常 revision 历史。

手工修改、旧崩溃迁移或磁盘损坏可能留下坏行，而 fast path 仍直接返回。

### 修复要求

二选一：

1. schema v4 重建表，增加 `NOT NULL` 和 `CHECK`；
2. 启动维护中执行有界数据验证，并隔离坏行。

推荐 DB 约束：

```sql
CHECK(score BETWEEN 0 AND 2147483647)
CHECK(revision > 0)
CHECK(status IN ('completed','practice'))
```

同时保留应用层错误信息。

## 4.7 CG6-F09：SQLite OperationalError 分类仍过度依赖英文消息

- **优先级**：P1，和 F01 同一修复批次
- **位置**：`_save_mutation`

除 `SQLITE_FULL` 外，以下也可能被误分类：

- `no such table: attempts`
- `database schema has changed`
- `interrupted`
- `cannot start a transaction...`
- 平台本地化或版本不同的消息。

应以 `sqlite_errorcode` 为主、消息为 fallback，并让每个分类决定：

```text
是否保留 spool
是否自动重试
是否需要修复
是否 quarantine
```

## 4.8 CG6-F10：legacy extra 解析缺少原始大小与深度限制

- **优先级**：P1
- **位置**：`_decode_legacy_extra`

当前在解析完成后才检查 canonical 大小。一个几十 MB 或极深嵌套字符串会先进入：

- `json.loads`
- `ast.literal_eval`
- 递归 `_legacy_value_is_safe`

### 修复要求

- raw bytes 上限；
- 最大容器深度；
- 最大节点数；
- 最大字符串长度；
- 捕获 `RecursionError`；
- 迁移大库时流式/分批读取，而不是一次 `fetchall()`。

## 4.9 CG6-F11：部分 spool 文件系统错误仍可能终止整次扫描

- **优先级**：P1
- **位置**：`list_envelopes`

每个文件主循环主要捕获 `StoreError`，但 canonical rename、unlink、request lock、目录 fsync 还可能抛出 `OSError`。

一条权限异常文件不应阻止其他 999 条记录恢复。

### 修复要求

- 每条文件独立捕获 `OSError`；
- 记录 `spool_io_error`；
- 无法移动时保留原文件；
- 不把整个 scan Future 置为失败；
- 下次使用退避重试。

## 4.10 CG6-F12：store reopen 可能反复执行昂贵失败迁移

- **优先级**：P1
- **位置**：`_try_reopen_store`

当前只有 `unsupported_schema` 被标记为永久错误。若是：

- migration 逻辑错误；
- 重复/坏索引；
- 无法备份；
- 非暂时权限问题；

每次 health/read/write 都可能再次初始化、再次备份或再次失败。

### 修复要求

引入：

```text
transient
repair_required
permanent_newer_schema
```

并带：

```text
next_retry_at
failure_count
last_error_code
```

repair-required 不自动高频重试，提供恢复页。

## 4.11 CG6-F13：receipt 清理缺少 `expires_at` 索引和分批策略

- **优先级**：P1/P2
- **位置**：`maintenance`

回执按 `expires_at` 删除，但未建立对应索引。长时间使用后，启动 maintenance 会全表扫描，并占用单 writer。

### 修复要求

- `idx_save_requests_expires_at`；
- 每批删除有限条；
- maintenance 低优先级；
- 不阻塞新成绩写入；
- 增加大 receipt 表 benchmark。

## 4.12 CG6-F14：request lock 的 stale 判断使用墙钟和固定 30 秒

- **优先级**：P2
- **位置**：`_request_lock`

系统休眠、时间跳变或极慢磁盘可能让仍在工作的 lock 被另一进程删除。

对正式桌面版，更可靠的是：

- OS 原生文件锁；
- PID + process start token；
- owner nonce；
- 仅在确认进程不存在时清理。

当前请求文件很小，概率较低，但需要跨平台验证。

## 4.13 CG6-F15：score policy 是普通字符串，拼写错误会退化为 monotonic

- **优先级**：P1
- **位置**：
  - `GameDescriptor.score_policy`
  - `record_mutation`

代码只显式判断：

```python
policy == "final_only"
```

其他任何值都会进入 monotonic 路径。

### 修复要求

- 使用 Enum/Literal；
- catalog 初始化时验证唯一 ID、规则版本和 policy；
- CI 中执行 registry contract test。

## 4.14 CG6-F16：Sokoban 完整通关总分为 0 时不会提交

- **优先级**：P1
- **位置**：`Sokoban._check_win`

初始：

```python
self._confirmed_total = 0
```

提交条件：

```python
self.total_score > self._confirmed_total
```

如果每关移动数都达到或超过 1000，全部关卡得分均为 0，合法完整通关总分为 0，但不会产生 attempt。

### 修复要求

- 使用 `None` 表示尚未确认，而不是 0 sentinel；
- condition：

```python
confirmed is None or total_score > confirmed
```

- 增加零分完整通关测试；
- 同分但 metadata 更新是否提交也应写进规则。

## 4.15 CG6-F17：本地榜单的并列 medal 与最近记录语义不准确

- **优先级**：P2
- **位置**：
  - `draw_leaderboard`
  - launcher recent panel

当前 medal 由列表索引决定，而不是 `entry["rank"]`。

并列第一的第二个玩家可能显示银牌；最近记录也被人工赋予 1/2/3 并显示奖牌，但它本质是时间列表，不是排名。

### 修复要求

- 榜单 medal 根据真实 rank；
- 最近记录使用时间/游戏标签，不显示金银铜；
- 本地首页优先展示：
  - 当前个人最佳；
  - 最近完成；
  - 继续游戏；
  - 关卡进度。

## 4.16 CG6-F18：profile 仍以显示名为身份，昵称和 IME 未完成

- **优先级**：P2，产品完善
- **现状**：

```text
profile_id = display name
```

因此：

- 改名形成新档案；
- 同名家庭成员合并；
- 每次启动默认 guest；
- 玩家名未持久化；
- 中文输入依赖 `KEYDOWN.unicode`，没有 `TEXTINPUT/TEXTEDITING`。

无需账号系统。建议实现本机 profile UUID、显示名和最近使用档案。

## 4.17 CG6-F19：BaseGame 与 2048 仍维护两套保存控制器

- **优先级**：P2
- **影响**：

- 状态字段重复；
- pending→saved 回写要修两处；
- reset/detach 逻辑更难验证；
- future/generation/queued revision 行为容易漂移。

建议抽出统一 `AttemptSaveController`，2048 只提供“同一 attempt 的新 revision”策略。

## 4.18 CG6-F20：启动仍有同步迁移和首次全目录扫描

- **优先级**：P2
- **位置**：`LocalBackendClient.__init__`

虽然正常 current schema 很快，但以下仍在创建 launcher 前同步发生：

- schema 检查；
- 必要备份/迁移；
- legacy import；
- quarantine 计数；
- 首次 pending scan；
- 有 pending 时又触发一次同步 rescan。

大旧库或 10,000 spool 文件仍可能延迟首帧。

建议建立启动状态页：

```text
游戏立即可用
记录服务正在检查/迁移
```

迁移完成后事件驱动刷新。

## 4.19 CG6-F21：optional Flask 与 HTTP 调试路径仍有边界不一致

- **优先级**：P2
- **问题**：

1. query helper 对所有端点都把 `limit` 视为允许，stats 中的无效 limit 会被忽略；
2. HTTPException（例如未知路由）仍可能返回默认 HTML；
3. `api_error` 没有传递 StoreError details；
4. HTTP 客户端 replace 失败清理仍按 `(game, player)`，而不是 attempt UUID/mode/ruleset；
5. HTTP 失败队列仍只在内存。

由于 HTTP 只是调试适配器，修复接口一致性即可，不应为它重新引入云端架构。

## 4.20 CG6-F22：工程门禁仍不完整

- **优先级**：P1/P2
- **现状**：

- 最新 commit 没有 GitHub Actions workflow 运行；
- 仓库未见 `.github/workflows`；
- 无 LICENSE、NOTICE、CHANGELOG、CONTRIBUTING、SECURITY；
- 测试仍混合大型自定义 runner、unittest 和 stress；
- 没有提交 coverage/JUnit；
- dev extra 没有 mypy、coverage、Hypothesis；
- 没有依赖锁文件；
- 没有三平台桌面构建配置；
- `pyproject` 版本仍为 0.4.0，需要在正式发布时与 schema/ruleset 变更统一管理；
- 多份历史审查文档仍位于根目录。

---

## 5. 当前五款游戏的判断

## 5.1 Tetris

上一轮输入和大 dt 修复未见回退。

仍适合做的单机完善：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- 规则帮助页；
- 可注入 RNG；
- 静态网格缓存；
- 同义物理键的“逻辑动作边沿”；
- 保持“辅助旋转”命名，不宣称严格标准 SRS。

这些都是单机舒适性，不需要竞技平台。

## 5.2 Snake

已保持：

- 双转向队列；
- 长停顿保护；
- 吃食物后重新计算速度间隔。

后续：

- 速度选择；
- 穿墙；
- 障碍；
- 双人同屏；
- RNG 注入；
- 色弱形状/纹理；
- 棋盘背景缓存。

## 5.3 2048

已保持：

- 无效果输入不会堵塞队列；
- win/pause/reset 清理输入；
- stable attempt UUID/revision；
- 同分但 final extra 不同会继续提交。

后续：

- 合并到统一 SaveController；
- 自动存档和继续；
- 撤销；
- 本地最高分页；
- 重开确认；
- 可注入 RNG；
- 棋盘尺寸；
- 静态背景缓存。

## 5.4 Sokoban

核心计分修复仍成立：

- 同关不重复累计；
- 跳关进入练习；
- 全部完成才提交；
- monotonic attempt。

需要补：

- 零分完整通关；
- 关卡进度；
- 选关和解锁；
- 最少移动/推动；
- 是否使用撤销；
- 星级；
- 静态死角；
- 死锁检测；
- 提示；
- 地图闭合/可达验证；
- XSB 导入；
- 关卡编辑器；
- 固定逻辑窗口。

## 5.5 Zuma

核心连锁与碰撞修复未见回退。

建议先做：

- reaction FSM；
- 多重/重叠 reaction 属性测试；
- RNG 注入；
- `incoming` 改 deque；
- 色弱符号；
- 训练与选关。

之后再增加：

- 原创道具球；
- 新轨道；
- 轨道编辑器；
- 关卡目标。

---

## 6. 明确的非目标

本项目不需要建设：

- 注册和登录；
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

Flask 只保留为：

- 教学；
- 调试；
- 本机 API 示例；
- 可选数据查看适配器。

---

## 7. 推荐的增量目标架构

现有架构无需推倒：

```text
Launcher / Game
    │
    ├── GameDescriptor
    ├── AttemptContext
    ├── AttemptSaveController
    │       ├── request_id
    │       ├── attempt_uuid
    │       ├── revision
    │       ├── occurred_at
    │       └── SaveStatus
    │
    ├── GameDataService
    │       ├── submit
    │       ├── save events/status
    │       ├── progress
    │       ├── save slots
    │       ├── settings
    │       └── backup/history
    │
    ├── LocalReadWorker
    └── LocalWriteWorker
            ├── LocalGameStore
            └── Durable Spool

Optional Flask Adapter
    └── same service contracts
```

### 7.1 SaveStatus

```text
SAVING
COMMITTED
DURABLE_PENDING
RECOVERY_REQUIRED
QUARANTINED
PERMANENT_REQUEST_ERROR
```

### 7.2 StorageErrorKind

```text
BUSY
FULL
READ_ONLY
IO_ERROR
CANT_OPEN
CORRUPT
CONSTRAINT
SCHEMA_REPAIR_REQUIRED
INVALID_MUTATION
INTERNAL
```

### 7.3 本地数据能力

后续按完整功能新增：

```text
profiles
progress
save_slots
settings
```

不要再次提前创建没有 API/UI 的空表。

---

## 8. 完整优化任务清单

### 优先级

- **P0**：可能丢本地成绩或阻断启动；发布阻断。
- **P1**：数据真实性、自恢复、迁移、测试和工程基础；稳定版前完成。
- **P2**：输入、UI、性能、可访问性和维护性。
- **P3**：内容、桌面发行和长期产品能力。
- **S/M/L/XL**：相对工作量。

---

## 8.1 P0：本地数据安全

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG6-P0-01 | 基于 SQLite error code 分类 | `StorageErrorKind` mapper | `SQLITE_FULL` 不再判永久请求错误 | M |
| CG6-P0-02 | 所有存储层失败默认保留 mutation | `_save_mutation` policy | 只有请求语义永久非法才删除 pending | M |
| CG6-P0-03 | 磁盘满可恢复 | fault test + UI | 释放空间后可重试；spool/non-durable 不被清除 | M |
| CG6-P0-04 | 严格解析 legacy pending | parser limits | NaN/Infinity/deep nesting 不逃出构造函数 | M |
| CG6-P0-05 | quarantine 永不成为启动失败源 | safe fallback | 无法重序列化时仍保留原始文件/摘要 | M |
| CG6-P0-06 | 逐条 spool I/O 隔离 | scan error boundary | 一个权限错误文件不阻断其他记录 | M |
| CG6-P0-07 | P0 故障注入套件 | unit/integration | 覆盖 FULL、NaN、目录只读、fsync 失败、巨大 legacy | L |
| CG6-P0-08 | 发布门禁 | CI gate | 任一 P0 场景失败禁止发布 | S |

---

## 8.2 P1：数据一致性、迁移与工程基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG6-P1-01 | per-request SaveEvent | event queue | pending 后最终 commit/quarantine 可观察 | L |
| CG6-P1-02 | UI pending 状态回写 | BaseGame/2048 integration | pending→saved 自动更新并刷新榜单 | L |
| CG6-P1-03 | 手动 retry 全异步 | retry Future | 按 S 不同步遍历磁盘 | M |
| CG6-P1-04 | pending 自动退避 | retry scheduler | 锁长期存在时不每 2 秒固定撞库 | M |
| CG6-P1-05 | 保留结算时间 | mutation timestamp | 重放不改变 achievement/recent 时间 | L |
| CG6-P1-06 | external import 与 schema 解耦 | import state | 坏旧库不触发反复 schema backup | L |
| CG6-P1-07 | import 失败 marker | retry state | 相同错误有退避和用户可见状态 | M |
| CG6-P1-08 | 移动旧库去重 | semantic/source identity | 同一旧库换路径不重复导入 | L |
| CG6-P1-09 | schema v4 行约束 | migration | score/revision/status/times 有 DB 约束 | XL |
| CG6-P1-10 | current-row integrity scan | maintenance | 坏行隔离，正常启动不崩 | L |
| CG6-P1-11 | legacy raw 大小限制 | decoder guard | 解析前检查字节数 | S |
| CG6-P1-12 | legacy 深度/节点限制 | safe validator | 深层输入不递归崩溃 | M |
| CG6-P1-13 | store reopen 状态机 | transient/repair/permanent | migration 错误不高频重复备份 | L |
| CG6-P1-14 | receipt expiry 索引 | schema index | maintenance 不全表扫描 | S |
| CG6-P1-15 | receipt 分批维护 | bounded cleanup | 不长时间占用 writer | M |
| CG6-P1-16 | score policy Enum | catalog contract | 拼写错误启动即失败 | S |
| CG6-P1-17 | registry contract test | test | ID、module、ruleset、policy 唯一合法 | S |
| CG6-P1-18 | Sokoban 零分通关 | sentinel fix | 0 分合法完整通关仍写 attempt | S |
| CG6-P1-19 | worker 关闭预算 | lifecycle | 取消非关键读，只 drain 必要写 | M |
| CG6-P1-20 | storage recovery UI | recovery panel | 可查看 DB/spool/quarantine 状态 | L |
| CG6-P1-21 | quarantine 导出 | data service | 用户可导出，不直接删除 | M |
| CG6-P1-22 | 数据备份/恢复 | export/import | 原子、校验、可回滚 | L |
| CG6-P1-23 | 历史记录清理 | retention UI | 可按游戏/日期清理，pending 不误删 | M |
| CG6-P1-24 | optional Flask endpoint schema | strict query | stats 的无效 limit 不再静默忽略 | S |
| CG6-P1-25 | API 全 JSON 错误 | handler | 404/405/500 保持 JSON | M |
| CG6-P1-26 | HTTP retry 以 attempt 为键 | client fix | 不按 game/player 清除另一局 | M |
| CG6-P1-27 | 本机 profile repository | profile UUID | 无账号，显示名与数据身份分离 | L |
| CG6-P1-28 | 旧显示名迁移 | profile migration | 历史记录归属可解释 | L |
| CG6-P1-29 | 昵称持久化 | last-used profile | 重启后保留 | M |
| CG6-P1-30 | progress repository | API + migration | Sokoban/Zuma 进度可靠保存 | L |
| CG6-P1-31 | save slots repository | API + migration | 2048 存档版本化 | L |
| CG6-P1-32 | settings repository | API + migration | 窗口/音量/键位原子保存 | L |
| CG6-P1-33 | 统一 SaveController | common service | 移除 BaseGame/2048 双状态机 | XL |
| CG6-P1-34 | 结构化日志 | rotating logs | worker/migration/game 异常有 traceback | M |
| CG6-P1-35 | 迁移到 pytest | fixtures/markers | 任一测试可单独运行 | XL |
| CG6-P1-36 | core-only 测试命令 | no-api suite | 无 Flask/requests 可测试桌面核心 | M |
| CG6-P1-37 | GitHub Actions | OS matrix | Linux/Windows/macOS smoke | L |
| CG6-P1-38 | JUnit/coverage/artifacts | CI outputs | 失败日志与截图可下载 | M |
| CG6-P1-39 | Ruff + typing + dependency audit | quality gate | PR 自动执行 | M |
| CG6-P1-40 | 依赖锁定 | lock/constraints | 发布环境可复现 | M |
| CG6-P1-41 | LICENSE/NOTICE | legal | 代码和素材许可明确 | M |
| CG6-P1-42 | CHANGELOG/SemVer | release governance | schema/ruleset 变化可追踪 | M |
| CG6-P1-43 | 文档目录整理 | `docs/audits/adr/dev` | 根目录不堆积历史审查 | S |

---

## 8.3 P2：单机体验、输入、性能与可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG6-P2-01 | GameState Enum | transitions | 无散落魔法字符串 | L |
| CG6-P2-02 | InputManager | action map | 五款游戏统一输入状态 | L |
| CG6-P2-03 | IME | TEXTINPUT/TEXTEDITING | 中日韩组合输入可用 | M |
| CG6-P2-04 | 键位重映射 | binding UI | 冲突检测、恢复默认 | L |
| CG6-P2-05 | 键盘菜单导航 | focus model | 不用鼠标可完成主要操作 | L |
| CG6-P2-06 | 手柄支持 | controller map | launcher/五款游戏可用 | L |
| CG6-P2-07 | 音频系统 | BGM/SFX | 无设备时不崩，音量持久化 | L |
| CG6-P2-08 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG6-P2-09 | 高 DPI | DPI handling | 字体和图形清晰 | M |
| CG6-P2-10 | 字体 fallback | licensed fonts | 缺系统 CJK 字体仍可读 | M |
| CG6-P2-11 | 色弱符号 | shape/pattern | 颜色不是唯一信息通道 | L |
| CG6-P2-12 | 高对比/降低动态 | accessibility | 脉冲、抖动可关闭 | L |
| CG6-P2-13 | Clock/RNG 注入 | deterministic services | 相同 seed+输入可重现 | L |
| CG6-P2-14 | 渐进抽取纯规则 Engine | rules modules | 核心测试无需 SDL | XL |
| CG6-P2-15 | launcher 拆分 | app/state/render/data | `main()` 职责清晰 | L |
| CG6-P2-16 | 本机首页重做 | best/recent/progress/continue | 不以 Top 10 为唯一核心 | L |
| CG6-P2-17 | tie medal 修复 | rank-aware renderer | 并列第一均显示同等级 | S |
| CG6-P2-18 | recent 专用组件 | timeline UI | 不显示误导性奖牌 | M |
| CG6-P2-19 | 静态 Surface 缓存 | profiler-driven | 有尺寸/主题失效策略 | L |
| CG6-P2-20 | Zuma reaction FSM | explicit model | 重叠 reaction 可属性测试 | L |
| CG6-P2-21 | 可重复 benchmark | benchmark CLI | 带环境和 seed | M |
| CG6-P2-22 | 30–60 分钟 soak | stability suite | 线程/FD/内存稳定 | M |
| CG6-P2-23 | 游戏内规则页 | controls/rules/version | ruleset 可查看 | M |
| CG6-P2-24 | 崩溃恢复页 | crash UI | 返回菜单并显示日志位置 | M |

---

## 8.4 P3：游戏内容与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG6-P3-01 | Tetris 7-bag | optional comfort mode | 与当前模式分 ruleset | M |
| CG6-P3-02 | Tetris ghost/hold/lock delay | comfort features | 规则测试完整 | L |
| CG6-P3-03 | Snake 速度/穿墙/障碍 | local modes | 每模式独立最佳 | L |
| CG6-P3-04 | Snake 双人同屏 | local multiplayer | 不依赖网络 | L |
| CG6-P3-05 | 2048 撤销 | undo model | 存档/attempt 规则明确 | L |
| CG6-P3-06 | 2048 自动存档/继续 | save slot | 崩溃后可恢复 | L |
| CG6-P3-07 | 2048 棋盘尺寸 | modes | ruleset 分离 | M |
| CG6-P3-08 | Sokoban 选关/解锁 | progress UI | 练习与 campaign 分开 | L |
| CG6-P3-09 | Sokoban 星级/最佳推动 | local metrics | 规则明确 | M |
| CG6-P3-10 | Sokoban 死锁/提示 | analysis | 提示可关闭 | XL |
| CG6-P3-11 | Sokoban 编辑器 | XSB import/export | 地图验证、预览 | L |
| CG6-P3-12 | Zuma 训练/选关 | practice modes | 不混入完整通关 | L |
| CG6-P3-13 | Zuma 色弱辅助 | symbols | 球色可独立辨识 | M |
| CG6-P3-14 | Zuma 原创道具/轨道 | content | 每项有确定性测试 | XL |
| CG6-P3-15 | Zuma 轨道编辑器 | path tool | 合法、版本化、可预览 | L |
| CG6-P3-16 | 本机成就 | local achievements | 无账号、无遥测 | L |
| CG6-P3-17 | 离线每日挑战 | date seed | 完全离线 | L |
| CG6-P3-18 | 本地 replay | command log | 用于复盘/调试 | L |
| CG6-P3-19 | 中文/英文 | localization | 长文本布局测试 | L |
| CG6-P3-20 | Windows 桌面包 | installer/portable | 无 Python 可运行 | XL |
| CG6-P3-21 | macOS app bundle | package | 数据目录和关闭 smoke | XL |
| CG6-P3-22 | Linux package | AppImage/等效 | XDG 路径正确 | L |
| CG6-P3-23 | 自动发布 | tagged builds | smoke 通过才发布 | L |
| CG6-P3-24 | 截图/GIF/主页 | showcase | README 首屏展示玩法 | M |
| CG6-P3-25 | 社区文件 | contributing/security | Bug 模板含日志/版本 | S |
| CG6-P3-26 | 谨慎新增游戏 | template/contracts | 同时交付规则、记录、输入、测试 | XL |

---

## 9. 必须新增的测试

### 9.1 存储故障

```text
test_sqlite_full_keeps_durable_spool
test_sqlite_full_keeps_non_durable_mutation_retryable
test_free_space_then_retry_commits_score
test_no_such_table_does_not_delete_pending
test_sqlite_error_codes_drive_classification
test_unexpected_storage_error_never_silently_discards
```

### 9.2 legacy pending

```text
test_legacy_pending_nan_is_quarantined_without_startup_failure
test_legacy_pending_infinity_is_quarantined
test_legacy_pending_deep_nesting_is_bounded
test_legacy_pending_oversized_file_is_preserved
test_quarantine_serialization_failure_does_not_escape
test_unreadable_quarantine_directory_does_not_block_game
```

### 9.3 pending 状态

```text
test_durable_pending_eventually_reports_committed
test_durable_pending_eventually_reports_quarantined
test_pending_commit_invalidates_overlay_leaderboard
test_sokoban_pending_commit_updates_confirmed_total
test_2048_pending_commit_updates_confirmed_state
test_manual_retry_does_not_scan_on_render_thread
```

### 9.4 migration/schema

```text
test_unreadable_legacy_source_does_not_create_backup_each_launch
test_moved_identical_legacy_database_does_not_duplicate_attempts
test_pending_replay_preserves_finished_at
test_current_schema_null_attempt_uuid_is_detected
test_current_schema_invalid_status_is_detected
test_legacy_raw_extra_size_limit
test_legacy_extra_max_depth
```

### 9.5 游戏

```text
test_sokoban_zero_score_full_clear_is_recorded
test_tied_leaderboard_entries_share_medal
test_recent_entries_do_not_render_as_competition_rank
```

---

## 10. 性能与稳定性门槛

必须记录机器、操作系统、Python/打包版本、窗口尺寸和 seed。

1. pygame 主线程不得执行磁盘扫描或 SQLite；
2. `submit_score_async()` p99 ≤ 2 ms；
3. 手动 retry 调用本身 p99 ≤ 2 ms；
4. DB 锁、只读、满盘时，Future 在有界时间返回明确状态；
5. storage failure 不得删除最后一份 mutation；
6. 正常游戏目标 60 FPS，基准机 p95 ≤ 16.7 ms；
7. 保存、scan、leaderboard 不产生 >50 ms 主线程长帧；
8. 32 进程 spool 不丢记录；
9. 强制终止后 durable pending 可恢复；
10. 同一次 2048 只有一个 attempt；
11. 100 次游戏切换：
    - 线程回到基线；
    - FD 不持续增长；
    - Surface/内存稳定；
12. 30–60 分钟：
    - pending 最终清空或明确隔离；
    - 无未处理 SQLite lock；
    - 无持续内存增长；
13. 坏 DB、满盘、只读、坏 spool、坏旧库：
    - 游戏本身仍可启动；
    - 原文件不被覆盖；
    - 有用户可见恢复入口。

---

## 11. 稳定版本质量门禁

正式稳定桌面版本至少应满足：

- 全部 P0 关闭；
- 磁盘满不丢成绩；
- 任意坏 legacy pending 不阻断启动；
- pending 最终状态可被 UI 观察；
- 默认无 Flask/HTTP；
- 本地提交不阻塞 pygame；
- ruleset、attempt、personal best、recent 语义准确；
- profile、progress/save slot/settings 至少按发布范围完整实现，不能只有空表；
- 数据位于用户目录；
- migration、backup、export 完整；
- core-only 测试可运行；
- GitHub Actions 三平台 smoke；
- pytest/JUnit/coverage；
- formatter/lint/type/dependency audit；
- LICENSE、CHANGELOG、数据恢复文档；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代故障注入、迁移 fixture、并发测试和真实玩家测试。

---

## 12. 推荐实施顺序

### M0：关闭两个发布阻断

1. SQLite error-code 分类；
2. 满盘保留 mutation；
3. legacy pending 严格解析；
4. quarantine 不抛异常；
5. P0 故障注入。

### M1：完成本地保存闭环

- SaveEvent；
- pending→commit UI；
- 手动 retry 异步；
- 结算时间；
- legacy import 解耦；
- schema 行约束；
- reopen backoff。

### M2：完成本地数据产品

- profile UUID；
- nickname；
- progress；
- save slots；
- settings；
- backup/history/recovery UI。

### M3：工程化与体验

- pytest/CI/type/coverage；
- launcher/Input/IME/手柄；
- 音频；
- DPI/字体；
- 可访问性；
- Clock/RNG；
- 纯规则引擎；
- benchmark/soak。

### M4：内容与发行

- 五款游戏单机舒适功能；
- Sokoban/Zuma 编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 许可证和展示材料；
- 基础稳定后再新增游戏。

---

## 13. 最终判断

当前项目已经完成了从“AI 辅助小游戏原型”到“本地优先桌面应用架构”的关键转变。

本轮修复中，以下能力已经达到较好的工程水平：

- stable attempt UUID/revision；
- current/legacy ruleset 隔离；
- per-request durable spool；
- SQLite/spool/HTTP 统一 mutation；
- schema/index 修复与备份；
- receipt 重建；
- score policy；
- optional dependency；
- read/write executor；
- 保存感知的退出保护。

下一步不应继续扩大抽象层或增加网络平台复杂度，而应：

> **先保证满盘、坏 pending、坏旧库等极端情况下也绝不静默丢失本机成绩；随后集中建设本机档案、进度、存档、设置、可访问性和桌面发行。**

这条路线最符合“经典童年小游戏本地合集”的产品价值。
