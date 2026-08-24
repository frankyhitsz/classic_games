# Classic Games Hub 第九次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 当前基线：`main` 分支 commit `24918852a651aac565e0feabbfab0e157146ca45`（`2491885`）
> 对比基线：上一轮审查 commit `f51de90b0d17d4be0725ba439b3873621aa6c32a`
> 增量范围：14 个提交，核心实现始于 `c629354f55ee216a9ca8d21299dcbc54214da001`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 审查范围：客户端、五款游戏、本机档案、成绩与本机状态 journal、SQLite schema/迁移、可选 Flask、测试、CI 与发行治理

---

## 0. 执行摘要

本轮修复总体有效。上一轮提出的大部分问题已经得到实质性解决：

- score 之外的本机状态新增 schema v2 journal；
- state key 使用 Windows `msvcrt.locking` / POSIX `flock`；
- journal 的 put、parse、quarantine 和 compare-and-delete 使用同一把 key lock；
- `logical_revision`、`operation_id` 与 ruleset 被写入 state envelope；
- Sokoban/Zuma progress 有明确 schema 和单调 merge；
- state journal 与 SQLite 同时失败时，最新 operation 会保存在内存；
- `LocalStateEvent`、`NON_DURABLE_PENDING` 和状态查询已经出现；
- `ProfileController` 为异步档案操作加入 generation；
- 2048 使用 typed slot-load result、加载门禁、重试、新开确认和 slot schema 3；
- attempt identity 不再依赖可变显示名；
- state table fast path 开始检查主键/唯一约束与外键；
- profile 规范化冲突会合并或隔离子状态；
- score receipt 可在状态缓存淘汰后重建 committed 状态；
- CI 增加三平台、Python 3.12/3.13、日志 artifact、timeout 和 coverage 门槛。

现有架构已经明确、合理，不建议推倒重来：

```text
pygame Launcher / Game
    │
    ├── GameDataService
    ├── Local read worker
    └── Local write worker
            ├── SQLite
            ├── score spool
            └── keyed local-state journal

Optional Flask
    └── 仅用于成绩 API 调试
```

### 本轮对前次报告的两项正式纠正

#### 前次 F08：撤销

前次报告称 state journal 在 `os.replace()` 后没有同步目录。重新核对基线代码后确认该判断不成立：

- `PersistentStateOutbox.put()` 在 replace 后已经调用目录 `fsync`；
- `remove_if_current()` 在 unlink 后也已经调用目录 `fsync`。

本任务书不再把它列为缺陷或优化任务。

#### 前次 F23：接受当前取舍，不实施直接删除

锁文件使用的是稳定 inode。即使清理进程已经取得锁，也可能有 waiter 早已打开旧 inode：

```text
waiter 持有旧 inode 的文件描述符
→ 清理者 unlink
→ 新进程创建同名新 inode
→ waiter 和新进程分别锁两个 inode
→ split-brain
```

因此“按时间直接删除 lock 文件”的建议不安全。当前不删除是正确取舍。

若未来确实需要回收，必须先设计新的锁协议，例如：

- 全局目录代际；
- lock registry；
- SQLite lock table；
- 或单实例架构。

在协议不变的前提下，不应删除这些小型 lock 文件。

---

## 0.1 当前最重要的新发现

本轮最严重的问题不在 journal 文件层，而在 journal 与 SQLite 之间。

### P0-A：state journal 的顺序没有进入 SQLite

当前跨进程 key lock 只覆盖：

```text
读取/合并 journal
→ replace journal
```

锁在写 SQLite 前已经释放。

SQLite 的 profiles、settings、save_slots 写入没有保存 journal 的：

```text
logical_revision
operation_id
```

因此仍可发生：

```text
进程 A 发布旧状态 A，暂停在写 DB 前
进程 B 发布新状态 B，写 DB，删除 journal
进程 A 恢复，写旧状态 A
进程 A 看到 journal 已不存在，将删除视作成功
最终：DB 是旧值，journal 也不存在
```

我用等价状态模型复现的最终状态为：

```text
database = old
journal = none
old remove_if_current = success
```

这会影响：

- 档案改名；
- settings；
- 2048 autosave；
- `set_progress`；
- 任何未来的本机状态。

journal 层测试通过，并不能证明“journal + DB”整体满足 latest-value。

### P0-B：启动时仍可能把游戏启动到临时 guest 档案

当前启动顺序：

```text
last_profile_async 尚未完成
→ 用户点击游戏
→ queue_launch
→ retry_profile_save 创建/确保 default guest
```

如果 guest ensure 先完成：

```text
profile_ready = true
→ queued launch 在 default guest 下启动
→ last_profile 稍后返回真实档案
→ launcher 返回后才切换到真实档案
```

`ProfileController` generation 没有阻止它，因为 load 与 default ensure 都属于 generation 0；unresolved launch token 又允许在任意首次 resolve 后启动。

结果是本局成绩、进度和存档可能归入错误档案。

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的检查

- 锁定当前 `main`；
- 对比上一轮基线到当前 head 的 14 个提交；
- 逐文件审查：
  - `client/common/ui.py`
  - `client/common/network.py`
  - `client/profile_controller.py`
  - `client/launcher.py`
  - 五款游戏
  - `game_service/profile.py`
  - `game_service/catalog.py`
  - `game_service/progress.py`
  - `game_service/mutation.py`
  - `game_service/service.py`
  - `game_service/local_backend.py`
  - `game_service/store.py`
  - `server/app.py`
  - storage v2/v4/v5/v6 tests
  - regression/stress/CI runner
  - workflow、README、任务记录、ADR 和工程文件；
- 核对上一轮 F01–F26；
- 对 state file CAS 与 DB 写入组合做确定性最小模型；
- 对 profile startup load/ensure 完成顺序做状态模型；
- 核对当前 branch protection 和 status-check 配置；
- 特别复核用户指出的 F08 与 F23。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 主要路径与关键边界已经闭环 |
| **基本到位** | 原缺陷关闭，但相邻组合场景仍有缺口 |
| **部分到位** | 模块内成立，跨模块整体仍不成立 |
| **撤销** | 前次判断经重新核对不成立 |
| **已复现** | 使用等价可执行状态模型复现 |
| **代码路径确定** | 从当前控制流可直接确定 |
| **高概率竞态** | 需要特定完成顺序，但代码没有同步保证 |
| **待真机验证** | 需要 Windows/macOS/Linux 实机证明 |
| **产品任务** | 不属于 Bug，但符合本地单机产品方向 |

### 1.3 限制

当前审查环境未安装 pygame 和 Flask，也没有本地仓库归档，因此本次没有独立完整运行：

- 107 项 gameplay regression；
- 90 项 storage/upgrade/lifecycle tests；
- 20,000 步 stress；
- 渲染与本机保存 benchmark；
- wheel smoke。

仓库 `task.md` 报告这些检查已经通过，并记录 CI #15 全矩阵通过及后续 fixture 隔离修复。这些可作为项目自测证据，但不是本次环境的独立复现结果。

当前仓库已经有 GitHub Actions workflow，但：

- `main` 仍未启用 branch protection；
- required status checks 为关闭状态；
- 本次连接未返回当前 head 的 completed status。

---

## 2. 上一轮 F01–F26 验收矩阵

| 上一轮项目 | 当前状态 | 本轮判断 |
|---|---|---|
| F01 state journal compare-and-delete TOCTOU | **文件层修复到位** | key lock 保护 put/remove；新发现 DB 层没有 revision CAS |
| F02 临时 slot load 被当作无存档 | **修复到位** | typed `SlotLoadResult` 与输入门禁 |
| F03 改名后 attempt 冲突 | **修复到位** | identity 不再比较 player |
| F04 state journal 写失败无最后副本 | **基本到位** | `_non_durable_state` 已有；superseded 清理仍有缺口 |
| F05 profile Future 无 generation | **部分到位** | `ProfileController` 已有；startup load/default ensure 仍同 generation 竞争 |
| F06 state journal 未冻结 ruleset | **当前 v2 到位** | 新 operation 会冻结；v1 升级仍依赖升级时 catalog |
| F07 offline progress last-writer-wins | **journal 层到位** | progress 会 merge；DB 端到端 ordering 仍缺 revision |
| F08 缺目录 fsync | **撤销** | 基线代码已经 fsync，不成立 |
| F09 state quarantine 无通知 | **基本到位** | 当前进程的新隔离可见；历史 quarantine 统计仍不完整 |
| F10 同 key 顺序依赖到达时机 | **journal 层到位** | `logical_revision` 已有；DB 不保存它 |
| F11 state table 主键未核对 | **修复到位** | fast path 检查唯一列序与 FK |
| F12 profile collision 丢子状态 | **修复到位** | settings/progress/slot 有合并与证据隔离 |
| F13 generic progress 无 schema | **修复到位** | Sokoban/Zuma validator 和 merge policy 已有 |
| F14 改名 identity | **修复到位** | 与 F03 同根 |
| F15 2048 持久化 row ID | **基本到位** | slot v3 不再保存；运行时仍保留旧 hint 兼容路径 |
| F16 语义坏 slot 不隔离 | **基本到位** | 已调用 quarantine API；成功结果未被 UI 确认 |
| F17 本机状态无最终事件 | **修复到位** | `LocalStateEvent` 已加入 |
| F18 profile Future 覆盖新选择 | **部分到位** | 显式切换有 generation；启动初始 load/guest ensure 仍有竞态 |
| F19 default profile 多来源 | **修复到位** | canonical default，guest/anonymous 同 UUID |
| F20 晚到 progress read 降 HUD | **修复到位** | game-side generation |
| F21 正常 state read 取写锁 | **修复到位** | 先普通读，坏数据才短写事务隔离 |
| F22 state scan/count 线性增长 | **明显改善** | 有界 batch 与内存 count；状态查询仍可同步扫描 |
| F23 lock 文件残留 | **接受当前取舍** | 直接删除会 split-brain，不列为任务 |
| F24 committed status 不可重建 | **修复到位** | 可读 score receipt |
| F25 CI 未闭环 | **部分到位** | workflow 已增强；branch protection/当前 head 状态仍缺 |
| F26 LICENSE | **仍需所有者决策** | 不应由 AI 擅自选择许可证 |

---

## 3. 当前发布阻断问题

## 3.1 CG9-F01：本机状态缺少端到端 revision/CAS

- **优先级**：P0
- **证据**：已复现等价模型
- **涉及**：
  - `PersistentStateOutbox.put`
  - `_durable_state_write`
  - `LocalGameStore.ensure_profile`
  - `set_setting`
  - `set_progress`
  - `save_slot`

### 根因

当前顺序：

```text
key lock:
    publish journal
unlock
write SQLite
key lock:
    compare-and-delete journal
```

SQLite 表没有 operation revision。

### 确定性场景

```text
A old:
  publish revision 10
  pause

B new:
  publish revision 20
  write DB = new
  remove journal

A resumes:
  write DB = old
  remove_if_current sees no file → True
```

### 影响

- profile 昵称回退；
- settings 回退；
- autosave 回退；
- `set_progress` 回退；
- replay 会把旧 operation 的执行时间变成“最近更新时间”。

### 正确修复

不能只扩大文件锁范围。即使持锁覆盖 DB，如果新的 operation 先完成并移除 journal，后到的旧 operation 仍可能在没有 journal 的情况下写回旧值。

需要 SQLite 级条件：

#### 方案 A：各表持久化 operation revision

```text
profiles.state_revision
settings.state_revision
progress.state_revision
save_slots.state_revision
```

upsert 条件：

```sql
... DO UPDATE ...
WHERE excluded.state_revision > current.state_revision
   OR (
       excluded.state_revision = current.state_revision
       AND excluded.operation_id > current.operation_id
   )
```

#### 方案 B：统一 state_receipts

```text
semantic_key PRIMARY KEY
logical_revision
operation_id
payload_hash
applied_at
```

业务写入与 receipt 更新在同一事务。

### 必须满足

- old operation 返回 `superseded/no_op`；
- 不修改 DB；
- 不修改 updated_at；
- 不删除新 journal；
- 不把 superseded 冒充成 committed；
- delayed replay 保留 operation 原始时间。

---

## 3.2 CG9-F02：startup profile load 与 default ensure 仍会把游戏启动到错误档案

- **优先级**：P0
- **证据**：代码路径确定 + 状态模型
- **涉及**：
  - `ProfileController`
  - launcher `retry_profile_save`
  - queued launch

### 场景

```text
generation = 0
last_profile_async pending
用户点击 2048
queue launch(expected_profile=None)
retry_profile_save → ensure default guest

default ensure 先完成
profile_ready = true
游戏在 default guest 下启动

last_profile 后完成
launcher 切到真实已有档案
```

### 为什么 generation 没挡住

- load 与 save 都绑定 generation 0；
- unresolved launch token 的 expected profile 为 None；
- `resolve()` 不增加 generation。

### 修复要求

建立显式 startup state：

```text
LOADING_LAST_PROFILE
NO_PROFILE_CONFIRMED
ENSURING_DEFAULT
READY
FAILED
```

规则：

- `LOADING_LAST_PROFILE` 时点击只排队，不创建 default；
- load 返回真实 profile：直接绑定并启动；
- load 明确返回 None：才 ensure canonical default；
- load 失败：显示重试或“明确使用临时档案”，不能静默降级；
- queued launch 绑定最终 resolved profile；
- 每个 Future 带 generation 和 operation token。

---

## 4. 当前高优先级问题

## 4.1 CG9-F03：state DB 没有幂等 receipt，重放会改变时间和版本

- **优先级**：P1

若 DB 已成功、进程在删除 journal 前崩溃，重放同一 operation：

- profile `last_used` 变成重放时间；
- setting `value_version` 再加 1；
- progress version 再加 1；
- save slot `updated_at` 变成重放时间。

对业务值可能无害，但会污染：

- 最近档案；
- 存档时间；
- 冲突选择；
- 审计和恢复判断。

应让 operation ID 在 DB 中幂等。

## 4.2 CG9-F04：wall-clock logical revision 不能可靠表达跨进程因果顺序

- **优先级**：P1

当前 revision 基于 `time.time_ns()`：

- 系统时钟回拨；
- 两台进程时间读取顺序；
- 休眠恢复；
- 同纳秒/低精度平台；

都可能让较新的用户操作获得较小 revision。

推荐：

- DB 可用时用 DB sequence；
- DB 不可用时使用 hybrid logical clock；
- slot 使用自身 `slot_revision`；
- progress 使用可交换 merge；
- settings/profile 使用 `(HLC, operation_id)`。

## 4.3 CG9-F05：superseded operation 被标记成 COMMITTED

- **优先级**：P1

当 journal 已存在更新 operation，旧 operation 的 `put()` 返回：

```text
published = false
superseded = true
```

当前 `_durable_state_write()` 会发出 `SaveState.COMMITTED`。

但更新 operation 可能仍只是 journal pending，尚未进入 SQLite。

建议增加：

```text
SUPERSEDED_BY_DURABLE_PENDING
SUPERSEDED_BY_COMMITTED
```

或至少返回：

```text
state = DURABLE_PENDING
superseded = true
winning_revision
```

## 4.4 CG9-F06：superseded 路径没有清理旧 `_non_durable_state`

- **优先级**：P1

旧内存 operation 重试时，如果发现磁盘上已有更新 operation，会提前返回，但没有从：

```python
_non_durable_state[key]
```

移除旧值。

结果：

- `failed_save_count()` 仍非零；
- `pending_saves_are_durable` 仍为 false；
- 重复提示未保存；
- 后续在新 journal 消失后，旧 operation 还可能再次尝试写 DB。

清理前必须确认更新 operation 已 durable 或 DB 已有更高 revision。

## 4.5 CG9-F07：状态查询仍可能在 pygame 主线程做文件锁、目录扫描或 SQLite

- **优先级**：P1

`get_local_state_status()` 缓存 miss 时会：

- `has_key()`；
- 获取 OS key lock，最长等待 1 秒；
- 可能 `list_entries()`。

`get_save_status()` 缓存 miss 时会同步读取 SQLite receipt。

这些方法被 BaseGame、2048、Sokoban、Zuma 和 launcher 在帧循环中调用。

应改为：

- getter 只读内存 snapshot；
- disk/DB reconstruction 在 read worker；
- 提供 async refresh；
- UI 只消费事件和 snapshot。

## 4.6 CG9-F08：状态缓存可以遮蔽另一个进程的新 pending

- **优先级**：P1
- **注意**：这是本轮新编号，与已撤销的“前次 F08”无关。

`get_local_state_status()` 优先返回缓存。

若缓存是旧的 COMMITTED，而另一进程后来写了同 key pending：

- 当前实例可能继续返回 committed；
- 直到后台 scan 更新；
- scan 发现文件时也未必立即发 pending event。

状态需要按 logical revision 比较 cache 与 disk discovery。

## 4.7 CG9-F09：特定 key 状态重建使用批量扫描，超过 128 条时可能找不到

- **优先级**：P1

当前：

```text
has_key(key) = true
→ list_entries(limit=128)
→ 在返回列表中查 key
```

如果目标不在本批 128 条中，返回 None。

应增加直接的：

```python
read_key(key)
```

而不是扫描整个目录。

## 4.8 CG9-F10：state journal v1 升级没有保留原字节备份

- **优先级**：P1

score spool v1 会保留 migration backup。

state journal v1 当前直接原子重写为 v2，没有等价备份。

升级逻辑若有错误，旧 operation 原文无法恢复。

## 4.9 CG9-F11：v1 progress key 仍可能保留 `"current"`

- **优先级**：P1

旧 key 可能是：

```text
progress:<profile>:<game>:current:<key>
```

升级后：

- args/ruleset 已冻结；
- semantic key 和文件名仍保留 `"current"`。

与此同时，新 v2 写入使用真实 ruleset key，因此可能存在两个文件表达同一进度。

应在迁移时：

- 生成 canonical key；
- 同时持有 old/new key lock；
- 合并；
- 备份原件；
- 原子迁移文件名。

## 4.10 CG9-F12：v1 journal 的 ruleset 取“升级时当前版本”

- **优先级**：P1

如果用户跳过多个版本，旧 journal 创建时的 ruleset 与升级时 catalog 不同，旧进度/slot 会被归入新规则。

应为已发布 v1 格式建立固定兼容映射，而不是读取未来 catalog。

## 4.11 CG9-F13：历史 state quarantine 在重启后不一定显示

- **优先级**：P1

`PersistentStateOutbox` 初始化只统计当前 pending 文件，不完整统计已有 quarantine。

用户可能曾丢失进度或存档，但 launcher 不再提示。

需要：

- 后台有界统计；
- 持久 recovery notice；
- 导出入口；
- 总数量/大小保留策略。

## 4.12 CG9-F14：全局 StorageStatus 只探测 score outbox

- **优先级**：P1

成绩 journal 可写不代表 state journal 可写。

应增加：

```text
score_outbox_writable
state_outbox_writable
database_readable
database_writable
```

否则首页可能显示“本机记录可用”，但 autosave/progress 无法持久化。

## 4.13 CG9-F15：2048 slot 仍有语义不变量缺口

- **优先级**：P1

当前允许：

```text
max_tile >= 2048
won = false
```

恢复后可能永远不再触发 2048 胜利。

也允许：

```text
game_state = playing
但棋盘已经不能移动
```

下一次输入才进入 gameover，并可能再次提交。

应验证或规范化：

- `won` 与 max tile；
- `won_announced` 与状态；
- playing 与 `_can_move()`；
- terminal state 与 autosave policy；
- slot revision 上限。

## 4.14 CG9-F16：2048 quarantine 尚未确认就显示“原始数据已隔离”

- **优先级**：P1

`_quarantine_bad_slot()` 发起 async quarantine 后立即显示成功文案，但 `_slot_quarantine_future` 没有完整结果轮询。

数据库锁、只读或满盘时，隔离可能失败。

UI 应区分：

```text
正在隔离
已隔离
隔离失败，原存档仍保留
```

用户确认新开前必须知道原件是否已安全保留。

## 4.15 CG9-F17：2048 slot-load timeout 只丢弃引用，后台任务仍占用单 writer

- **优先级**：P1

8 秒超时后游戏允许重试，但原 Future 可能仍在 worker 中执行。

连续重试会：

- 堆积 ensure/load operation；
- 阻塞后续 autosave、profile 和 progress 写入；
- 产生晚到结果。

需要：

- load generation；
- coalesce；
- 能取消未开始任务；
- profile ensure 与 slot read 分离；
- slot read 放 read worker。

## 4.16 CG9-F18：同一 profile 的两个 2048 实例会争用一个 autosave slot

- **优先级**：P1

当前 autosave key：

```text
profile + game + autosave
```

两个同时运行的 2048：

- 使用不同 attempt UUID；
- 持续覆盖同一个 slot；
- 最后一次写入不一定是玩家想继续的那一局。

选择：

- 默认单活动实例；
- 按 attempt 建 autosave slot；
- 或支持多槽并在启动时选择。

## 4.17 CG9-F19：score request ID 在 receipt 过期后的冲突语义不稳定

- **优先级**：P1

receipt 180 天后删除，但 attempts.request_id 仍唯一。

若同 request ID 被用于不同 attempt：

- 可能落入底层 `IntegrityError`；
- local path 将其归为 constraint/quarantine；
- optional Flask 可能返回 database error；
- 而不是明确 409 request conflict。

应在 insert 前查询 attempt.request_id 并返回稳定语义。

## 4.18 CG9-F20：state table 缺少部分 DB CHECK 约束

- **优先级**：P1/P2

fast path 已检查列、唯一键和 FK，但 DB 本身仍可包含：

- 负 value_version；
- 非法 state_version；
- 空 ruleset；
- 非有限 updated_at；
- 不规范 profile ID（通过关闭 FK 手工写入）。

建议在下一次 schema migration 中增加 CHECK，应用层校验继续保留。

## 4.19 CG9-F21：启动 fast path 的完整性检查可能随历史数据增长

- **优先级**：P2

每次启动会执行：

- `foreign_key_check`；
- invalid attempt scan；
- trigger SQL 核对；
- legacy source 检查。

当前数据规模小没有问题，但长期历史较大后可能延迟首帧。

可使用：

- schema fingerprint；
- last clean shutdown marker；
- bounded maintenance；
- 后台完整性检查。

## 4.20 CG9-F22：HTTP 调试启动器会把多个名字放入同一 default profile

- **优先级**：P1/P2

HTTP client 没有 profile API，launcher 又使用 canonical default profile ID。

显式 HTTP 调试时修改玩家名，多个名字仍可能共享同一 profile。

既然 HTTP 模式明确只是成绩 API 调试，建议：

- 不发送 profile_id，让服务端从 player 派生；或
- 用显示名派生 debug profile；
- UI 标记“调试模式，不提供本机档案语义”。

不需要建设完整在线档案服务。

## 4.21 CG9-F23：CI workflow 已存在，但仓库门禁仍未生效

- **优先级**：P1

当前：

- branch protection disabled；
- required checks disabled；
- 当前 head 没有本次审查可见的 completed status。

项目 `task.md` 报告前序 CI 已通过，但不能代替 required checks。

## 4.22 CG9-F24：coverage 仍主要覆盖 storage/stress

- **优先级**：P2

gameplay regression 另行执行，不进入 coverage。

当前 60% 门槛适合作为过渡，不适合稳定版本最终目标。

## 4.23 CG9-F25：测试系统仍混合三套运行方式

- **优先级**：P2

当前并存：

- 大型自定义 regression runner；
- unittest discover；
- stress script。

缺少：

- pytest fixture/marker；
- 属性测试；
- mutation testing；
- 可选择的 fast/full/release test profile。

## 4.24 CG9-F26：依赖与工具仍可能漂移

- **优先级**：P1/P2

- `pyproject` 允许 pygame 2.5–<3；
- requirements/environment 固定 2.6.1；
- dev 工具为下界；
- 没有 lock/constraints。

建议明确：

- 开发测试版本；
- 桌面发行锁定版本；
- 可支持范围。

## 4.25 CG9-F27：LICENSE/NOTICE 仍缺失

- **优先级**：P1（正式分发前）

不能由 AI 擅自选择。

仓库所有者需要确认：

- 代码权利；
- AI 辅助代码的使用政策；
- 游戏名称和商标；
- 字体、图形、音频；
- 未来素材来源。

完成清单后再选择适合的许可证。

## 4.26 CG9-F28：尚无完整数据导出、导入和恢复 UI

- **优先级**：P1/P2

当前已经积累：

- attempts；
- profiles；
- settings；
- progress；
- save slots；
- score quarantine；
- state quarantine；
- invalid DB rows；
- migration backups。

但用户没有统一入口管理它们。

---

## 5. 分模块审查

## 5.1 `game_service/profile.py`

### 已改善

- canonical default；
- guest/anonymous 同一身份；
- Unicode NFC；
- 控制字符拒绝；
- legacy UUIDv5；
- strict UUID。

### 后续

- profile CRUD 安全流程；
- 重复显示名 UX；
- export-before-delete；
- profile merge；
- profile operation DB revision。

## 5.2 `PersistentStateOutbox`

### 已改善

- schema v2；
- per-key OS lock；
- CAS remove；
- logical revision；
- ruleset；
- progress merge；
- bounded scan；
- quarantine；
- fsync；
- local state event。

### 主要缺口

- DB 不保存 revision；
- v1 migration 没 raw backup；
- v1 key 未 canonicalize；
- historical quarantine 统计；
- status direct read；
- superseded 状态语义；
- stale non-durable cleanup；
- state DB receipt/idempotency。

## 5.3 `LocalBackendClient`

### 已改善

- 真异步；
- score/state 两类 journal；
- non-durable fallback；
- read/write worker；
- storage error classification；
- SaveEvent/LocalStateEvent；
- typed slot load；
- retry backoff；
- deferred store initialization。

### 主要缺口

- end-to-end state ordering；
- status getter 可能同步 I/O；
- stale cache；
- unexpected Future cleanup；
- load-task coalescing；
- state outbox health；
- close 超时与取消策略。

## 5.4 `LocalGameStore`

### 已改善

- schema v5；
- explicit migration；
- profile/FK；
- current display-name join；
- progress schema；
- profile collision merge；
- state corruption isolation；
- state PK fast-path；
- score attempt policy；
- receipt maintenance。

### 主要缺口

- local-state operation revision；
- local-state idempotency；
- replay timestamp；
- request-ID expiry conflict；
- state CHECK；
- startup scan cost；
- export/recovery APIs。

## 5.5 启动器

### 已改善

- ProfileController；
- IME；
- profile gate；
- queued launch；
- new/switch/rename；
- async local startup；
- recent 非竞争展示。

### 主要缺口

- startup load/default ensure race；
- 明确档案列表；
- generation 完整绑定；
- 键盘/手柄导航；
- 本地进度中心；
- 错误恢复页；
- `main()` 仍高度集中。

## 5.6 2048

### 已改善

- delayed input；
- load gate；
- typed failure；
- retry/new-game confirm；
- slot schema 3；
- attempt restore；
- row-ID hint 降级；
- semantic validation；
- score/save status；
- terminal policy。

### 主要缺口

- won/dead-board invariant；
- quarantine ack；
- load task cancellation；
- multi-instance slot；
- multi-slot/undo；
- local-state DB revision；
- status getter synchronous path。

## 5.7 Sokoban

### 已改善

- 重复计分；
- 跳关语义；
- 全关判定；
- 0 分通关；
- campaign/practice；
- progress load/generation；
- schema-aware merge；
- 解锁 HUD。

### 后续

- 正式关卡选择；
- 最少移动/推动；
- 撤销标记；
- 星级；
- 死锁检测；
- 提示；
- 地图闭合、可达和静态死角；
- XSB 导入；
- 编辑器；
- 固定逻辑窗口。

## 5.8 Zuma

### 已改善

- 多 pending reaction；
- swept collision；
- path bisect；
- 长帧余量；
- progress load/generation；
- schema-aware merge。

### 后续

- reaction FSM；
- 重叠反应属性测试；
- `incoming` deque；
- RNG 注入；
- 训练和选关；
- 色弱符号；
- 原创道具和轨道编辑器。

## 5.9 Tetris

此前核心输入和长帧修复保持。

后续适合单机舒适性：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- RNG 注入；
- 规则页；
- 网格缓存。

保持“自定义辅助旋转”名称，不声称严格标准 SRS。

## 5.10 Snake

此前核心输入和长停顿修复保持。

后续：

- 速度；
- 穿墙；
- 障碍；
- 双人同屏；
- RNG；
- 色弱纹理；
- 棋盘缓存。

---

## 6. 明确非目标

本项目不需要建设：

- 注册登录；
- 云端账号；
- 公网排行榜；
- 匹配；
- 赛季；
- 实时联机；
- 服务端权威验证；
- 反作弊；
- replay 审核；
- 在线商城；
- 强制联网；
- 默认遥测；
- 复杂权限平台。

Flask 继续定位为：

- 教学；
- 调试；
- 本机成绩 API 示例。

---

## 7. 推荐的增量架构

无需推倒现有结构，重点增加 state commit 层：

```text
Game / Launcher
    │
    ├── AttemptSaveController
    ├── ProfileController
    ├── ProgressController
    └── SaveSlotController
             │
             ▼
      LocalStateOperation
             │
             ├── semantic_key
             ├── logical_revision / HLC
             ├── operation_id
             ├── ruleset_version
             ├── occurred_at
             └── payload_hash
             │
             ▼
      State journal + SQLite transaction
             │
             └── state_receipts / per-row revision
```

### 7.1 建议的 DB state receipt

```text
semantic_key TEXT PRIMARY KEY
logical_revision INTEGER NOT NULL
operation_id TEXT NOT NULL
payload_hash TEXT NOT NULL
occurred_at REAL NOT NULL
applied_at REAL NOT NULL
```

### 7.2 建议状态

```text
SAVING
COMMITTED
DURABLE_PENDING
NON_DURABLE_PENDING
SUPERSEDED
RECOVERY_REQUIRED
QUARANTINED
PERMANENT_INPUT_ERROR
```

---

## 8. 完整优化任务清单

### 优先级定义

- **P0**：可能把本机数据写回旧值、写错档案或静默丢失；发布阻断。
- **P1**：数据一致性、自恢复、迁移与正式发行基础。
- **P2**：维护性、输入、UI、性能和可访问性。
- **P3**：游戏内容与桌面发行。
- **S/M/L/XL**：相对工作量。

---

## 8.1 P0：本机状态端到端一致性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG9-P0-01 | state operation DB revision | schema migration | 每个 semantic key 保存 revision/operation ID | XL |
| CG9-P0-02 | conditional state upsert | repository policy | 旧 operation 无法覆盖新 DB 值 | L |
| CG9-P0-03 | state receipt/idempotency | `state_receipts` | crash-after-commit 重放为 no-op | L |
| CG9-P0-04 | 保留 operation occurred_at | DB/API | 重放不刷新 last_used/slot time | M |
| CG9-P0-05 | setting 端到端并发测试 | two-process barrier | 新 setting 最终保留，journal 清空 | M |
| CG9-P0-06 | slot 端到端并发测试 | two-process barrier | 新 autosave 不被旧 worker 写回 | M |
| CG9-P0-07 | profile rename 并发测试 | integration | 旧 rename 不覆盖新名字 | M |
| CG9-P0-08 | set-progress 端到端测试 | integration | stale set 不降低进度 | M |
| CG9-P0-09 | startup profile 状态机 | controller | load 未完成时不 ensure default | L |
| CG9-P0-10 | queued launch 绑定 resolved profile | token v2 | 游戏使用最终确认 profile | M |
| CG9-P0-11 | load/default ensure 顺序测试 | controlled futures | ensure 先完成也不能启动 guest | M |
| CG9-P0-12 | profile load 失败显式选择 | UI | 不静默降级到新 guest | M |
| CG9-P0-13 | stale non-durable state 清理 | lifecycle | superseded 后不永久阻塞退出 | M |
| CG9-P0-14 | superseded 状态模型 | SaveState | 不把 pending winner 冒充 committed | S |
| CG9-P0-15 | render-thread status 去 I/O | snapshot/event | getter 不取得文件锁或 SQLite | L |
| CG9-P0-16 | P0 发布门禁 | required CI job | 任一 P0 用例失败禁止发布 | S |

---

## 8.2 P1：数据可靠性、迁移与工程门禁

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG9-P1-01 | Hybrid logical clock | state clock | 跨进程时钟回拨不误序 | L |
| CG9-P1-02 | operation ID DB 冲突规则 | ADR/test | 同 revision 有确定性结果 | S |
| CG9-P1-03 | state status revision-aware | snapshot | cache 不遮蔽新 pending | M |
| CG9-P1-04 | direct state-key reader | API | 不扫描 128 条查一个 key | M |
| CG9-P1-05 | async status reconstruction | read worker | receipt/journal 重建不在帧线程 | M |
| CG9-P1-06 | state v1 raw backup | migration backup | 重写前保留原字节 | M |
| CG9-P1-07 | state v1 canonical key migration | key migrator | `current` 迁到真实 ruleset key | L |
| CG9-P1-08 | state v1 fixed ruleset map | compatibility table | 跳版本升级不套用未来规则 | M |
| CG9-P1-09 | state quarantine 启动统计 | bounded scan | 历史隔离仍提示 | M |
| CG9-P1-10 | state quarantine 导出 | recovery API | 原始文件可导出 | M |
| CG9-P1-11 | state quarantine 保留策略 | retention | 不无限增长、不自动误删 | M |
| CG9-P1-12 | 独立 state-outbox health | StorageStatus v2 | score/state 可写性分别显示 | S |
| CG9-P1-13 | LocalStateEvent SUPERSEDED | event model | UI 文案准确 | S |
| CG9-P1-14 | unexpected Future cleanup | callbacks | `_unpublished_state` 不永久残留 | M |
| CG9-P1-15 | worker close timeout | lifecycle | 文件系统挂起不无限卡退出 | M |
| CG9-P1-16 | 2048 won invariant | slot validator | max≥2048 时 won 状态一致 | S |
| CG9-P1-17 | 2048 playing/dead-board invariant | slot validator | 死局不恢复为 playing | M |
| CG9-P1-18 | 2048 quarantine Future 轮询 | UI | 成功后才显示已隔离 | M |
| CG9-P1-19 | 2048 load task generation | controller | 晚到结果不应用 | M |
| CG9-P1-20 | 2048 load coalescing/cancel | worker policy | 重试不堆积任务 | M |
| CG9-P1-21 | 2048 active-slot ownership | session policy | 两实例不会静默互相覆盖 | L |
| CG9-P1-22 | 2048 多槽基础模型 | repository | 可保留不同 attempt | L |
| CG9-P1-23 | score request-ID expiry lookup | store query | 不落入裸 IntegrityError | S |
| CG9-P1-24 | constraint error mapping | error contract | optional API 返回稳定 409/repair | M |
| CG9-P1-25 | state CHECK constraints | schema migration | version、时间、ruleset 有 DB 约束 | L |
| CG9-P1-26 | startup schema fingerprint | metadata | 正常启动不全量重复核对 | M |
| CG9-P1-27 | bounded background integrity | maintenance | 大历史不阻塞首帧 | M |
| CG9-P1-28 | profile load failure UI | choice dialog | retry/use guest/return 明确 | M |
| CG9-P1-29 | duplicate display-name UX | profile list | 同名可区分 | M |
| CG9-P1-30 | canonical default migration | migration | 旧 guest/anonymous 统一且可解释 | S |
| CG9-P1-31 | HTTP debug profile policy | debug adapter | 多名字不意外共享身份 | M |
| CG9-P1-32 | service capability protocol | interfaces | HTTP 不假装支持 local-state | M |
| CG9-P1-33 | 数据导出 service | export | attempts/profiles/progress/slots/settings | L |
| CG9-P1-34 | 数据导入 service | import | 校验、预览、回滚 | L |
| CG9-P1-35 | migration backup 管理 | UI/service | 可列出和恢复 | M |
| CG9-P1-36 | invalid rows 管理 | recovery | 可查看、导出、清理 | M |
| CG9-P1-37 | 结构化日志 | rotating logs | worker/migration/game 有 traceback | M |
| CG9-P1-38 | 恢复页面 | desktop UI | DB、score/state journal、quarantine 可见 | L |
| CG9-P1-39 | pytest 全量迁移 | fixtures/markers | 任一测试可单独运行 | XL |
| CG9-P1-40 | fast/full/release test profiles | commands | 本地反馈与发布门禁分层 | M |
| CG9-P1-41 | state model property tests | Hypothesis | revision、CAS、merge 不变量 | L |
| CG9-P1-42 | game property tests | Hypothesis | 五款核心不变量 | XL |
| CG9-P1-43 | mutation testing | selected modules | 测试能杀死关键错误 | M |
| CG9-P1-44 | current head CI verification | workflow evidence | 所有 matrix job 实际成功 | S |
| CG9-P1-45 | branch protection | repo settings | main 合并必须通过 required checks | S |
| CG9-P1-46 | regression 纳入 coverage | coverage config | 游戏路径被统计 | M |
| CG9-P1-47 | coverage 阈值提升计划 | staged gate | 核心≥90%，全项目≥80% | M |
| CG9-P1-48 | JUnit/截图/完整日志 | artifacts | 失败可诊断 | M |
| CG9-P1-49 | 类型检查 | mypy/pyright | service/store/client 边界通过 | M |
| CG9-P1-50 | 依赖审计 | pip-audit/等效 | 已知风险自动报告 | S |
| CG9-P1-51 | 依赖锁定 | lock/constraints | 发布构建可复现 | M |
| CG9-P1-52 | 依赖来源统一 | policy | pyproject/requirements/environment 不漂移 | S |
| CG9-P1-53 | LICENSE 权利清单 | owner decision | 代码与素材权利明确 | M |
| CG9-P1-54 | NOTICE/素材清单 | legal inventory | 字体、图形、音频来源明确 | M |
| CG9-P1-55 | 商标与游戏名称检查 | release checklist | 正式分发风险有记录 | M |
| CG9-P1-56 | SemVer/schema/ruleset 治理 | ADR | 每次兼容变化可追踪 | S |
| CG9-P1-57 | docs 目录继续整理 | docs | `spec.md/task.md` 归档 | S |
| CG9-P1-58 | 数据恢复演练 | release test | 从 backup/journal 恢复成功 | L |

---

## 8.3 P2：单机体验、维护性与性能

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG9-P2-01 | ProfileController 完整状态类 | controller | launcher 不散落管理 Future | L |
| CG9-P2-02 | 明确档案列表页 | UI | 新建、切换、重命名、查看进度 | L |
| CG9-P2-03 | export-before-delete | profile flow | 删除有备份与二次确认 | L |
| CG9-P2-04 | profile merge UI | local-only tool | 冲突预览、可回滚 | XL |
| CG9-P2-05 | GameState Enum | state model | 无魔法字符串 | L |
| CG9-P2-06 | AttemptSaveController | common controller | 2048/BaseGame 保存统一 | XL |
| CG9-P2-07 | LocalStateController | common layer | settings/progress/slot 状态统一 | L |
| CG9-P2-08 | ProgressController | common layer | generation、merge、事件统一 | L |
| CG9-P2-09 | SaveSlotController | common layer | loading/retry/new-game 统一 | L |
| CG9-P2-10 | InputManager | action map | 五款游戏统一输入 | L |
| CG9-P2-11 | 完整 IME 控件 | widget | 组合、光标、选择、退格稳定 | M |
| CG9-P2-12 | 键位重映射 | settings UI | 冲突检测、恢复默认 | L |
| CG9-P2-13 | 键盘菜单导航 | focus model | 不用鼠标可操作 | L |
| CG9-P2-14 | 手柄支持 | controller | launcher/五款游戏可用 | L |
| CG9-P2-15 | 音频系统 | BGM/SFX | 无设备不崩，音量持久化 | L |
| CG9-P2-16 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG9-P2-17 | 高 DPI | DPI handling | 字体和图形清晰 | M |
| CG9-P2-18 | 字体 fallback | licensed font chain | 缺系统 CJK 字体仍可读 | M |
| CG9-P2-19 | 色弱符号 | patterns/shapes | 颜色不是唯一信息 | L |
| CG9-P2-20 | 高对比/降低动态 | accessibility | 脉冲、抖动可关闭 | L |
| CG9-P2-21 | Clock/RNG 注入 | deterministic services | seed+输入可重现 | L |
| CG9-P2-22 | 渐进抽取纯规则 Engine | rules modules | 核心测试无需 SDL | XL |
| CG9-P2-23 | launcher 拆分 | app/state/render/data | `main()` 职责清晰 | L |
| CG9-P2-24 | 首页改为进度中心 | dashboard | best/recent/progress/continue | L |
| CG9-P2-25 | 静态 Surface 缓存 | profiler-driven | 有尺寸/主题失效 | L |
| CG9-P2-26 | Zuma reaction FSM | explicit model | 重叠 reaction 可属性测试 | L |
| CG9-P2-27 | 可重复 benchmark | CLI | 带 OS、版本、seed | M |
| CG9-P2-28 | 30–60 分钟 soak | stability suite | 线程/FD/内存稳定 | M |
| CG9-P2-29 | 游戏内规则页 | controls/rules/version | ruleset 可查看 | M |
| CG9-P2-30 | 崩溃恢复页 | crash UI | 返回菜单并显示日志 | M |
| CG9-P2-31 | 设置页面 | settings UI | 窗口、音量、按键、辅助项 | L |
| CG9-P2-32 | 历史/legacy 浏览 | history UI | 当前规则与旧记录分开 | M |

---

## 8.4 P3：游戏内容与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG9-P3-01 | Tetris 7-bag | comfort mode | 独立 ruleset | M |
| CG9-P3-02 | Tetris ghost/hold/lock delay | comfort features | 规则测试完整 | L |
| CG9-P3-03 | Snake 速度/穿墙/障碍 | local modes | 模式最佳分开 | L |
| CG9-P3-04 | Snake 双人同屏 | local multiplayer | 不依赖网络 | L |
| CG9-P3-05 | 2048 撤销 | undo model | slot/attempt 语义清晰 | L |
| CG9-P3-06 | 2048 多存档槽 | save UI | 可查看、继续、删除 | L |
| CG9-P3-07 | 2048 棋盘尺寸 | modes | ruleset 分离 | M |
| CG9-P3-08 | Sokoban 正式选关 | progress UI | practice/campaign 分开 | L |
| CG9-P3-09 | Sokoban 星级/最佳推动 | metrics | 规则明确 | M |
| CG9-P3-10 | Sokoban 死锁检测/提示 | analysis | 提示可关闭 | XL |
| CG9-P3-11 | Sokoban 编辑器 | XSB import/export | 地图验证和预览 | L |
| CG9-P3-12 | Zuma 训练/选关 | practice mode | 不混入完整通关 | L |
| CG9-P3-13 | Zuma 色弱辅助 | symbols | 球色可独立辨认 | M |
| CG9-P3-14 | Zuma 原创道具/轨道 | content | 每项有确定性测试 | XL |
| CG9-P3-15 | Zuma 轨道编辑器 | path tool | 合法、版本化、可预览 | L |
| CG9-P3-16 | 本机成就 | local achievements | 无账号、无遥测 | L |
| CG9-P3-17 | 离线每日挑战 | date seed | 完全离线 | L |
| CG9-P3-18 | 本地 replay | command log | 用于复盘和调试 | L |
| CG9-P3-19 | 中英文 | localization | 长文本布局测试 | L |
| CG9-P3-20 | Windows 桌面包 | installer/portable | 无 Python 可运行 | XL |
| CG9-P3-21 | macOS app bundle | package | 数据目录与关闭 smoke | XL |
| CG9-P3-22 | Linux package | AppImage/等效 | XDG 路径正确 | L |
| CG9-P3-23 | 自动发布 | tagged builds | smoke 通过才发布 | L |
| CG9-P3-24 | 签名与校验和 | release integrity | 下载可验证 | M |
| CG9-P3-25 | 截图/GIF/主页 | showcase | README 首屏展示玩法 | M |
| CG9-P3-26 | 谨慎新增游戏 | template/contracts | 同时交付规则、数据、输入、测试 | XL |

---

## 9. 必须新增的测试

### 9.1 端到端 state ordering

```text
test_old_setting_worker_cannot_overwrite_new_committed_value
test_old_slot_worker_cannot_overwrite_new_committed_autosave
test_old_profile_rename_cannot_revert_new_name
test_state_db_receipt_makes_crash_replay_idempotent
test_state_updated_at_comes_from_operation_not_replay_time
test_superseded_operation_clears_non_durable_state
```

### 9.2 profile startup

```text
test_early_click_does_not_start_default_before_last_profile
test_default_ensure_finishing_first_does_not_win_startup_race
test_load_failure_requires_explicit_guest_choice
test_queued_launch_uses_resolved_profile
test_stale_profile_future_cannot_change_current_profile
```

### 9.3 state status

```text
test_status_getter_never_waits_for_file_lock_on_render_thread
test_specific_key_status_does_not_scan_first_128_entries
test_external_new_pending_supersedes_cached_committed_status
test_state_v1_upgrade_preserves_raw_backup
test_state_v1_current_key_migrates_to_ruleset_key
test_historical_state_quarantine_is_reported
```

### 9.4 2048

```text
test_slot_with_2048_tile_cannot_claim_won_false
test_dead_playing_slot_is_normalized_or_quarantined
test_quarantine_message_waits_for_ack
test_slot_load_retry_is_coalesced
test_two_instances_do_not_silently_share_one_autosave
test_ruleset_mismatch_is_reported_as_incompatible_not_corrupt
```

### 9.5 score/receipt

```text
test_reused_request_id_after_receipt_expiry_returns_conflict
test_integrity_error_is_mapped_to_semantic_error
```

### 9.6 属性测试

#### Tetris

- generation 不跨块；
- 同义键；
- 所有锁定格合法；
- 自定义旋转规格；
- 未来 bag 不变量。

#### Snake

- 不接受 180°；
- 快速转向顺序；
- 长帧；
- 长度和食物不变量。

#### 2048

- 每 tile 每步最多合并一次；
- 总值守恒；
- 输入不迟到；
- slot/attempt/revision 一致；
- load/retry/new-game 状态机。

#### Sokoban

- 同关不重复；
- practice 不进 campaign；
- 0 分通关；
- progress 单调；
- 地图合法性。

#### Zuma

- 多 pending 不丢；
- 连锁顺序；
- 长帧；
- 临界救场；
- progress 单调。

---

## 10. 性能与稳定性门槛

1. pygame 主线程不得执行：
   - SQLite；
   - score/state journal scan；
   - OS 文件锁等待；
   - profile migration；
   - save-slot read/write；
2. `submit_score_async()` p99 ≤2 ms；
3. `retry_failed_saves()` 调用本身 p99 ≤2 ms；
4. state status getter p99 ≤0.2 ms；
5. 32 进程同 state key：
   - 最新 DB 值正确；
   - journal 最终清空；
   - 无旧值回写；
6. startup profile Future 任意完成顺序，游戏归属始终正确；
7. 正常游戏目标 60 FPS，基准机 p95 ≤16.7 ms；
8. 保存、进度、存档不得造成 >50 ms 主线程长帧；
9. 旧 DB + score spool + state journal 升级：
   - 自动恢复；
   - 原件备份；
10. 100 次游戏切换：
    - 线程回基线；
    - FD 不增长；
    - Surface/内存稳定；
11. 30–60 分钟：
    - pending 最终 commit/superseded/recovery/quarantine；
    - 不无限重试；
    - 不持续增长内存；
12. 满盘、只读、坏 DB、坏 slot、坏 progress：
    - 游戏仍可玩；
    - 最后一份数据不静默删除；
    - 有恢复提示。

---

## 11. 稳定版本质量门禁

正式稳定版本至少满足：

- 所有 P0 关闭；
- state revision 进入 SQLite；
- profile startup race 关闭；
- status getter 不做主线程 I/O；
- 2048 slot 语义完整；
- score/state 两类 journal 均可恢复；
- ruleset 在创建时冻结；
- progress 在 journal 和 DB 两层均单调；
- migration/backup/export 完整；
- GitHub Actions 当前 head 三平台实际通过；
- required checks 启用；
- core-only 测试；
- pytest/JUnit/coverage；
- formatter/lint/type/dependency audit；
- LICENSE/NOTICE 在权利确认后加入；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代跨进程 barrier 测试、升级 fixture、故障注入和真实玩家测试。

---

## 12. 推荐实施顺序

### M0：关闭 state 端到端一致性与 profile 竞态

1. DB state revision/receipt；
2. conditional upsert；
3. profile startup state machine；
4. superseded/non-durable 清理；
5. status getter 去 I/O；
6. P0 并发测试。

### M1：恢复与迁移硬化

- v1 state backup/key migration；
- HLC；
- state health/quarantine；
- 2048 slot 语义；
- receipt expiry；
- export/import/recovery UI。

### M2：工程可持续

- pytest；
- 全量 coverage；
- branch protection；
- typing；
- property tests；
- dependency lock；
- LICENSE 权利核对；
- CI artifact。

### M3：桌面体验

- 档案页；
- InputManager；
- 键位/手柄；
- 音频；
- DPI/字体；
- 可访问性；
- launcher 拆分；
- RNG/Clock；
- 纯规则引擎；
- settings UI。

### M4：游戏内容与发行

- 五款游戏的单机舒适功能；
- Sokoban/Zuma 编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 自动发布；
- 基础稳定后再新增游戏。

---

## 13. 最终判断

本轮修复是明显进步。特别是：

- state journal 文件层 CAS；
- ruleset 冻结；
- progress schema；
- non-durable fallback；
- ProfileController；
- 2048 typed load；
- profile collision migration；
- CI 诊断；

都属于正确方向。

但当前测试主要证明了：

```text
journal 文件不会被旧 worker 错删
```

尚未证明：

```text
旧 worker 不会在 journal 删除后把 SQLite 写回旧值
```

这两者不是同一个不变量。

下一步最值得做的不是继续扩大功能，也不是增加联网平台，而是：

> **让 state operation 的 logical revision 真正进入 SQLite，并把启动档案解析做成严格状态机。**

完成这两项后，项目的数据可靠性会从“文件恢复较强”提升到“跨进程、跨重启的端到端一致”。
