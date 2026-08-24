# Classic Games Hub 第四次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 审查基线：`main` 分支 commit `34cc5c94be4cd03ef171f64bb0ec2dab893d6077`（`34cc5c9`）
> 对比基线：上次审查 commit `f88a6ffed22a25980abda9ce6909a403631870bf`（`f88a6ff`）
> 产品定位：**本地运行、单机为主、默认无网络依赖的经典小游戏合集**
> 审查范围：`client/`、`game_service/`、`server/`、`tests/`、启动脚本、依赖和仓库文档

---

## 0. 执行摘要

本轮修复方向是正确的，而且完成了项目架构上的关键转折：

- 默认启动路径不再依赖 Flask、端口或网络；
- 本机 SQLite 成为默认记录仓储；
- Flask 被降为可选 API 适配器；
- 数据库迁移到操作系统用户数据目录；
- 新增 attempts、schema version、幂等响应快照和持久待保存队列；
- 上轮发现的 2048 迟到输入、胜利后残留输入、HTTP `drain()` 竞态、完整 payload 幂等、低分更新时间和 Flask import 副作用等问题已经得到实质修复。

这些变化与“本地经典小游戏合集”的定位高度一致。无需建设账号、云端排行榜、反作弊、赛季或匹配系统。

但是，新本地仓储层目前存在几项新的发布阻断问题：

1. `LocalBackendClient.submit_score_async()` 名义上异步，实际上在调用线程同步执行 SQLite；数据库有写锁时，我实际测得调用阻塞约 **5.01 秒**，会重新冻结 pygame 主线程。
2. `pending_saves.json` 只做极弱的字段筛选。一个语法合法但字段异常的待保存项，可以让 `LocalBackendClient` 在启动重放时直接抛 `TypeError`，导致启动器无法创建。
3. outbox 仅有进程内 `threading.RLock`，并使用固定的 `.tmp` 文件名。16 个并发进程写入时，我多次复现只保留 **8–9 条/16 条**，并出现 `.tmp` 重命名 `FileNotFoundError`。
4. outbox 在调用仓储验证之前就序列化负载。`extra` 含不可 JSON 序列化值时，本应返回稳定 `StoreError`，当前却会从 outbox 直接抛 `TypeError`。
5. 2048 或推箱子的一次运行如果第一次保存进入 pending、随后更高最终分保存成功，旧 pending 再重放时会形成第二条 attempt。当前数据库自增行 ID 不能作为跨失败重试的稳定游戏会话身份。
6. 查询语义仍未完全完成：保存响应中的 rank、排行榜 tie 时间、`mode/ruleset_version/status`、进度/存档/设置表，以及退出时的未保存保护仍有明显缺口。

因此，本轮结论是：

> **本地优先架构已经落地，但“本地保存一定不阻塞、一次运行只对应一次 attempt、待保存文件不会损坏启动”这三个基础保证尚未成立。**

正式发行前应先完成本任务书的 P0。之后再做输入、音效、存档、关卡编辑器和桌面打包。

---

## 1. 审查方法、证据与限制

### 1.1 已完成的检查

- 锁定并阅读当前 commit 的生产代码、测试、脚本和文档；
- 与上次审查的 F01–F13 逐项对照；
- 执行：

```bash
python -m compileall -q client game_service server tests
```

当前 Python 源码通过语法编译；

- 对本地仓储执行针对性可运行复现；
- 对数据库模型、outbox、迁移、输入队列、退出路径、排行榜查询和可选 Flask 适配器进行控制流检查；
- 统计测试结构和工程文件。

### 1.2 本轮实际复现结果

| 场景 | 实际结果 | 结论 |
|---|---|---|
| SQLite 持有 `BEGIN IMMEDIATE` 写锁后调用 `submit_score_async()` | 调用约 **5.012 秒**后返回 `database_unavailable` | “异步”方法实际阻塞调用线程 |
| `pending_saves.json` 包含 `{"request_id": "...", "bogus": 1}` | 构造客户端时抛 `TypeError: unexpected keyword argument 'bogus'` | 合法 JSON 的坏记录可阻断启动 |
| 16 个进程并发向同一 outbox 添加记录 | 多轮只保留 8–9 条，并出现 `.tmp -> pending_saves.json` 的 `FileNotFoundError` | outbox 不具备跨进程安全性 |
| `extra={"bad": {1, 2}}` | outbox 的 `json.dumps()` 抛 `TypeError` | 验证顺序错误，稳定错误契约被绕过 |
| 2048 首次保存因锁进入 pending，随后最终分保存成功，再重放 pending | 同一次运行产生 2 条 attempt | 缺少稳定 `attempt_uuid` |
| 显式提交不存在的 `submission_id=999999` | 静默插入新 attempt | stale ID 没有返回冲突/不存在 |
| Alice 最佳 1000、Bob 900，Alice 新局 100 | 新局响应 rank=3，但排行榜 Alice rank=1 | 写响应 rank 与个人最佳榜语义不一致 |
| Alice 先得 10、Bob 后得 100、Alice 最后得 100 | tie 排序用 Alice 的旧 10 分记录时间 | `MAX(score)` 与 `MIN(created_at)` 来自不同 attempt |
| 外部旧库存在 `scores` 表但缺 `extra` 列 | 新本地服务初始化失败，health 为 false | 外部旧库 schema 校验不足，坏旧库拖垮新库 |

### 1.3 环境限制

当前审查环境没有安装 `pygame` 和 `Flask`，因此未完整执行仓库自己的原生 headless 测试、窗口渲染和 Flask 测试。

本次已独立完成：

- 语法编译；
- 本地仓储可执行复现；
- 代码级逐项验收；
- 测试结构检查。

仓库文档记录了 107 个聚合 PASS、固定 seed 压力和渲染/存储基准；这些结果可以作为项目自测记录，但本报告不把它们声称为本次环境独立复现的结果。当前 `tests/regression.py` 可数到 99 个编号子进程场景，压力脚本另包含玩法、渲染、存储、资源和并发检查。

---

## 2. 上一轮问题修复验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| 最终失败成绩只在 HTTP 客户端内存 | **方向已修，闭环未完成** | 默认本地模式加入磁盘 outbox；但 outbox 校验、并发和稳定会话身份仍有 P0 |
| 结算页与启动器两套重试产生重复请求 ID | **基本修复** | UI 人工重试复用原 request ID；仍可能因 milestone/final 没有稳定 attempt ID 而拆成两条 |
| HTTP `drain()` 先于完成回调返回 | **修复到位** | HTTP 客户端已把归档纳入完成路径；本地客户端同步实现使 `drain()` 恒真，但带来主线程阻塞 |
| 2048 无效果方向阻塞后续队列 | **修复到位** | `_move()` 返回 bool，动画结束后连续丢弃无效果命令 |
| 2048 胜利前队列在继续后迟到执行 | **修复到位** | win、gameover、pause、reset 和 continue 边界已清理队列 |
| “成绩已保存”与本局/个人最佳混淆 | **基本修复** | attempts 与个人最佳开始分离，UI 能显示“本局已记录/新纪录”；attempt 生命周期仍不完整 |
| 低分 2048 更新错误刷新最近活动 | **修复到位** | no-op 不再更新 attempt 时间 |
| request ID 未绑定完整 payload | **修复到位** | 使用 canonical payload hash，冲突返回稳定错误 |
| 幂等记录指向已删除成绩行 | **修复到位** | attempt 不再因聚合最佳被删除，幂等表保存响应快照 |
| 永久性 4xx 进入无限重试 | **修复到位** | HTTP 和本地路径开始区分 retryable |
| retry 队列先清空再调度 | **HTTP 路径到位** | 本地 outbox 采用另一实现，存在新的并发/校验问题 |
| Flask import 写磁盘 | **修复到位** | 已使用 `create_app(config)`，导入本身不初始化数据库 |
| 默认运行仍依赖 Flask | **修复到位** | `run.sh` 默认直接启动本地 SQLite 版 launcher |
| Tetris 同义键和跨方块大 dt | **保留修复** | 未发现回退 |
| Snake 长停顿补多步 | **保留修复** | 未发现回退 |
| Sokoban 重复计分/跳关全通关 | **保留修复** | 未发现回退 |
| Zuma 多 pending 连锁 | **保留修复** | 未发现回退 |

### 总体评价

上轮建议的核心方向已经正确落地。当前问题主要来自新仓储的边界，而不是旧游戏规则漏洞重新出现。

---

## 3. 当前高优先级问题

## 3.1 CG4-F01：本地“异步”接口同步执行 SQLite，写锁时冻结主线程

- **优先级**：P0
- **证据**：已复现
- **位置**：
  - `game_service/local_backend.py:159-188`
  - `game_service/local_backend.py:248-256`
  - `game_service/store.py:128-132`

当前四个所谓异步方法都只是：

```python
return completed_future(self.sync_operation(...))
```

`submit_score_async()` 会先在调用线程执行完整 outbox 写入和 SQLite `BEGIN IMMEDIATE`，再构造一个已经完成的 Future。

SQLite 连接设置：

```text
timeout = 5.0 seconds
busy_timeout = 5000 ms
```

当另一个连接持有写锁时，pygame 事件/渲染线程会等待约五秒。

启动器构造 `LocalBackendClient` 时还会同步调用 `retry_failed_saves()`。若存在多条 pending 且数据库被锁，启动阶段可能按条累计等待。

### 修复要求

- 引入单一 `LocalWriteWorker`；
- `submit_score_async()` 必须先返回真正未完成的 Future；
- 主线程调用本身在锁冲突场景下应于 1–2 ms 内返回；
- writer 使用短 busy timeout；
- 超时后保留/确认 durable spool，而不是等待五秒；
- leaderboard/recent/health 若继续使用 async 命名，也应在 worker 中执行或改名为同步接口并证明耗时有界；
- pygame/渲染线程中不得直接执行磁盘 I/O。

---

## 3.2 CG4-F02：合法 JSON 的异常 outbox 记录可以阻断整个程序启动

- **优先级**：P0
- **证据**：已复现
- **位置**：`game_service/local_backend.py:35-49`、`261-276`

当前读取只保留：

```text
item 是 dict
且 request_id 是 str
```

然后启动构造函数立即重放：

```python
self.store.record_score(**payload)
```

没有校验允许字段、字段类型、request ID 格式或 payload hash。

### 影响

- 手工损坏；
- 旧版本写入不同字段；
- 程序中断留下半兼容数据；
- 恶意或意外修改；

都可能让启动器在创建 backend 时直接崩溃。

### 修复要求

- 定义版本化 `PendingSaveEnvelope`；
- 每条独立解析；
- 未知字段、缺字段、类型错误进入 quarantine；
- 一条坏记录不得阻止其他记录重放；
- 启动器显示“已隔离 1 条无法恢复的待保存记录”；
- 保留原始文件供用户导出/诊断。

---

## 3.3 CG4-F03：outbox 不具备跨进程安全性

- **优先级**：P0
- **证据**：已复现
- **位置**：`game_service/local_backend.py:28-86`

`threading.RLock` 只保护同一 Python 对象；独立启动两款游戏、两个 launcher 或两个进程时没有作用。所有进程还共同使用：

```text
pending_saves.json.tmp
```

典型竞争：

```text
进程 A 读旧列表
进程 B 读旧列表
A 写 tmp 并 replace
B 写同一个 tmp / replace
最终覆盖、丢记录或 rename 失败
```

### 修复选择

二选一，并明确产品策略：

#### 方案 A：单实例

- 运行 launcher 时取得跨进程文件锁；
- 第二实例显示“应用已运行”；
- 独立游戏也复用同一实例或使用 IPC。

#### 方案 B：允许多实例

- 每个 pending write 使用独立文件，如 `pending/<request_id>.json`；
- 写入使用唯一临时文件后原子 rename；
- 或建立单独 outbox SQLite/日志；
- 需要跨进程锁或冲突安全的 append/claim 机制。

对本项目，**每 request 一个 spool 文件**通常比共享 JSON 数组简单、可靠。

---

## 3.4 CG4-F04：outbox 在规范化和验证之前序列化负载

- **优先级**：P0
- **证据**：已复现
- **位置**：`game_service/local_backend.py:200-236`

流程目前是：

```text
outbox.add(raw payload)
→ store.record_score()
→ store 内部才 normalize/validate
```

`extra` 含 set、bytes 或 NaN 等非法 JSON 值时，outbox 会先抛异常，调用方得不到 `invalid_extra`。

### 修复要求

- 把 payload 规范化提取为共享公共函数；
- 先验证、canonicalize 和生成 hash；
- 再写 outbox；
- outbox 写入捕获 `TypeError/ValueError/OSError`；
- 存储和可选 HTTP 必须使用同一请求模型，避免校验规则漂移。

---

## 3.5 CG4-F05：一次游戏运行在失败恢复后可能拆成多个 attempt

- **优先级**：P0
- **证据**：已复现
- **位置**：
  - `client/games/game_2048.py`
  - `client/games/sokoban.py`
  - `game_service/store.py:347-384`

当前继续更新同一局依赖数据库返回的整数 `submission_id`。

若首次 milestone 保存失败：

- 客户端没有数据库行 ID；
- 最终分数保存成功时插入新 attempt；
- 旧 pending 日后重放，又插入另一个 attempt。

我复现了同一 2048 运行被记为 100 和 200 两条 attempt。

### 根因

数据库行 ID 是仓储内部标识，不是游戏会话的稳定身份。

### 修复要求

游戏运行开始时由客户端生成：

```text
attempt_uuid
revision
```

后续 milestone、final、失败重试和崩溃恢复全部使用同一 `attempt_uuid`：

- 唯一约束：`attempt_uuid`；
- revision 单调递增；
- 旧 revision 不得覆盖新 revision；
- 分数按游戏规则单调或以 final 状态为准；
- outbox 只保存 attempt mutation，不再依赖数据库自增 ID。

---

## 3.6 CG4-F06：显式 stale/mismatched `submission_id` 静默创建新记录

- **优先级**：P1
- **证据**：已复现
- **位置**：`game_service/store.py:347-368`

传入 `submission_id` 后若查询不到匹配行，`attempt` 仍为 `None`，随后走插入分支。

### 正确语义

- 未传 ID：创建；
- 显式传 ID 且存在、身份匹配：更新；
- 显式传 ID 但不存在：404；
- ID 存在但 game/player 不匹配：409；
- 使用 `attempt_uuid` 后，可逐步废弃这个整数更新接口。

---

## 3.7 CG4-F07：写入响应 rank 与个人最佳榜不一致

- **优先级**：P1
- **证据**：已复现
- **位置**：`game_service/store.py:386-414`

保存响应 rank 使用本次 attempt 的 `stored_score`，而排行榜按每位玩家的 `MAX(score)`。

例如 Alice 已有 1000、Bob 900，Alice 新局 100：

- 本局响应 rank=3；
- 个人最佳榜 Alice rank=1。

### 修复要求

选择一种语义：

- 返回 `personal_best_rank`，按 `personal_best` 计算；推荐；
- 或从写响应移除 rank；
- 不要把本次 attempt 的相对位置叫作个人排行榜名次。

---

## 3.8 CG4-F08：排行榜 tie 时间来自无关的旧 attempt

- **优先级**：P1
- **证据**：已复现
- **位置**：`game_service/store.py:424-444`

查询：

```sql
SELECT player, MAX(score), MIN(created_at)
FROM attempts
GROUP BY player
```

`MAX(score)` 和 `MIN(created_at)` 可以来自不同记录。

### 修复要求

- 使用窗口函数选择每位玩家的最佳 attempt；
- tie 时间必须来自该最佳 attempt；
- 或并列分数显示同 rank，二级排序仅使用规范化玩家名，不宣称“先达到者优先”。

---

## 3.9 CG4-F09：外部旧数据库 schema 不兼容时会拖垮新仓储

- **优先级**：P1
- **证据**：已复现
- **位置**：`game_service/store.py:483-540`

同库 embedded legacy 路径会验证必需列；外部 legacy 路径只检查 `scores` 表存在，然后直接 SELECT 固定列。

### 影响

一个缺列或非预期旧库会让新本地数据库服务整体不可用，尽管新库本身可以正常创建。

### 修复要求

- 对外部旧库做完整列集合检查；
- 逐行规范化玩家名、分数、extra 和时间；
- 不兼容旧库应跳过并显示迁移通知，不得禁用新库；
- 迁移失败与新库不可写必须使用不同错误文案；
- 增加 corrupt row、缺列、错误类型和超大 extra 测试。

---

## 3.10 CG4-F10：`mode`、`ruleset_version`、`status` 只是数据库默认值

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：
  - `game_service/catalog.py`
  - `game_service/store.py:162-175`
  - `record_score()` 与查询方法

catalog 已有 `ruleset_version`，attempts 也有三个字段，但写入接口不接受它们，查询也不筛选。所有记录实际上都是：

```text
mode=classic
ruleset_version=1
status=completed
```

### 影响

未来加入：

- Tetris 舒适模式；
- Snake 穿墙；
- Sokoban 练习；
- 规则计分修订；

都会混在同一个最佳榜中。

### 修复要求

在 schema v2 完整接入：

```text
profile_id
mode
ruleset_version
status
```

所有 personal best、recent、stats 查询必须按明确维度过滤。

---

## 3.11 CG4-F11：progress/save_slots/settings 是未接入的空壳

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：`game_service/store.py:186-207`

已有表，但没有公共仓储方法、服务接口、UI、迁移测试或原子保存测试。

### 建议

二选一：

- 近期就实现并使用；
- 或在 schema v1 不创建它们，等有完整接口时通过迁移增加。

已有空表容易让文档和开发者误以为功能已完成。

---

## 3.12 CG4-F12：同 request ID 不同 outbox payload 被静默忽略

- **优先级**：P1
- **证据**：已复现
- **位置**：`PersistentSaveOutbox.add()`

outbox 只判断 request ID 是否存在：

```python
if not any(item["request_id"] == payload["request_id"]):
    append
```

如果同 ID、不同分数或 extra，旧负载被静默保留。服务端仓储能检测 hash 冲突，但 outbox 在到达仓储前就改变了语义。

### 修复要求

- envelope 保存 payload hash；
- 同 ID 同 hash：幂等；
- 同 ID 不同 hash：明确 conflict，隔离并记录；
- 禁止静默丢弃新负载。

---

## 3.13 CG4-F13：outbox 恢复策略会静默丢弃部分记录

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：`_read_unlocked()`

合法 JSON 但顶层不是 list，或 list 内部分项缺 `request_id` 时：

- 可能返回空列表；
- 没有 recovery notice；
- 原文件也不一定被隔离；
- 用户以为没有待保存项。

### 修复要求

- 顶层 schema 严格；
- 每条有版本、hash、创建时间、尝试次数；
- invalid、corrupt、unsupported 分开处理；
- quarantine 文件名使用 UUID/纳秒，避免同秒冲突；
- UI 提供“导出待恢复数据”。

---

## 3.14 CG4-F14：退出、窗口关闭、鼠标返回和重置没有统一未保存保护

- **优先级**：P1；若 outbox 也不可写则会丢数据
- **证据**：代码路径确定
- **位置**：
  - `client/common/ui.py:441-460`
  - overlay 按钮回调
  - 各游戏 reset/return 回调

键盘 Esc 对“失败且不 durable”提供二次确认，但：

- `pygame.QUIT` 直接退出游戏循环；
- overlay 的“返回菜单”按钮可能直接设置 `running=False`；
- R/reset 按钮会建立新 session；
- 不同游戏有自己的 reset 逻辑。

### 修复要求

所有破坏性路径必须调用同一个：

```python
request_destructive_action(action)
```

统一判断：

- 保存中；
- 已 durable pending；
- 完全未落盘；
- 永久错误。

窗口关闭、鼠标按钮、键盘和程序退出必须行为一致。

---

## 3.15 CG4-F15：本地读取错误被伪装成“没有数据”

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：`LocalBackendClient.leaderboard/recent/stats`

SQLite 错误被转换为空数组或全零统计。UI 无法区分：

- 从未玩过；
- 数据库暂时锁定；
- 文件损坏；
- 目录不可读。

`health()` 也只执行读取，不检查实际写入与 outbox 可写能力。

### 修复要求

定义：

```text
StorageStatus
DataResult[T]
```

至少包含：

```text
ok
data
error_code
retryable
readable
writable
outbox_writable
recovery_notice
```

UI 应显示“本机记录暂时不可读”，而不是“暂无记录”。

---

## 3.16 CG4-F16：默认本地模式仍强制安装 Flask 和 requests

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：
  - `requirements.txt`
  - `environment.yml`
  - `client/common/ui.py` 的类型导入
  - `client/common/network.py`

虽然 Flask/HTTP 已是可选适配器，核心依赖文件仍要求：

```text
flask
requests
pygame
```

BaseGame 的类型也主要写成 `BackendClient`，实际默认对象是另一个不继承它的 `LocalBackendClient`。

### 修复要求

建立 `pyproject.toml`：

```text
core: pygame
api extra: flask, requests
dev extra: pytest, ruff, mypy, hypothesis, coverage
```

定义结构化 `GameDataService` Protocol，游戏只依赖协议。

---

## 3.17 CG4-F17：“每次游玩记录”目前实际是“每次结算记录”

- **优先级**：P1，产品语义
- **证据**：代码路径确定

attempt 只在 gameover、胜利或特定里程碑时创建。玩家：

- 中途按 R；
- 返回菜单；
- 关闭窗口；
- 放弃一关；

不会形成 attempt。status 又始终是 completed。

### 选择

- 文档改为“每次完成/失败结算记录”；或
- 引入完整生命周期：

```text
in_progress
completed
failed
abandoned
practice
```

对本地统计而言，完整生命周期更有价值，但不需要联网身份或竞技验证。

---

## 3.18 CG4-F18：数据保留、迁移和恢复仍不完整

- **优先级**：P1/P2
- **问题**：

1. `save_requests` 没有保留期，会无限增长；
2. attempts 没有历史删除或重置入口；
3. 损坏数据库恢复只重命名主文件，WAL/SHM sidecar 的处理应明确；
4. 备份文件名只有秒级时间戳，重复操作可能冲突；
5. legacy row 没有完整 normalize；
6. 用户没有导出、导入、清空某游戏历史或重建个人最佳的 UI；
7. schema 迁移缺少失败回滚和集成测试矩阵。

---

## 3.19 CG4-F19：可选 LAN Flask 暴露不适合当前产品定位

- **优先级**：P2
- **证据**：产品与代码设计

README 允许显式绑定 `0.0.0.0`，但 API 没有身份或写入令牌。由于本项目不计划建设联网竞技平台，最简单合理的策略是：

- 默认和文档主路径仅支持 loopback；
- LAN 只标记为开发调试且不可信；
- 或增加一个简单、显式启用的本地调试 token；
- 不要为此建设账号系统和复杂鉴权。

---

## 4. 分模块审查

## 4.1 `game_service/local_backend.py`

### 优点

- 将桌面端从 HTTP 中解耦；
- 有用户数据目录；
- 有 durable pending 概念；
- 请求 ID 可以跨重试复用；
- 同一 API 可供现有 UI 平滑接入；
- 损坏数据库有保留副本的尝试。

### 主要问题

- 真异步缺失；
- 构造函数同步重放 outbox；
- outbox schema 太弱；
- 跨进程不安全；
- payload 先持久化后校验；
- 同 ID 不同 payload 静默保留旧值；
- malformed item 可阻断启动；
- 读错误吞成空数据；
- `failed_save_count()` 每帧读文件；
- `drain()` 恒真，名称容易误导；
- recovery notice 不覆盖 outbox 隔离；
- `close()` 不执行异步 writer flush，因为目前没有 writer。

## 4.2 `game_service/store.py`

### 优点

- 参数化 SQL；
- 请求 payload hash；
- 完整响应快照；
- attempts 不再为个人最佳聚合而删除；
- 低分 no-op；
- schema version；
- WAL；
- 迁移前备份；
- 旧库只读导入；
- 用户数据目录。

### 主要问题

- 5 秒 busy timeout 不适合渲染线程；
- 自增 `id` 被客户端当会话身份；
- stale submission ID 变成 insert；
- rank 计算语义不一致；
- tie 时间查询错误；
- mode/ruleset/status 没接入；
- progress/save/settings 没 API；
- legacy 外部 schema 校验不足；
- import rows 未完整验证；
- `replace` 已基本成为遗留参数；
- request 快照无清理；
- health 只读不写；
- 查询没有 profile/mode/version/status 维度；
- 平均分将不同模式和规则混合。

## 4.3 `client/common/ui.py`

### 已改善

- 保存状态可见；
- 失败可人工重试；
- 重试复用 request ID；
- ACK 后再刷新榜单；
- “本局已记录/新纪录”文案；
- 输入穿透和失焦暂停仍保持。

### 剩余问题

- 本地 Future 已完成，无法带来真正非阻塞；
- BaseGame 和 2048 仍维护两套保存状态机；
- 退出保护未覆盖所有入口；
- 状态仍是魔法字符串；
- broad `except Exception` 隐藏契约错误；
- overlay 和阴影 Surface 每帧创建；
- 固定窗口；
- 字体 fallback 不可靠；
- 无统一错误详情/日志入口；
- 无设置、音频、按键映射和可访问性服务。

## 4.4 启动器

### 已改善

- 默认使用 `LocalBackendClient`；
- 不需要 Flask；
- 显示本机最佳和最近记录；
- 可显示恢复/初始化错误；
- HTTP 只由显式环境变量启用。

### 剩余问题

- `main()` 仍高度集中；
- `failed_save_count()` 每帧读取并解析 JSON；
- 本地 health/read 方法仍同步；
- 游戏元数据仍有多处映射；
- 卡片布局固定；
- 主要依靠鼠标；
- 玩家名不持久化；
- 无 IME `TEXTINPUT/TEXTEDITING`；
- “榜单”仍是首页核心，比个人进度/继续游戏价值低；
- 无档案 ID，同名只靠字符串；
- 启动异常只给简短提示；
- 关闭前没有统一 writer/outbox 状态页。

## 4.5 2048

上轮的两个队列 Bug已经修复。

剩余重点：

- milestone 与 final 需要稳定 attempt UUID；
- 自己维护一套保存 Future，与 BaseGame 重复；
- reset 时 detach queued submission 仍依赖数据库行 ID；
- 无自动存档/继续；
- 无本地最高分专页；
- 无撤销；
- R 易误触；
- RNG 不可注入；
- 静态棋盘背景可缓存。

## 4.6 Tetris

上轮输入问题未见回退。

适合单机体验的后续项：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- 明确保留“自定义辅助旋转”，不要重新称为标准 SRS；
- 规则帮助页；
- 可注入 RNG；
- 静态网格缓存。

这些是单机舒适性，不需要竞技化。

## 4.7 Snake

- 双转向队列和长停顿策略仍有效；
- 可补速度选择、穿墙、障碍和双人同屏；
- 最佳成绩按模式分开；
- RNG 可注入；
- 食物和蛇增加形状/纹理，避免只靠颜色；
- 静态棋盘缓存。

## 4.8 Sokoban

核心计分和练习语义仍正确。

后续更适合本地产品的指标：

- 每关最少移动；
- 每关最少推动；
- 是否使用撤销；
- 星级；
- 解锁进度。

功能方面：

- 死锁检测；
- 提示；
- 选关；
- 固定逻辑窗口；
- XSB 导入；
- 关卡编辑器；
- 地图闭合、可达和静态死角验证。

## 4.9 Zuma

核心 pending 队列修复未见回退。

后续先做：

- 显式 reaction FSM；
- 多重/重叠反应属性测试；
- 可注入 RNG；
- 色弱符号；
- 训练和选关。

反应规则稳定后再增加：

- 原创道具球；
- 新轨道；
- 轨道编辑器；
- 关卡目标。

## 4.10 可选 Flask 适配器

### 已改善

- app factory；
- 复用同一 LocalGameStore；
- 默认 loopback；
- 统一 StoreError；
- 请求体限制；
- JSON 错误；
- optional 定位正确。

### 剩余问题

- README 的 LAN 调试应明确风险；
- 可选 HTTP schema 要跟随 attempt UUID/mode/version；
- `submitted_from` 没有本地产品价值；
- health 应区分 read/write；
- API 测试继续保留即可，不应反过来支配桌面架构。

## 4.11 测试

### 优点

- 大量 pygame 生命周期和玩法回归；
- 固定 seed 压力；
- 临时本地数据库；
- 可选 Flask 适配器测试；
- 默认数据库污染检查；
- 子进程超时；
- 渲染、资源、SQLite 并发基准。

### 当前盲点

- 只测无锁正常写入延迟，没有持锁时主线程返回时间；
- 并发测试写 SQLite，不测共享 JSON outbox；
- 不测 malformed outbox；
- 不测跨进程 outbox；
- 不测同一运行 pending→final→replay；
- 不测 stale submission ID；
- 不测 rank 与排行榜一致；
- 不测 tie 时间；
- 不测外部 legacy 缺列；
- 不测 QUIT/鼠标返回/reset 未保存保护；
- 不测 core-only 安装；
- 不测 mode/ruleset 查询；
- 仍是约 2,800 行单文件、自定义 runner、import 即执行；
- 无 pytest fixture/marker；
- 无 coverage、JUnit、CI 和失败截图。

---

## 5. 明确的产品非目标

为避免项目偏离，本任务书明确不建议建设：

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

可选 Flask 只保留为：

- 教学；
- 调试；
- 本机 API 示例；
- 可选数据查看适配器。

---

## 6. 推荐目标架构

```text
Launcher / Game
    │
    ├── GameEngine
    ├── Renderer / Input
    └── AttemptSession
            │
            ├── attempt_uuid
            ├── revision
            ├── game_id / profile_id
            ├── mode / ruleset_version
            ├── status
            ├── score / extra
            └── LocalGameService
                    │
                    ├── true async LocalWriteWorker
                    ├── LocalGameStore (SQLite)
                    └── DurableSpool
                           └── 每 request 独立文件或安全 outbox DB

Optional Flask Adapter
    └── 调用同一个 LocalGameService
```

### 6.1 `AttemptSession`

```python
@dataclass
class AttemptSession:
    attempt_uuid: UUID
    revision: int
    game_id: str
    profile_id: str
    mode: str
    ruleset_version: str
    status: AttemptStatus
    score: int
    extra: dict | None
```

规则：

- 游戏开始生成 UUID；
- milestone/final/重试不换 UUID；
- 每次状态变化 revision +1；
- 旧 revision 重放不得覆盖新 revision；
- complete/failed/abandoned 是明确终态；
- practice 不进入与 classic 相同的个人最佳。

### 6.2 写入策略

1. 主线程创建规范化 mutation；
2. 立即交给 writer 并得到 Future；
3. writer 以短超时写 SQLite；
4. 若锁冲突，写 durable spool；
5. Future 返回：
   - committed；
   - durable_pending；
   - permanent_failure；
6. 后台按 attempt UUID 和 revision 合并；
7. 退出时 flush；若 spool 已 durable，可安全退出。

### 6.3 多进程策略

必须明确一种：

- 默认单实例；或
- 多实例安全。

若支持独立运行五款游戏，建议 durable spool 采用：

```text
pending/
    <request_id>.json
```

每个文件有：

```text
schema_version
request_id
payload_hash
attempt_uuid
revision
created_at
payload
```

避免共享数组的 read-modify-write。

### 6.4 schema v2 建议

#### attempts

```text
attempt_uuid TEXT PRIMARY KEY
profile_id
game_id
mode
ruleset_version
status
revision
score
extra_json
started_at
finished_at
created_at
updated_at
```

#### request_receipts

```text
request_id PRIMARY KEY
payload_hash
attempt_uuid
revision
response_json
created_at
expires_at
```

#### progress

```text
profile_id
game_id
mode
ruleset_version
progress_json
updated_at
```

#### save_slots

```text
profile_id
game_id
slot
ruleset_version
state_json
updated_at
```

#### settings

```text
profile_id
key
value_json
updated_at
```

查询必须按：

```text
profile + game + mode + ruleset + status
```

---

## 7. 完整优化任务清单

### 优先级定义

- **P0**：会冻结主线程、丢失/重复记录、阻断启动或破坏一次运行身份；发布阻断。
- **P1**：本地数据语义、迁移、工程和测试基础；稳定版前完成。
- **P2**：体验、输入、性能、可访问性和规则解耦。
- **P3**：内容、桌面发行和社区建设。
- **S/M/L/XL**：相对工作量。

---

## 7.1 P0：本地保存正确性与非阻塞保证

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG4-P0-01 | 实现真正的本地写 worker | `LocalWriteWorker`、真实 Future | DB 被锁 5 秒时，`submit_score_async()` 调用本身 ≤2 ms 返回；pygame 主线程无 SQLite 调用 | 无 | L |
| CG4-P0-02 | 缩短锁等待并 durable fallback | 短 busy timeout、fallback policy | 锁冲突在设定预算内进入 durable pending；不会等待 5 秒 | P0-01 | M |
| CG4-P0-03 | 统一请求规范化 | `ScoreMutation`/schema | outbox、SQLite、HTTP 使用同一验证；非法 extra 返回稳定错误，不抛 `TypeError` | 无 | L |
| CG4-P0-04 | 版本化 outbox envelope | parser、schema、quarantine | 合法 JSON 坏记录不阻断启动；逐条隔离；有 recovery notice | P0-03 | L |
| CG4-P0-05 | 解决 outbox 跨进程竞争 | 单实例锁或 per-request spool | 32 进程并发：无丢记录、无临时文件冲突；或第二实例被明确拒绝 | P0-04 | L |
| CG4-P0-06 | 引入稳定 `attempt_uuid` | session API、DB unique key | 一次运行所有 milestone/final/retry 只有一条 attempt | P0-03 | XL |
| CG4-P0-07 | 引入 revision 与乱序合并 | monotonic revision | pending 低分晚到不创建新 attempt、不覆盖 final；响应为 stale/no-op | P0-06 | L |
| CG4-P0-08 | 废弃或严格化整数 `submission_id` | 404/409 契约 | 显式不存在/不匹配 ID 不得静默插入 | P0-06 | M |
| CG4-P0-09 | outbox 检测 request ID payload 冲突 | payload hash | 同 ID 同 payload 幂等；不同 payload 明确 conflict，不静默保留旧值 | P0-03/04 | M |
| CG4-P0-10 | 统一破坏性操作保护 | `request_destructive_action()` | Esc、QUIT、鼠标返回、R、重置和关闭全部遵循同一保存策略 | P0-01/04 | L |
| CG4-P0-11 | 建立可靠 flush/close | writer drain、spool ACK | close 返回时，写入已 commit 或 durable；不能只表示队列已提交 | P0-01/05 | M |
| CG4-P0-12 | 旧库导入容错 | schema/row validation、skip notice | 缺列或坏行旧库不阻断新库；旧库保持只读；用户能看到跳过数量 | 无 | L |
| CG4-P0-13 | 类型化保存与存储状态 | `SaveResult`、`StorageStatus` | UI 可区分 committed、durable pending、permanent failure、read failure | P0-01/03 | L |
| CG4-P0-14 | 建立 P0 回归套件 | 确定性单元/集成测试 | 覆盖 P0-01 至 P0-13；禁止用 sleep 猜测异步完成 | 全部 P0 | L |

### P0 必须新增的测试

```text
test_submit_async_returns_immediately_under_write_lock
test_locked_database_falls_back_to_durable_spool
test_invalid_extra_never_escapes_as_type_error
test_malformed_outbox_item_is_quarantined
test_one_bad_pending_item_does_not_block_good_items
test_concurrent_outbox_writers_do_not_lose_records
test_same_run_pending_milestone_and_final_become_one_attempt
test_stale_revision_cannot_override_final
test_missing_submission_id_target_is_rejected
test_outbox_request_id_payload_conflict
test_quit_button_and_window_close_share_unsaved_guard
test_reset_cannot_silently_discard_non_durable_result
test_close_means_committed_or_durable
test_incompatible_legacy_database_does_not_disable_new_store
```

---

## 7.2 P1：完成本地数据模型和工程基础

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG4-P1-01 | schema v2 | migration、attempt UUID/revision | 旧 v1 可备份迁移；失败原库不变 | P0-06/07 | XL |
| CG4-P1-02 | 接入 profile/mode/ruleset/status | 写入与查询契约 | personal best/recent/stats 不跨模式、规则版本或练习状态混合 | P1-01 | L |
| CG4-P1-03 | 完成 attempt 生命周期 | begin/finish/abandon | 文档中的“游玩次数”有精确定义；R/返回可记录 abandoned 或明确不计 | P1-01 | L |
| CG4-P1-04 | 修复保存响应 rank | `personal_best_rank` | 响应 rank 与本机最佳榜一致；低分新局不会返回 rank 3/榜 rank 1 | P1-02 | S |
| CG4-P1-05 | 修复 tie 查询 | best-attempt CTE/window query | tie 时间来自最佳 attempt；并列规则有文档和测试 | P1-02 | M |
| CG4-P1-06 | 实现 progress repository | save/load/delete API | Sokoban/Zuma 进度可原子保存，带规则版本 | P1-01/02 | L |
| CG4-P1-07 | 实现 save slot repository | 版本化存档 API | 2048 可保存/恢复；坏存档不覆盖现有状态 | P1-01/02 | L |
| CG4-P1-08 | 实现 settings repository | profile/global settings | 窗口、音量、键位可原子保存并迁移 | P1-01 | M |
| CG4-P1-09 | 清理未使用 schema | migration policy | 未实现功能不提前建空表，或每张表均有 API 和测试 | P1-06~08 | S |
| CG4-P1-10 | 数据保留策略 | receipt/outbox pruning | 幂等快照有期限；不会无限增长；pending 不被误删 | P1-01 | M |
| CG4-P1-11 | 数据导出、导入和重置 | service API | 可导出 attempts/progress/settings；导入校验且可回滚 | P1-01/06~08 | L |
| CG4-P1-12 | 增强迁移备份 | 唯一备份名、sidecar policy | 同秒多次迁移不覆盖；WAL/SHM 处理明确 | P1-01 | M |
| CG4-P1-13 | 定义 `GameDataService` Protocol | 中立接口模块 | 游戏不依赖具体 Local/HTTP 类；mypy 可检查两个适配器 | P0-13 | M |
| CG4-P1-14 | 拆分可选依赖 | `pyproject.toml` extras | `pip install .` 仅需 pygame；`.[api]` 才装 Flask/requests | P1-13 | L |
| CG4-P1-15 | 统一游戏注册表到数据维度 | descriptor 扩展 | entrypoint、mode、ruleset、尺寸和主题只有一个来源 | P1-02 | M |
| CG4-P1-16 | 拆分 launcher | App/State/Renderer/DataPanel/GameRunner | 主循环不再同时负责仓储、布局、导入和输入 | P1-13/15 | L |
| CG4-P1-17 | 重做本地首页 | 个人最佳、最近完成、进度、继续 | 默认不强调“在线”；能看到继续游戏和关卡进度 | P1-02/06/07 | L |
| CG4-P1-18 | 持久化本机档案 | default profile + optional family profiles | 无账号；昵称和设置跨启动保存；同名显示名不再是数据身份 | P1-02/08 | L |
| CG4-P1-19 | 缓存本地状态摘要 | in-memory snapshot/events | 每帧不读取 outbox 或数据库；保存完成后事件驱动刷新 | P0-01/P1-16 | M |
| CG4-P1-20 | 强化可选 Flask 边界 | loopback docs、optional token | 默认仅本机；LAN 明确不安全或要求显式 token | P1-13 | S |
| CG4-P1-21 | 迁移 pytest | fixtures、markers、parametrize | 测试可单独运行；不再 import 即执行 | P0-14 | XL |
| CG4-P1-22 | 建立 CI | GitHub Actions | Linux/Windows/macOS smoke；上传 JUnit、日志和失败截图 | P1-14/21 | L |
| CG4-P1-23 | 质量门禁 | Ruff、类型、coverage、依赖审计 | PR 自动检查；纯规则模块覆盖率目标可见 | P1-14/21 | M |
| CG4-P1-24 | 结构化日志与恢复页 | rotating log、recovery UI | 数据库/outbox/游戏异常有日志、版本、恢复操作 | P0-13/P1-16 | M |
| CG4-P1-25 | 跨平台命令入口 | console scripts | Windows 无需 Bash；Conda 非必需；依赖缺失提示准确 | P1-14 | M |
| CG4-P1-26 | 整理文档目录 | `docs/adr`、`docs/audits`、`docs/dev` | 根目录不堆积历史审查文件；状态记录与实际提交一致 | 无 | S |
| CG4-P1-27 | 完整架构和数据文档 | ADR、schema、backup、troubleshooting | 新贡献者能从空环境安装、迁移、测试和恢复数据 | P1-01~26 | M |

---

## 7.3 P2：单机体验、输入、性能和可访问性

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG4-P2-01 | 统一 InputManager | action map、物理键状态、队列 | 五款游戏不再散落处理 keycode；失焦/暂停统一清理 | P1-16 | L |
| CG4-P2-02 | 正确支持 IME | TEXTINPUT/TEXTEDITING 控件 | 中文、日文、韩文组合输入可用 | P2-01 | M |
| CG4-P2-03 | 键位重映射 | binding UI、冲突检测 | 可恢复默认；设置持久化 | P1-08/P2-01 | L |
| CG4-P2-04 | 键盘与手柄导航 | focus/controller layer | 不用鼠标可完成启动、暂停、继续、重试和返回 | P2-01 | L |
| CG4-P2-05 | 音频系统 | BGM/SFX、音量和静音 | 无音频设备不崩；切换游戏正确释放 | P1-08 | L |
| CG4-P2-06 | 逻辑分辨率和缩放 | resizable、letterbox、DPI | 常见分辨率不裁切；高 DPI 清晰 | P1-16 | XL |
| CG4-P2-07 | 字体和资源管理 | licensed font fallback、asset manager | 缺系统中文字体仍可读；缺资源有降级 | P2-06 | M |
| CG4-P2-08 | 可访问性选项 | 色弱符号、高对比、降低动态 | 颜色不是唯一信息通道；脉冲/抖动可关闭 | P1-08/P2-07 | L |
| CG4-P2-09 | 统一 Clock 和 RNG | injectable clock/random | 相同 seed+命令可重现；暂停不推进逻辑时间 | P1-15 | L |
| CG4-P2-10 | 渐进抽取纯规则 Engine | 五款规则模块 | 核心规则测试无需 pygame/SDL | P2-09 | XL |
| CG4-P2-11 | 统一保存 session | `AttemptSession` integration | 2048 不再维护与 BaseGame 平行的保存状态机 | P0-06/P1-03 | L |
| CG4-P2-12 | 静态 Surface 缓存 | gradient/grid/panel cache | 由 profiler 驱动；有尺寸/主题失效规则 | P2-06 | L |
| CG4-P2-13 | Zuma reaction FSM | 显式状态和属性测试 | 多重重叠反应确定、可重放 | P2-10 | L |
| CG4-P2-14 | 可重复性能基准 | benchmark command | 记录机器、系统、窗口和版本；包含锁竞争场景 | P0-01/P2-10 | M |
| CG4-P2-15 | 长时间稳定性 | soak test | 100 次切换、30–60 分钟运行无持续资源增长 | P1-24/P2-14 | M |
| CG4-P2-16 | 存档/继续 UI | continue panel | 2048 可继续；版本不兼容有提示 | P1-07/17 | M |
| CG4-P2-17 | 数据管理 UI | backup/history/reset | 用户无需操作数据库文件即可备份和清理 | P1-11/17 | M |
| CG4-P2-18 | 游戏内帮助和规则页 | controls/rules screen | 计分、旋转、练习和存档规则可查看 | P1-15 | M |

---

## 7.4 P3：游戏内容、桌面发行和长期完善

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG4-P3-01 | Tetris 舒适模式 | 7-bag、ghost、hold、lock delay 可选 | 保留当前自定义辅助旋转；模式分开记录 | P1-02/P2-10 | XL |
| CG4-P3-02 | Snake 多模式 | 速度、穿墙、障碍、双人同屏 | 每种模式有独立最佳和规则说明 | P1-02/P2-10 | L |
| CG4-P3-03 | 2048 完整本地体验 | 最高分、撤销、自动存档、棋盘尺寸 | attempt 不重复；撤销/继续规则有测试 | P1-07/P2-11 | L |
| CG4-P3-04 | Sokoban 关卡进度 | 选关、解锁、星级、最佳移动/推动 | 练习和顺序闯关分开 | P1-06/P2-10 | L |
| CG4-P3-05 | Sokoban 死锁与提示 | 静态死角、基础提示 | 提示可关闭；星级影响规则明确 | P2-10 | XL |
| CG4-P3-06 | Sokoban 编辑器 | XSB 导入导出、地图验证 | 拒绝未闭合、箱目标不等、明显不可达地图 | P3-04/05 | L |
| CG4-P3-07 | Zuma 训练与选关 | training、速度辅助、色弱符号 | 训练记录不混入完整通关 | P1-02/P2-08/13 | L |
| CG4-P3-08 | Zuma 原创内容 | 道具球、轨道、目标 | 每项有确定性测试；经典模式不变 | P2-13 | XL |
| CG4-P3-09 | Zuma 轨道编辑器 | 可视化路径工具 | 路径合法、版本化、可预览 | P3-08 | L |
| CG4-P3-10 | 本机成就与统计 | achievements/history | 无账号、无遥测；导入/replay 不重复触发 | P1-03/18 | L |
| CG4-P3-11 | 离线每日挑战 | 本地日期 seed | 完全离线；同版本同 seed 可重现 | P2-09/10 | L |
| CG4-P3-12 | 本地 replay | command log/viewer | 用于复盘和调试，不用于反作弊 | P2-09/10 | L |
| CG4-P3-13 | 本地化 | 中文/英文字符串资源 | 主要 UI 无硬编码；长文本布局通过 | P2-06/07 | L |
| CG4-P3-14 | Windows 桌面包 | installer/portable | 无 Python 环境可运行；升级不丢数据 | P1/P2 完成 | XL |
| CG4-P3-15 | macOS 桌面包 | signed/app bundle 规划 | 数据位于 Application Support；窗口/字体 smoke | P1/P2 完成 | XL |
| CG4-P3-16 | Linux 桌面包 | AppImage/等效方案 | XDG 数据目录正确；常见发行版 smoke | P1/P2 完成 | L |
| CG4-P3-17 | 自动发布 | tag workflow、checksums | 构建通过 smoke 后发布；失败不产生 release | P1-22/P3-14~16 | L |
| CG4-P3-18 | 许可证与素材清单 | LICENSE、NOTICE | 代码、字体、图形和音频来源明确 | 无 | M |
| CG4-P3-19 | 项目展示 | 截图、GIF、平台矩阵 | README 首屏说明本地优先和五款玩法 | P3-13~16 | M |
| CG4-P3-20 | 社区文件 | CONTRIBUTING、SECURITY、Issue 模板 | Bug 报告包含版本、日志和复现步骤 | P1-27 | S |
| CG4-P3-21 | 谨慎增加新游戏 | registry/template/contract tests | 每款新游戏同时交付规则、存储、输入、暂停和测试 | P1-15/P2-10 | XL |

### 适合新增的本地玩法

优先考虑规则通用、素材风险低、适合短局和键盘操作的类型：

- 扫雷；
- 打砖块；
- 四子棋；
- 五子棋；
- 数独；
- 华容道；
- 记忆翻牌；
- 连连看；
- 双人同屏球拍；
- 原创纵向射击；
- 弹球；
- 迷宫。

每款新游戏必须同时交付：

- 规则规格；
- 确定性测试；
- attempt/progress 语义；
- 暂停、失焦和返回；
- 键盘可操作；
- 可访问性；
- 资源许可；
- 长帧或边界测试。

---

## 8. 测试体系补充

## 8.1 本地仓储关键属性

- 同一 `attempt_uuid` 最终只有一条 attempt；
- revision 单调；
- stale mutation 不覆盖 final；
- 同 request ID 同 hash 幂等；
- 同 request ID 不同 hash 冲突；
- committed 或 durable 之后退出不会丢失；
- durable spool 跨重启恢复；
- 一条坏 spool 不影响其他条目；
- 多进程不会丢 pending；
- write lock 不阻塞 render thread；
- migration 可重复；
- 导入失败保留旧库。

## 8.2 查询一致性

- 保存响应 personal best rank 与榜单一致；
- tie 时间来自最佳 attempt；
- mode/ruleset/status/profile 过滤；
- practice 不进入 classic best；
- abandoned 不进入完成榜；
- recent 使用真实 attempt 时间；
- stats 的 attempts/completed/abandoned 定义准确。

## 8.3 UI/E2E

- DB 锁定时画面继续刷新；
- 保存进入 durable pending 后可安全返回；
- DB 与 outbox 同时不可写时，Esc/QUIT/按钮/R 行为一致；
- 恢复待保存后 UI 更新；
- 一条坏 pending 触发恢复通知但仍启动；
- 无 Flask、无 requests 的 core 安装可运行；
- IME；
- 键盘/手柄菜单；
- 高 DPI；
- 无音频设备；
- 色弱模式；
- 数据库迁移和备份 UI；
- 打包产物首次启动。

## 8.4 工具治理

- 迁移 pytest；
- 子进程 timeout 可配置；
- JUnit；
- coverage；
- 失败截图；
- 日志产物；
- Hypothesis/等效属性测试；
- 可选 mutation testing；
- 测试完成后检查仓库和真实用户目录未污染；
- PASS 数量由 runner/CI 自动汇总，不在工作记录里手工维护；
- 所有压力测试记录 seed；
- 增加持锁延迟和跨进程 outbox 压力，而不是只测无竞争 SQLite。

---

## 9. 性能与稳定性验收门槛

指标必须记录机器、操作系统、Python/打包版本和窗口尺寸。

1. pygame 主线程不得执行可能等待 SQLite 锁的调用；
2. 持有 5 秒 DB 锁时，`submit_score_async()` 本身 p99 ≤2 ms；
3. 锁冲突后在设定时间内进入 durable pending；
4. 正常游戏目标 60 FPS，指定基准机 p95 frame time ≤16.7 ms；
5. 保存、排行刷新和 outbox 解析不得造成 >50 ms 长帧；
6. 32 进程 outbox 压力：
   - 不丢记录；
   - 不损坏 JSON；
   - 不出现临时文件冲突；
   - 或单实例策略明确拒绝额外实例；
7. 进程在 pending 后强制终止，再启动必须恢复；
8. 同一次 2048 milestone/final 最终只有一个 attempt；
9. 切换五款游戏 100 次：
   - 线程数回到基线；
   - FD 不持续增加；
   - Surface/内存达到稳定平台；
10. 30–60 分钟自动游玩：
    - writer/spool 最终清空；
    - 无未处理 SQLite lock；
    - 无持续内存增长；
11. 数据库只读、损坏、磁盘满和不兼容旧库：
    - 不阻止纯游戏运行；
    - 不覆盖原文件；
    - 有可见恢复入口。

---

## 10. 稳定版本质量门禁

第一个正式稳定桌面版本建议至少满足：

- 全部 P0 关闭；
- 默认无 Flask/HTTP；
- 本地写入真正不阻塞渲染线程；
- pending 跨进程、跨重启可靠；
- 一次运行只有一个稳定 attempt；
- 退出、窗口关闭、重置不静默丢失非 durable 成绩；
- personal best、recent、attempt 和进度语义分离；
- mode/ruleset/status 已接入或明确没有这些模式；
- 数据位于操作系统用户目录；
- schema version、迁移、备份和导出完整；
- pytest + CI 可从空环境运行；
- core 安装不需要 Flask/requests；
- Windows、macOS、Linux 至少 smoke；
- 纯规则模块建议：
  - 行覆盖率 ≥90%；
  - 分支覆盖率 ≥85%；
- 全项目建议行覆盖率 ≥80%；
- formatter、lint、typing、依赖审计通过；
- README、LICENSE、CHANGELOG、数据位置和恢复说明完整；
- 默认不联网；
- 默认不上传遥测。

覆盖率不能替代规则规格、属性测试、锁竞争测试和真实玩家测试。

---

## 11. 推荐实施顺序

### M0：关闭本地保存发布阻断

依次完成：

1. 真正异步 writer；
2. 规范化请求；
3. 版本化、跨进程安全 spool；
4. stable attempt UUID/revision；
5. 退出保护和 flush；
6. legacy 容错；
7. P0 回归。

### M1：完成本地数据产品

- schema v2；
- mode/ruleset/status/profile；
- progress/save slots/settings；
- personal best/rank/tie；
- 数据导出和清理；
- 本地首页；
- 可选家庭档案。

### M2：工程可持续

- GameDataService Protocol；
- pyproject 与 optional extras；
- launcher 拆分；
- pytest；
- CI；
- 类型、覆盖率和日志；
- 跨平台命令；
- 文档整理。

### M3：体验和性能

- InputManager；
- IME；
- 键位和手柄；
- 音频；
- 缩放/DPI；
- 字体；
- 可访问性；
- Clock/RNG；
- 纯规则 Engine；
- 存档 UI；
- benchmark/soak。

### M4：内容和发行

- 五款游戏的本地舒适性功能；
- 推箱子和祖玛编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 许可证和展示材料；
- 基础稳定后再增加新游戏。

---

## 12. 最终判断

本轮修改是迄今最重要的一次架构修复：

- 项目终于不再把本机 HTTP 当作默认数据通路；
- Flask 的定位已经回到可选适配器；
- attempts 和个人最佳开始分离；
- app factory、幂等快照、用户数据目录和旧库导入均是正确方向；
- 上轮大多数玩法与 HTTP 生命周期问题已经关闭。

当前最需要避免的是：

> 因为无竞争环境中 SQLite 写入约 1 ms，就把同步数据库调用包装成 completed Future 并视为“异步”。

真实可靠性取决于锁竞争、文件损坏、多进程、崩溃和迁移异常。当前已经实际证明这些边界仍能冻结主线程、丢 pending 或阻断启动。

推荐下一步聚焦：

> **用稳定 attempt UUID、真正的单写线程和跨进程安全 durable spool，建立“本机成绩既不阻塞也不丢失”的最小可靠闭环。**

这不需要账号、公网竞技或云平台。完成 P0/P1 后，项目就具备成为可打包、可长期维护的本地经典小游戏产品的基础。
