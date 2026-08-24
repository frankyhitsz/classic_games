# Classic Games Hub 第十次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 当前基线：`main` 分支 commit `9ac703f82b4ec2fddf6b3fef7a656e929f280800`（`9ac703f`）
> 对比基线：上一轮审查 commit `24918852a651aac565e0feabbfab0e157146ca45`
> 增量范围：6 个提交，核心修复集中于 state receipt/CAS、档案启动状态机、2048 存档恢复、CI 与测试隔离
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 审查范围：`client/`、五款游戏、`game_service/`、SQLite schema/迁移、score/state journal、可选 Flask、测试、CI、文档与发行治理

---

## 0. 执行摘要

本轮修复总体有效，而且关闭了上一轮最核心的两个架构缺口：

1. 本机状态的 `logical_revision / operation_id / payload_hash` 已经进入 SQLite `state_receipts`；
2. 启动器已有明确的 `loading / load_failed / resolved` 档案状态，初次读取失败不会再静默创建 guest。

同时已经实现：

- schema v6 的 `state_receipts` 与 `state_merge_receipts`；
- `apply_state_operation()` 在同一事务内比较 state operation 顺序、写业务行和回执；
- 单调 progress 的晚到 merge 仍可应用一次；
- 跨进程持久逻辑时钟；
- `SaveState.SUPERSEDED`；
- state getter 不再在 pygame 帧线程同步取得文件锁或查询 SQLite；
- v1 state journal 原始字节备份、固定 ruleset 兼容表和 canonical key 迁移；
- score/state 两类 outbox 健康状态分离；
- score receipt 过期后的权威 attempt 查询；
- 2048 存档不变量、隔离确认和非阻塞 load 链；
- gameplay 子进程覆盖率汇总；
- 三平台和 Python 3.12/3.13 CI。

因此当前项目不需要再次推倒架构。合理主线仍是：

```text
pygame
  → GameDataService
      → Local read/write workers
          → SQLite
          → score spool
          → keyed local-state journal

Flask
  → 仅作为可选成绩 API 调试适配器
```

本轮重新复核后，用户转述的两项反馈均有依据：

- **F22 的原广义结论不准确，应撤销。**HTTP API 支持显式 `profile_id`；省略时按玩家名派生稳定 UUID，是为了把同一玩家的多次成绩聚合到同一身份。随机 ID 会错误地把每次提交拆成不同玩家。
- **F25 不应被表述为缺陷。**`unittest`、pygame 子进程 runner 和 stress 脚本职责不同且互补；不应为了“统一语法”强制迁移到 pytest。当前删除未使用 pytest 依赖是合理的。

不过，重新把 journal、receipt、业务表和迁移组合起来检查后，仍发现三个发布阻断级数据问题：

### P0-A：progress journal 聚合会复用一个已经提交过的 operation ID

当“较新的 progress operation 已经提交，但 journal 因崩溃尚未删除”，又有一个更早创建的 progress merge 晚到时，journal 会合并两者，但保留较新 operation 的 ID、改变 payload hash。

SQLite 的 `state_merge_receipts` 已经记录：

```text
operation_id = newer
payload_hash = old_hash
```

新聚合变为：

```text
operation_id = newer
payload_hash = merged_hash
```

仓储会正确判断为 operation ID 冲突；但本地 backend 随后会把这个非重试错误当作永久失败并删除聚合 journal。晚到 operation 的关卡/高分贡献因此丢失。

### P0-B：业务状态被隔离或删除后，旧 state receipt 仍声称该状态已经提交

`get_setting()`、`get_progress()`、`load_slot()` 和 `quarantine_slot()` 会隔离并删除坏业务行，但没有同步失效对应的：

```text
state_receipts
state_merge_receipts
```

若同一有效 journal 随后重放，`apply_state_operation()` 可能直接从 receipt 返回“duplicate committed”，不重建已经不存在的业务行；backend 随即删除 journal。

结果是：

```text
业务行不存在
receipt 仍说 committed
最后一个可恢复 journal 被删除
```

### P0-C：schema v5→v6 没有为既有本机状态建立 baseline receipt

升级会创建空 `state_receipts`。如果 v5 数据库中已经有较新 setting/slot/profile，而磁盘仍留有更旧的 v2 state journal，v6 重放时因为找不到 prior receipt，会接受旧 operation 并覆盖较新业务值。

当前升级测试覆盖 schema 结构和普通迁移，但尚未覆盖：

```text
v5 业务行较新
+ v2 journal 较旧
→ 升级 v6
```

本轮结论：

> **状态 CAS 主体已经实现，但还需修复 progress 聚合身份、receipt 与业务行的一致性，以及 v5→v6 的 baseline receipt。**
>
> 完成这三项后，项目的数据可靠性会从“正常并发顺序正确”提升到“崩溃、损坏和跨版本升级后仍能正确恢复”。

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的检查

- 锁定当前 `main` 最新提交；
- 对比上一轮基线到当前 head 的 6 个提交；
- 逐项核对上一轮 F01–F28；
- 阅读当前：
  - `client/common/ui.py`
  - `client/common/network.py`
  - `client/profile_controller.py`
  - `client/launcher.py`
  - Tetris、Snake、2048、Sokoban、Zuma
  - `game_service/catalog.py`
  - `game_service/profile.py`
  - `game_service/progress.py`
  - `game_service/mutation.py`
  - `game_service/service.py`
  - `game_service/local_backend.py`
  - `game_service/store.py`
  - `server/app.py`
  - storage v2/v4/v5/v6/v7 tests
  - regression runner、stress、CI workflow
  - README、task、审查回应和工程文件；
- 建模复现 progress operation ID/hash 冲突；
- 复核 receipt/business-row 生命周期；
- 复核 schema v5→v6 的既有状态基线；
- 复核 F22 的 API、客户端、服务端和 launcher 集成；
- 复核 F25 三类测试入口的真实职责；
- 检查当前分支保护与 required checks 设置。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 主要路径和关键边界已经闭环 |
| **基本到位** | 原问题已关闭，但相邻组合场景仍有缺口 |
| **部分到位** | 模块内成立，跨模块整体仍不完整 |
| **撤销** | 前次判断经重新核对不成立 |
| **已复现** | 使用等价可执行状态模型复现 |
| **代码路径确定** | 从当前控制流可以确定 |
| **升级边界** | 只在旧数据/旧 journal 组合下出现 |
| **待真机验证** | 需要 Windows/macOS/Linux 实机证明 |
| **产品任务** | 不属于 Bug，但符合本地单机产品方向 |

### 1.3 环境限制

当前审查环境没有安装 pygame 和 Flask，也没有本地仓库归档，因此本次未独立完整运行：

- gameplay regression；
- storage unittest 全套；
- 20,000 步 stress；
- 渲染与保存 benchmark；
- wheel smoke。

仓库 `task.md` 记录：

- 107 项功能检查通过；
- 106 项存储、迁移和生命周期用例通过；
- 固定 seed 20,000 步通过；
- CI #22 的 core-only、Python 3.12/3.13 和三平台完整矩阵共 6 个作业通过。

这些是项目自测证据，本报告不将其冒充为当前环境独立执行结果。

仓库已经包含 CI workflow，但 GitHub 当前设置仍显示：

```text
main 未启用 branch protection
required status checks 关闭
```

---

## 2. 对 F22 与 F25 的正式复核

## 2.1 F22：原广义结论撤销，API 设计本身正确

### 已确认的事实

HTTP 客户端和 Flask API 都接受显式：

```text
profile_id
```

未传入时，统一 mutation 层会根据 `player` 生成稳定的 legacy profile UUID：

```text
ProfileIdentity.from_legacy_name(player)
```

因此：

```text
player = alice
多次省略 profile_id
→ 同一个稳定 profile
```

这正是排行榜需要的行为。若每次生成随机 UUID：

```text
alice 第一次 → profile A
alice 第二次 → profile B
```

会错误地把同一玩家拆成不同身份。

### 仍存在的独立集成问题

可选 HTTP launcher 目前没有 profile capability，但 launcher 仍会把本机 canonical default profile ID 显式传给游戏。

因此通过：

```bash
GAMES_USE_HTTP=1
```

从 launcher 修改多个名字并提交时，可能仍共享同一个 default profile ID。

这不是 API “省略 profile_id”逻辑的问题，而是**HTTP 调试 launcher 不应携带本机档案 ID**。

正确修复不是随机 ID，而是：

- HTTP 后端无 `profiles` capability 时：
  - launcher 隐藏/禁用本机档案 UI；
  - 启动游戏时传 `profile_id=None`；
  - 让服务端按玩家名派生稳定 debug identity；
- 或显式使用 `ProfileIdentity.from_legacy_name(player)`。

该项只影响可选调试模式，优先级低于默认本机路径。

---

## 2.2 F25：撤销“混合测试框架就是缺陷”的结论

当前三类测试职责清楚：

### `unittest`

用于：

- SQLite repository fixture；
- migration；
- journal；
- fault injection；
- profile、slot 和 receipt 契约。

### 自定义 regression runner

用于：

- 每个 pygame 检查运行在独立子进程；
- 隔离 SDL display/font/audio init/quit；
- 验证 launcher→游戏→launcher 生命周期；
- 支持按检查名称单独运行；
- 支持子进程 coverage。

### stress

用于：

- 固定 seed 长循环；
- 渲染 p95；
- SQLite 并发；
- worker 和 FD 资源循环。

三者目标不同，并且都可以独立运行。当前删除没有被实际使用的 pytest 依赖是合理的。

### 本任务书的调整

不再下发以下任务：

- “全部迁移到 pytest”；
- “删除自定义 runner”；
- “把 stress 强行改写成普通单元测试”。

后续测试优化只围绕：

- 统一顶层命令和结果格式；
- 增加属性测试；
- 提高覆盖率；
- 改善 artifact；
- 加强 release gate。

不做无收益的语法迁移。

---

## 3. 上一轮主要问题验收矩阵

| 上一轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| state operation 未进入 SQLite CAS | **修复到位** | schema v6 `state_receipts` + 事务内 `apply_state_operation` |
| startup profile load/default ensure 竞态 | **修复到位** | loading/load-failed/resolved；失败后显式 G 选择 guest |
| state operation 重放改变版本和时间 | **修复到位** | 同 operation receipt 幂等返回 |
| wall-clock rollback 产生更小 revision | **基本到位** | 持久逻辑时钟；时钟文件损坏/丢失仍需恢复 |
| superseded 冒充 committed | **修复到位** | `SaveState.SUPERSEDED` |
| superseded 不清 non-durable | **修复到位** | winner 顺序下清理旧内存项 |
| status getter 在帧线程 I/O | **修复到位** | 只读 snapshot 并安排 read-worker 重建 |
| 外部 pending 被旧 committed cache 遮蔽 | **修复到位** | 后台比较 journal 与 receipt revision |
| 特定 key 查询扫描 128 条 | **修复到位** | `read_key()` |
| state v1 无原始备份 | **修复到位** | migration-backup |
| v1 `current` key 未 canonicalize | **修复到位** | 迁移到真实 ruleset key |
| v1 使用升级时 catalog | **修复到位** | 固定 `LEGACY_RULESETS` |
| historical state quarantine 不提示 | **基本到位** | 有界统计和 notice；仍缺管理 UI |
| score/state outbox 健康状态未分开 | **修复到位** | `StorageStatus` 独立字段 |
| 2048 slot 语义不变量 | **修复到位** | won、死局、slot revision 均有检查 |
| quarantine 文案早于 ACK | **修复到位** | quarantining / failed / quarantined |
| slot load 阻塞 single writer | **基本到位** | callback chain 和 request coalescing |
| 两个 2048 实例共享一槽 | **未解决，产品决策** | 需单实例、ownership 或多槽 |
| receipt 过期后 request 冲突 | **修复到位** | 查权威 attempt，稳定 conflict |
| state table 缺 CHECK | **修复到位** | schema v6 CHECK |
| startup integrity 可能随数据增长 | **待基准** | 当前无性能故障证据，不提前削弱自愈检查 |
| HTTP identity | **原广义结论撤销** | API 正确；仅保留 HTTP launcher 集成项 |
| branch protection | **未完成** | 属仓库设置 |
| gameplay 不进 coverage | **修复到位** | parallel coverage + combine |
| 测试体系“混合” | **撤销** | 分层设计合理 |
| 依赖文件职责冲突 | **原结论过重** | pyproject 与固定 pygame 文件用途不同；仍需 release lock |
| LICENSE | **未完成，需所有者决定** | 不能由 AI 擅自选择 |
| 数据导出/恢复页 | **未完成** | 明确产品缺口 |

---

## 4. 当前发布阻断问题

## 4.1 CG10-F01：progress journal 聚合复用已提交 operation ID

- **优先级**：P0
- **证据**：代码路径确定 + 等价模型复现
- **涉及**：
  - `PersistentStateOutbox._merge_progress_operations`
  - `state_merge_receipts`
  - `LocalGameStore.apply_state_operation`
  - `_durable_state_write`

### 当前聚合规则

两个 progress operation 合并时：

```text
value = merge(existing, incoming)
logical_revision = max(existing, incoming)
operation_id = 较新 operation 的 ID
payload_hash = 合并后新 hash
```

### 失败场景

```text
A：revision 20，operation_id = A
A 已经写入 DB
state_merge_receipts[A] = hash(A)
A 的 journal 因进程在 commit 后、unlink 前崩溃而残留

B：revision 10，operation_id = B，较晚到达
journal 合并 A+B
聚合 operation_id 仍为 A
聚合 hash 变为 hash(A+B)

apply_state_operation:
  找到 state_merge_receipts[A]
  hash(A) != hash(A+B)
  → state_operation_conflict
```

Local backend 会把该非重试错误视为永久失败并删除当前 journal。

### 结果

B 的新增贡献可能是：

- 新完成关卡；
- 更高解锁等级；
- 更高分数；
- `completed_all=true`。

这些贡献会丢失。

### 修复方案

不要复用任一组成 operation 已经使用过的 ID。

推荐：

```text
aggregate_operation_id =
  hash(sorted(existing_component_hashes + incoming_component_hashes))
```

或使用新的 UUID，并把 component IDs 写入 envelope。

关键不变量：

- 聚合 operation ID 唯一；
- 聚合重放幂等；
- 组件重复应用不会改变结果；
- 旧 merge 贡献不会因 winner 已提交而被拒绝；
- `state_merge_receipts` 按聚合 ID 或组件 ID正确去重。

---

## 4.2 CG10-F02：业务行隔离后，state receipt 会阻止有效 journal 恢复

- **优先级**：P0
- **证据**：代码路径确定
- **涉及**：
  - `get_setting`
  - `get_progress`
  - `load_slot`
  - `quarantine_slot`
  - `state_receipts`
  - `state_merge_receipts`

### 当前流程

业务 JSON 损坏时：

```text
保存原始值到 invalid_local_state
→ DELETE settings/progress/save_slots row
```

但 state receipt 不变。

### 失败场景

1. operation X 已提交；
2. 进程在删除 journal 前崩溃；
3. 业务行之后被识别为损坏并隔离；
4. receipt X 仍存在；
5. journal X 重放；
6. `apply_state_operation()` 看到同 operation/hash；
7. 返回 `duplicate_operation=True`；
8. 不重建业务行；
9. backend 删除 journal。

### 结果

```text
业务行：不存在
receipt：committed
journal：被删除
```

最后一个可恢复副本丢失。

### 修复要求

以下操作必须与 receipt 生命周期联动：

- 隔离 setting；
- 隔离 progress；
- 隔离 slot；
- 删除 slot；
- 修复坏业务行。

推荐二选一：

#### 方案 A：receipt 带结果行 hash

duplicate 返回前验证：

```text
业务行存在
业务行 hash == receipt.result_value_hash
```

不一致则重放/修复。

#### 方案 B：隔离时事务内失效 receipt

```text
DELETE/mark state_receipts
DELETE/mark state_merge_receipts
INSERT state tombstone / recovery record
```

需要保留明确的：

```text
QUARANTINED
ROW_MISSING
RECOVERY_REQUIRED
```

---

## 4.3 CG10-F03：v5→v6 没有为既有本机状态建立 baseline receipt

- **优先级**：P0
- **证据**：升级控制流确定
- **涉及**：
  - schema v5 状态表
  - schema v6 `state_receipts`
  - 旧 v2 journal replay

### 场景

```text
schema v5 DB:
  autosave = newer
  updated_at = 200

pending-state:
  autosave = older
  logical_revision = old revision
```

升级创建空 `state_receipts`。

重放 older 时：

```text
prior receipt = None
→ apply as first state operation
→ DB newer 被 older 覆盖
```

受影响：

- profile display name；
- setting；
- `set_progress` 快照；
- 2048 autosave。

### 修复要求

schema v6 migration 为既有行生成 synthetic baseline receipt：

```text
semantic_key
baseline logical_revision
deterministic operation_id
payload_hash
occurred_at = existing updated_at
result_json
```

baseline revision 必须大于所有升级前可能存在的 state journal revision，或在重放时以现有行 `updated_at` 作为 fallback 比较。

同时：

- 将 state clock 提升到 baseline high-water；
- progress merge 即使旧于 baseline仍允许单调并入；
- setting/profile/slot 的旧快照被 supersede；
- 提供真实 v5 DB + stale journal fixture。

---

## 5. 其他高优先级问题

## 5.1 CG10-F04：state receipt 表自身缺少完整结构修复

`_schema_is_current()` 目前主要确认：

```text
state_receipts 列存在
state_merge_receipts 列存在
```

尚未完整核对：

- `semantic_key` / `operation_id` 是否为 PRIMARY KEY；
- CHECK 是否存在；
- 是否有重复行；
- `result_json` 是否可解码；
- `method` 是否有效；
- revision 和时间是否满足约束。

同版本损坏表即使被判“不当前”，初始化仍使用：

```sql
CREATE TABLE IF NOT EXISTS
```

不会自动重建已有错误表。

需要像 settings/progress/save_slots 一样进行显式 rename→rebuild→validate。

## 5.2 CG10-F05：损坏 state receipt 没有自动重建

`_decode_state_result()` 遇到坏 JSON 会抛：

```text
state_receipt_corrupt
```

当前 local backend 可能将它当永久 StoreError，移除 journal。

建议：

- 从权威业务行重建 result；
- 或删除坏 receipt 后在同一事务重放 operation；
- 保留坏 receipt 原文到 invalid table；
- 不能删除有效 journal。

## 5.3 CG10-F06：晚到 merge 后 winner receipt 的 `result_json` 已过期

晚到 progress merge 会更新业务值，但不会更新 winner `state_receipts.result_json`。

结果：

```text
DB progress = level 0 + level 1
winner receipt result = 只有 level 1
```

之后：

- duplicate winner；
- status reconstruction；
- UI 从 receipt 恢复；

可能得到旧值。

应让 state receipt 只表达顺序，读取结果时查询权威业务行；或更新 winner receipt 的 result 快照，但不改变 winner revision。

## 5.4 CG10-F07：status reconstruction 会把未应用的 stale merge误报为 committed

当前后台重建主要比较：

```text
journal order
winner state_receipt order
```

对普通 latest-value state 合理。

但 stale `merge_progress` 即使 order 较小，仍可能尚未应用；它是否完成应查：

```text
state_merge_receipts[operation_id]
```

仅因为存在更高 winner receipt，不能判定这个 merge contribution 已经提交。

## 5.5 CG10-F08：`state_merge_receipts` 会无限增长

每次 progress merge 都可能插入一行，当前 maintenance 只清理 score receipt。

长期使用、反复过关或大量恢复后，该表持续增长。

需要：

- `semantic_key/applied_at` 索引；
- 保留策略；
- 分批清理；
- 清理前确保不再有对应 journal；
- 说明 receipt 过期后的幂等策略。

## 5.6 CG10-F09：持久 state clock 损坏或丢失后缺少 DB high-water 恢复

当前 `.state-clock` 可以抵抗正常时钟回拨，但如果文件：

- 被删除；
- 被截断；
- 写入超大整数；
- 与 DB 分开恢复；

新 revision 可能：

- 低于 DB winner，被长期 supersede；
- 超出 SQLite 64 位范围，形成反复失败 operation。

启动时应从：

```text
MAX(state_receipts.logical_revision)
```

恢复 clock high-water，并验证/隔离坏 clock 文件。

## 5.7 CG10-F10：state revision 在 worker 分配，可能按处理顺序而不是用户动作顺序

operation 的 `updated_at` 在入口生成，但持久 revision 在 write worker 处理时分配。

跨进程场景：

```text
A 较早用户动作，但 worker 长时间阻塞
B 较晚用户动作先提交并取得 revision
A 恢复后取得更大的 revision
→ A 旧状态成为 winner
```

对于 progress merge无害，但会影响：

- setting；
- profile rename；
- autosave。

需要明确语义：

- latest commit wins；或
- latest user occurrence wins。

对于 autosave，更合理的是：

- 每 session 有 ownership；
- slot 以 `slot_revision + attempt_uuid` 比较；
- 或使用 HLC 的发生时间部分，不只在 worker 分配顺序。

## 5.8 CG10-F11：直接 store 写接口绕过 state receipt

以下 public 方法仍可直接修改业务表：

```text
ensure_profile
set_setting
set_progress
merge_progress
save_slot
```

默认 local backend 已使用 `apply_state_operation()`，但测试、工具或未来调用方若直接使用，会绕过 state receipt 顺序。

建议：

- 将其标为内部原语；
- public 写入口统一通过 operation；
- 或 direct 方法同时建立 baseline receipt。

## 5.9 CG10-F12：延迟/重复 score replay 会错误更新 `profile.last_used`

`record_mutation()` 在已有有效 score receipt 分支中仍会：

```text
last_used = MAX(last_used, now)
```

普通 pending 成绩恢复也使用写库时的 `now`，而不是原始 `occurred_at`。

结果：

- 很久以前完成的成绩今天补写；
- 该档案会被误认为今天最后使用；
- 下次启动可能默认进入错误档案。

建议：

- 新 attempt 使用 `occurred_at`；
- duplicate request 不修改 last_used；
- stale/no-op revision 不修改；
- profile 选择操作才是最权威的 last-used 信号。

## 5.10 CG10-F13：HTTP API 正确，但 HTTP launcher 仍可能显式复用 default profile

该项是对 F22 的精确收敛：

- API contract 无问题；
- omission-based stable identity 无问题；
- debug launcher 的 capability 集成仍需调整。

优先级低，仅影响显式 HTTP 调试模式。

## 5.11 CG10-F14：Sokoban/Zuma 没有读取 state result 中的 `value`

`apply_state_operation()` 对 progress 返回：

```json
{
  "ok": true,
  "value": { ...merged progress... }
}
```

Sokoban 和 Zuma 当前把整个 result 传给 `_apply_progress()`，而 `_apply_progress()` 查找的是顶层：

```text
unlocked_level
completed_levels
highest_score
```

因此不会应用 `result["value"]`。

本进程已经预先提高的 HUD 不会回退，但当 DB 合并出其他进程更高进度时，当前 HUD 不会即时更新。

修复：

```python
value = result.get("value", result)
```

并增加跨实例 merge→HUD 测试。

## 5.12 CG10-F15：2048 terminal slot 策略仍不够明确

读取 `game_state=gameover` 时，当前游戏不会加载终局棋盘，而会继续使用初始化后的新棋盘并立即保存。

实际效果相当于：

```text
自动丢弃终局 slot
开始新游戏
```

但没有显式 delete、历史查看或用户提示。

建议明确：

- terminal slot 提供“查看上局结果/开始新局”；
- 或提交成功后显式删除 autosave；
- 不通过随机新棋盘覆盖表达“清除”。

## 5.13 CG10-F16：同一档案的多个 2048 实例仍争用单一 autosave

当前 key：

```text
profile + 2048 + autosave
```

两个实例使用不同 attempt UUID，却覆盖同一 slot。

合理选择：

1. 默认限制每个 profile/game 一个活动实例；
2. 每 attempt 一个 autosave；
3. 支持多槽并在启动时选择。

不需要网络或账号。

## 5.14 CG10-F17：state quarantine 与 receipt 的恢复界面尚未形成产品闭环

底层已有：

- `pending-state-quarantine`
- `invalid_local_state`
- migration backup

但用户没有统一界面：

- 查看原因；
- 导出原文；
- 重试修复；
- 删除；
- 恢复备份。

## 5.15 CG10-F18：startup 完整性检查未来可能随历史增长

当前本机规模下没有性能故障证据，因此不建议现在增加“跳过检查”标记。

但应建立 benchmark，观察：

- attempts 10 万行；
- invalid rows；
- receipt 表；
- foreign key check；
- legacy marker。

只有实测超过启动预算后，才引入后台检查或 clean-shutdown fingerprint。

## 5.16 CG10-F19：当前 CI 已增强，但仓库保护仍未启用

workflow 本身不能替代仓库设置。

正式发布前应：

- 保护 main；
- required checks；
- 禁止未通过 CI 直接推送；
- 可选要求 PR review。

## 5.17 CG10-F20：测试分层合理，但缺少统一 release profile

不改写三套测试。

建议增加：

```text
python -m tests.run fast
python -m tests.run full
python -m tests.run release
```

它只编排现有：

- unittest；
- regression；
- stress；
- build；
- database fingerprint。

不改变每层内部实现。

## 5.18 CG10-F21：依赖范围与正式发行仍不可完全复现

当前：

- `pyproject.toml` 定义支持范围；
- `requirements.txt/environment.yml` 固定 pygame；
- dev 工具使用下界。

这种职责划分本身不是缺陷，但正式发行仍需要：

- constraints/lock；
- CI 工具固定版本；
- dependency audit；
- 构建产物记录 dependency manifest。

## 5.19 CG10-F22：LICENSE/NOTICE 仍缺失

必须由仓库所有者确认：

- 代码授权权利；
- AI 辅助代码政策；
- 游戏名称/商标；
- 字体；
- 图形；
- 将来的音频。

不能由 AI 擅自代选。

## 5.20 CG10-F23：数据导出、导入、恢复和清理仍缺少统一入口

当前本地数据已经较复杂：

```text
attempts
profiles
settings
progress
save_slots
score/state journals
quarantine
invalid rows
migration backups
```

正式桌面版本应提供统一的数据管理页面或 CLI。

---

## 6. 分模块审查

## 6.1 `game_service/local_backend.py`

### 已达到的水平

- 真异步；
- score/state 双 journal；
- Windows/POSIX 文件锁；
- 跨进程 state clock；
- non-durable fallback；
- read/write worker；
- SaveEvent/LocalStateEvent；
- typed slot load；
- retry/backoff；
- render-thread getter 去 I/O；
- ruleset freeze；
- progress journal merge；
- quarantine 与 migration backup。

### 主要剩余问题

- progress 聚合 operation ID；
- receipt/business-row一致性；
- state clock high-water；
- state receipt status 对 stale merge；
- merge receipt retention；
- worker allocation order；
- direct API bypass；
- recovery UI。

## 6.2 `game_service/store.py`

### 已达到的水平

- schema v6；
- score/state receipts；
- transaction-level state CAS；
- stale merge receipt；
- profile/FK；
- CHECK；
- legacy import；
- state corruption isolation；
- request expiry semantics；
- leaderboard/recent 当前名 join；
- profile collision merge。

### 主要剩余问题

- baseline receipt migration；
- receipt table rebuild；
- receipt result corruption；
- quarantine invalidation；
- stale merge winner result；
- merge receipt maintenance；
- delayed score replay last_used；
- public direct state writes。

## 6.3 启动器与 ProfileController

### 已改善

- startup state；
- generation；
- queued launch；
- explicit guest；
- IME；
- profile new/switch/rename；
- local async startup。

### 仍需

- 明确档案列表页；
- 删除/导出/合并；
- duplicate display-name UX；
- HTTP capability 分支；
- 键盘/手柄导航；
- launcher 主循环拆分；
- 本地进度中心。

## 6.4 2048

### 已改善

- 输入队列；
- load gate；
- typed load；
- retry/new-game confirm；
- slot schema 3；
- attempt restore；
- row-ID hint 降级；
- semantic validation；
- quarantine ACK；
- load coalescing。

### 仍需

- terminal slot 策略；
- multi-instance ownership；
- 多槽；
- 撤销；
- autosave debounce；
- RNG 注入；
- state receipt/business-row一致性。

## 6.5 Sokoban

### 已改善

- 重复计分；
- 跳关练习；
- 完整通关；
- 0 分通关；
- progress schema；
- campaign/practice；
- generation；
- 解锁 HUD。

### 仍需

- progress result 解包；
- 正式选关；
- 最少移动/推动；
- 是否使用撤销；
- 星级；
- 死锁检测；
- 提示；
- 地图闭合、可达、静态死角；
- XSB 导入；
- 编辑器；
- 固定逻辑窗口。

## 6.6 Zuma

### 已改善

- 多 pending reaction；
- swept collision；
- path bisect；
- 长帧余量；
- progress schema；
- generation。

### 仍需

- progress result 解包；
- reaction FSM；
- 重叠反应属性测试；
- RNG 注入；
- `incoming` deque；
- 训练/选关；
- 色弱符号；
- 原创道具和轨道编辑器。

## 6.7 Tetris

此前核心修复保持：

- 同义物理键；
- soft-drop repeat；
- piece-generation guard；
- top-out；
- 自定义辅助旋转命名。

后续适合单机舒适性：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- RNG 注入；
- 规则页；
- 静态网格缓存；
- 同义键逻辑动作边沿。

## 6.8 Snake

此前核心修复保持：

- 双转向队列；
- 长停顿保护；
- 速度间隔更新。

后续：

- 速度选择；
- 穿墙；
- 障碍；
- 双人同屏；
- RNG 注入；
- 色弱纹理；
- 静态棋盘缓存。

## 6.9 optional Flask/HTTP

当前定位合理：

- 默认不用；
- 无需复制本机进度/存档体系；
- 显式 `profile_id` 与稳定 legacy identity 均正确。

仍需：

- HTTP launcher capability 集成；
- debug mode 明确标识；
- 默认 loopback；
- LAN 风险提示；
- 不扩大成账号平台。

## 6.10 测试

### 保留现有分层

```text
unittest     → repository/migration/fault fixtures
regression   → pygame/SDL 独立子进程
stress       → 长循环、性能、资源、并发
```

### 后续增加

- progress aggregate crash fixture；
- receipt/business-row divergence；
- v5 baseline receipt；
- 属性测试；
- release orchestrator；
- 更高 coverage；
- branch protection。

---

## 7. 明确非目标

本项目不需要建设：

- 注册登录；
- 云端账号；
- 公网排行榜；
- 赛季；
- 匹配；
- 实时联机；
- 服务端权威判定；
- 反作弊；
- replay 审核；
- 在线商城；
- 强制联网；
- 默认遥测；
- 复杂权限平台。

Flask 继续只用于：

- 教学；
- 调试；
- 本机成绩 API 示例。

---

## 8. 推荐增量架构

不推翻当前结构，只增加 receipt 与业务行的一致性层：

```text
LocalStateOperation
    │
    ├── semantic_key
    ├── logical_revision
    ├── operation_id
    ├── component_operation_ids (progress aggregate)
    ├── payload_hash
    ├── ruleset_version
    └── occurred_at
            │
            ▼
SQLite transaction
    ├── validate existing business row
    ├── compare state receipt
    ├── apply business state
    ├── update state receipt/result hash
    └── record merge components
```

### 8.1 建议 receipt 补充字段

```text
result_value_hash
business_row_kind
business_row_version
state = committed / quarantined / tombstoned
```

### 8.2 Progress aggregate

推荐 envelope 增加：

```text
component_operations: [
  {operation_id, payload_hash}
]
```

聚合 ID由组件集合确定，不能复用组件 ID。

---

## 9. 完整优化任务清单

### 优先级定义

- **P0**：可能静默丢失本机进度/存档，或让旧数据覆盖新数据；发布阻断。
- **P1**：数据一致性、自恢复、迁移与正式发行基础。
- **P2**：维护性、UI、性能和可访问性。
- **P3**：玩法内容与桌面发行。
- **S/M/L/XL**：相对工作量。

---

## 9.1 P0：状态恢复与升级安全

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG10-P0-01 | progress aggregate 使用新 ID | aggregate operation model | 不复用任一 component ID | M |
| CG10-P0-02 | 记录 progress components | envelope/receipt | 每个贡献幂等且可审计 | L |
| CG10-P0-03 | committed-journal crash fixture | integration test | winner 已提交、journal 残留、旧 merge 晚到仍合并 | M |
| CG10-P0-04 | receipt 与业务行 hash 绑定 | schema v7 | duplicate 前验证业务行存在且匹配 | XL |
| CG10-P0-05 | quarantine 事务内失效 receipt | store policy | 删除业务行后不再返回虚假 committed | L |
| CG10-P0-06 | progress corruption 安全恢复 | repository | 坏旧值隔离，有效 incoming merge 不丢 | L |
| CG10-P0-07 | setting/slot 重放恢复测试 | fault fixtures | receipt 存在但 row 缺失时 journal 可重建 | M |
| CG10-P0-08 | v5→v6 baseline receipt | migration | 既有本机状态获得 synthetic winner | XL |
| CG10-P0-09 | state clock 同步 baseline high-water | migration/bootstrap | 升级后新 revision 大于 baseline | M |
| CG10-P0-10 | v5 新行+旧 journal fixture | upgrade test | 旧 setting/slot/profile 不覆盖新值 | L |
| CG10-P0-11 | P0 故障注入套件 | tests | commit-before-unlink、row quarantine、migration 均覆盖 | L |
| CG10-P0-12 | 发布门禁 | required CI job | 任一 P0 失败不得 tag/release | S |

---

## 9.2 P1：数据一致性、恢复与工程门禁

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG10-P1-01 | state receipt 表结构 verifier | schema check | PK/CHECK/重复行均核对 | M |
| CG10-P1-02 | state receipt 表显式重建 | migration | malformed v6 可备份修复 | L |
| CG10-P1-03 | state receipt 坏 JSON 重建 | repair path | 不删除有效 journal | M |
| CG10-P1-04 | winner receipt 返回权威当前值 | result strategy | late merge 后 duplicate result 不陈旧 | M |
| CG10-P1-05 | merge status 查 merge receipt | status refresh | stale merge 未应用时不显示 committed | M |
| CG10-P1-06 | state_merge_receipts 索引 | schema | semantic_key/applied_at 可维护 | S |
| CG10-P1-07 | merge receipt 保留策略 | maintenance | 有界增长且不破坏恢复 | M |
| CG10-P1-08 | state clock 损坏恢复 | clock repair | 删除/截断/超大 clock 可从 DB high-water 恢复 | L |
| CG10-P1-09 | state clock quarantine notice | recovery | 坏 clock 原件保留 | S |
| CG10-P1-10 | 本机状态发生顺序 ADR | ordering policy | commit-order 与 user-time 语义明确 | S |
| CG10-P1-11 | autosave ownership/order | slot policy | 延迟旧实例不覆盖当前活动局 | L |
| CG10-P1-12 | direct store write API 内部化 | API boundary | public 写入统一经过 operation | M |
| CG10-P1-13 | delayed score replay 不改 last profile | store fix | duplicate/pending replay 不更新 last_used 到当前时间 | M |
| CG10-P1-14 | score profile 使用 occurred_at | store policy | last_used 与真实游戏时间一致 | S |
| CG10-P1-15 | HTTP launcher capability 分支 | launcher | 无 profiles capability 时不发送 local default ID | M |
| CG10-P1-16 | HTTP identity contract test | tests | 显式 ID与按名派生均正确 | S |
| CG10-P1-17 | progress result 解包 | Sokoban/Zuma | 使用 `result.value` 更新 HUD | S |
| CG10-P1-18 | 跨实例 progress HUD 测试 | game test | DB 较高 merge 即时反映 | M |
| CG10-P1-19 | 2048 terminal slot ADR | product rule | 查看/删除/新开语义明确 | S |
| CG10-P1-20 | terminal slot 显式处理 | slot API/UI | 不用随机新棋盘隐式覆盖 | M |
| CG10-P1-21 | 2048 slot ownership | session token | 两实例不静默互相覆盖 | L |
| CG10-P1-22 | active-slot 冲突 UI | 2048 UI | 接管/另存/返回可选择 | L |
| CG10-P1-23 | state quarantine 状态页 | recovery UI | 显示数量、原因、路径 | L |
| CG10-P1-24 | quarantine 导出 | service/UI | 原文可保存 | M |
| CG10-P1-25 | migration backup 列表/恢复 | service/UI | 可预览和回滚 | L |
| CG10-P1-26 | invalid_local_state 管理 | retention | 可查看、导出、清理 | M |
| CG10-P1-27 | 数据导出 | archive format | attempts/profile/progress/slot/settings 全覆盖 | L |
| CG10-P1-28 | 数据导入预览 | validation | 冲突预览、原子提交、可回滚 | L |
| CG10-P1-29 | 结构化日志 | rotating logs | worker/migration/game 有 traceback | M |
| CG10-P1-30 | 当前 head CI 证据 | workflow run | 六个 job 可见成功 | S |
| CG10-P1-31 | main branch protection | repo settings | required checks 开启 | S |
| CG10-P1-32 | CI actions 权限收紧 | workflow | `contents: read` 等最小权限 | S |
| CG10-P1-33 | Actions 固定可信版本 | workflow policy | 降低供应链漂移 | M |
| CG10-P1-34 | 统一 release test 命令 | orchestrator | 保留三层测试，统一机器输出 | M |
| CG10-P1-35 | JUnit 汇总 | CI artifacts | unittest/regression/stress 结果统一可读 | M |
| CG10-P1-36 | gameplay coverage 持续门禁 | coverage | 当前能力不回退 | S |
| CG10-P1-37 | 覆盖率分阶段提升 | plan | core 90%，全项目 80% 的分阶段目标 | M |
| CG10-P1-38 | 属性测试工具评估 | ADR | 只用于适合的不变量，不改写现有 runner | S |
| CG10-P1-39 | state/progress 属性测试 | property tests | merge/CAS/idempotency 不变量 | L |
| CG10-P1-40 | 依赖 constraints/lock | release deps | 构建可复现 | M |
| CG10-P1-41 | 依赖审计 | pip-audit/等效 | 已知漏洞自动报告 | S |
| CG10-P1-42 | pyproject/requirements/environment 说明 | docs | 支持范围与固定开发版本职责清楚 | S |
| CG10-P1-43 | LICENSE 权利清单 | owner checklist | 可据此选择许可证 | M |
| CG10-P1-44 | NOTICE/素材清单 | inventory | 代码、字体、图形、音频来源明确 | M |
| CG10-P1-45 | 商标和名称检查 | release checklist | 正式分发风险记录 | M |
| CG10-P1-46 | SemVer/schema/ruleset 治理 | ADR | 兼容变化可追踪 | S |
| CG10-P1-47 | startup integrity benchmark | benchmark | 有数据后再决定后台化 | M |
| CG10-P1-48 | 数据恢复演练 | release test | 从 DB backup+journal 恢复成功 | L |

---

## 9.3 P2：维护性、桌面体验与可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG10-P2-01 | ProfileController 完整封装 | controller | launcher 不散落管理 Future | L |
| CG10-P2-02 | 本机档案列表页 | UI | 新建、切换、重命名、查看进度 | L |
| CG10-P2-03 | export-before-delete | profile flow | 删除可恢复 | L |
| CG10-P2-04 | duplicate name UX | profile UI | 同名档案可区分 | M |
| CG10-P2-05 | GameState Enum | state model | 无散落魔法字符串 | L |
| CG10-P2-06 | AttemptSaveController | common controller | BaseGame/2048 保存控制统一 | XL |
| CG10-P2-07 | LocalStateController | common layer | settings/progress/slot 状态统一 | L |
| CG10-P2-08 | ProgressController | common layer | generation、merge、event 统一 | L |
| CG10-P2-09 | SaveSlotController | common layer | loading/retry/new-game 统一 | L |
| CG10-P2-10 | InputManager | action map | 五款游戏统一输入 | L |
| CG10-P2-11 | 完整 IME 文本控件 | widget | 组合、光标、选择、退格稳定 | M |
| CG10-P2-12 | 键位重映射 | settings UI | 冲突检测、恢复默认 | L |
| CG10-P2-13 | 键盘菜单导航 | focus model | 不用鼠标可操作 | L |
| CG10-P2-14 | 手柄支持 | controller layer | launcher/五款游戏可用 | L |
| CG10-P2-15 | 音频系统 | BGM/SFX | 无设备不崩、音量持久 | L |
| CG10-P2-16 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG10-P2-17 | 高 DPI | DPI handling | 字体和图形清晰 | M |
| CG10-P2-18 | 字体 fallback | licensed font chain | 缺系统 CJK 字体仍可读 | M |
| CG10-P2-19 | 色弱符号 | patterns/shapes | 颜色不是唯一信息 | L |
| CG10-P2-20 | 高对比/降低动态 | accessibility | 脉冲和抖动可关闭 | L |
| CG10-P2-21 | Clock/RNG 注入 | deterministic services | seed+输入可重现 | L |
| CG10-P2-22 | 渐进抽取纯规则 Engine | rules modules | 核心测试无需 SDL | XL |
| CG10-P2-23 | launcher 拆分 | app/state/render/data | main 职责清晰 | L |
| CG10-P2-24 | 首页改为本地进度中心 | dashboard | best/recent/progress/continue | L |
| CG10-P2-25 | 静态 Surface 缓存 | profiler-driven | 有尺寸/主题失效 | L |
| CG10-P2-26 | 2048 autosave debounce | write policy | 减少重复 fsync，退出仍 flush | M |
| CG10-P2-27 | Zuma reaction FSM | explicit model | 重叠 reaction 可属性测试 | L |
| CG10-P2-28 | 可重复 benchmark CLI | benchmark | 带 OS、版本、seed | M |
| CG10-P2-29 | 30–60 分钟 soak | stability suite | 线程/FD/内存稳定 | M |
| CG10-P2-30 | 游戏内规则页 | controls/rules/version | ruleset 可查看 | M |
| CG10-P2-31 | 崩溃恢复页 | crash UI | 返回菜单并显示日志 | M |
| CG10-P2-32 | 设置页面 | settings UI | 窗口、音量、按键、辅助项 | L |

---

## 9.4 P3：玩法内容与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG10-P3-01 | Tetris 7-bag | comfort mode | 独立 ruleset | M |
| CG10-P3-02 | Tetris ghost/hold/lock delay | comfort features | 规则测试完整 | L |
| CG10-P3-03 | Snake 速度/穿墙/障碍 | local modes | 模式最佳分开 | L |
| CG10-P3-04 | Snake 双人同屏 | local multiplayer | 不依赖网络 | L |
| CG10-P3-05 | 2048 撤销 | undo model | slot/attempt 语义清楚 | L |
| CG10-P3-06 | 2048 多存档槽 | save UI | 可查看、继续、删除 | L |
| CG10-P3-07 | 2048 棋盘尺寸 | modes | ruleset 分离 | M |
| CG10-P3-08 | Sokoban 正式选关 | progress UI | practice/campaign 分开 | L |
| CG10-P3-09 | Sokoban 星级/最佳推动 | metrics | 规则明确 | M |
| CG10-P3-10 | Sokoban 死锁检测/提示 | analysis | 提示可关闭 | XL |
| CG10-P3-11 | Sokoban 编辑器 | XSB import/export | 地图验证和预览 | L |
| CG10-P3-12 | Zuma 训练/选关 | practice mode | 不混入完整通关 | L |
| CG10-P3-13 | Zuma 色弱辅助 | symbols | 球色可独立辨认 | M |
| CG10-P3-14 | Zuma 原创道具/轨道 | content | 每项有确定性测试 | XL |
| CG10-P3-15 | Zuma 轨道编辑器 | path tool | 合法、版本化、可预览 | L |
| CG10-P3-16 | 本机成就 | local achievements | 无账号、无遥测 | L |
| CG10-P3-17 | 离线每日挑战 | date seed | 完全离线 | L |
| CG10-P3-18 | 本地 replay | command log | 用于复盘和调试 | L |
| CG10-P3-19 | 中文/英文 | localization | 长文本布局测试 | L |
| CG10-P3-20 | Windows 桌面包 | installer/portable | 无 Python 可运行 | XL |
| CG10-P3-21 | macOS app bundle | package | 数据目录和关闭 smoke | XL |
| CG10-P3-22 | Linux package | AppImage/等效 | XDG 路径正确 | L |
| CG10-P3-23 | 自动发布 | tagged builds | smoke 通过才发布 | L |
| CG10-P3-24 | 签名与校验和 | release integrity | 下载可验证 | M |
| CG10-P3-25 | 截图/GIF/项目主页 | showcase | README 首屏展示玩法 | M |
| CG10-P3-26 | 谨慎新增游戏 | template/contracts | 同时交付规则、数据、输入、测试 | XL |

---

## 10. 必须新增的测试

## 10.1 Progress aggregate

```text
test_committed_newer_progress_journal_plus_older_merge_uses_new_aggregate_id
test_progress_aggregate_does_not_reuse_component_operation_id
test_progress_aggregate_replay_is_idempotent
test_progress_component_is_not_lost_after_commit_before_unlink_crash
```

## 10.2 Receipt 与业务行

```text
test_quarantined_setting_invalidates_or_repairs_state_receipt
test_quarantined_progress_does_not_discard_valid_pending_merge
test_quarantined_slot_replay_rebuilds_or_reports_tombstone
test_duplicate_state_operation_verifies_business_row_exists
test_corrupt_state_receipt_is_rebuilt_without_deleting_journal
```

## 10.3 v5→v6 upgrade

```text
test_v5_newer_setting_beats_older_v2_journal_after_upgrade
test_v5_newer_autosave_beats_older_v2_journal_after_upgrade
test_v5_profile_name_beats_older_pending_rename
test_v5_progress_baseline_still_accepts_stale_monotonic_merge
test_state_clock_is_seeded_above_migration_baseline
```

## 10.4 State receipt schema

```text
test_v6_state_receipts_without_primary_key_is_rebuilt
test_v6_state_merge_receipts_without_primary_key_is_rebuilt
test_duplicate_state_receipt_rows_are_quarantined_or_merged
test_invalid_state_receipt_result_json_is_repaired
```

## 10.5 Profile/score time

```text
test_delayed_score_replay_does_not_change_last_profile_to_retry_time
test_duplicate_score_request_does_not_touch_profile_last_used
test_new_score_uses_occurred_at_for_profile_activity
```

## 10.6 HTTP identity

```text
test_http_explicit_profile_id_is_preserved
test_http_omitted_profile_id_is_stable_by_player_name
test_http_launcher_without_profile_capability_does_not_send_local_default_id
```

## 10.7 游戏 UI

```text
test_sokoban_applies_progress_result_value
test_zuma_applies_progress_result_value
test_2048_terminal_slot_uses_explicit_policy
test_two_2048_instances_detect_slot_ownership_conflict
```

## 10.8 保留的测试分层

```text
unittest repository/migration suite
pygame subprocess regression suite
deterministic stress suite
```

新增顶层编排，但不强制改写内部框架。

---

## 11. 性能与稳定性门槛

1. pygame 主线程不得执行：
   - SQLite；
   - score/state journal scan；
   - OS 文件锁等待；
   - receipt reconstruction；
   - profile migration；
   - save-slot I/O；
2. `submit_score_async()` p99 ≤2 ms；
3. `retry_failed_saves()` 调用本身 p99 ≤2 ms；
4. status getter p99 ≤0.2 ms；
5. 32 进程同 state key：
   - 最终 DB 值符合 revision/merge policy；
   - journal 最终清空；
   - 不丢 progress component；
6. commit-before-unlink 强制崩溃后能恢复；
7. v5 DB + v2 journal 升级不回退；
8. 正常游戏目标 60 FPS，基准机 p95 ≤16.7 ms；
9. 保存、进度和存档不得造成 >50 ms 主线程长帧；
10. 100 次游戏切换：
    - 线程回基线；
    - FD 不增长；
    - Surface/内存稳定；
11. 30–60 分钟：
    - pending 最终 committed/superseded/recovery/quarantined；
    - receipt 表增长受控；
    - 不无限重试；
12. 满盘、只读、坏 DB、坏 receipt、坏 slot、坏 progress：
    - 游戏仍可玩；
    - 最后一个有效副本不静默删除；
    - 有恢复提示。

---

## 12. 稳定版本质量门禁

正式稳定版本至少满足：

- 全部 P0 关闭；
- progress aggregate operation 不复用 component ID；
- receipt 与业务行一致；
- v5→v6 baseline receipt 完整；
- status getter 不做主线程 I/O；
- score/state 两类 journal 都可恢复；
- ruleset 在创建时冻结；
- progress 在 journal 和 DB 两层均单调；
- 2048 多实例策略明确；
- migration/backup/export 完整；
- GitHub Actions 当前 head 三平台实际通过；
- main required checks 开启；
- core-only 测试；
- 三层测试均可单独运行；
- coverage、lint、类型和依赖审计；
- LICENSE/NOTICE 在权利确认后加入；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代：

- 跨进程 barrier；
- commit-before-unlink；
- 升级 fixture；
- receipt corruption；
- 真实玩家测试。

---

## 13. 推荐实施顺序

### M0：关闭三项数据发布阻断

1. progress aggregate operation identity；
2. receipt/business-row一致性；
3. v5→v6 baseline receipt；
4. 对应故障与升级测试。

### M1：硬化 state receipt 和恢复体系

- receipt 表重建；
- corrupt receipt repair；
- merge status/result；
- clock high-water；
- profile last_used；
- quarantine/data export；
- 2048 slot ownership。

### M2：工程门禁

- required checks；
- release test orchestrator；
- coverage 提升；
- property tests；
- dependency lock/audit；
- LICENSE 权利清单；
- recovery drill。

### M3：桌面体验

- 本机档案页；
- InputManager；
- 键位/手柄；
- 音频；
- DPI/字体；
- 可访问性；
- launcher 拆分；
- RNG/Clock；
- 纯规则 Engine；
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

## 14. 最终判断

本轮修改仍然是明显进步：

- state CAS 已经进入 SQLite；
- startup profile 状态机已基本正确；
- state getter 不再阻塞帧线程；
- 2048 恢复更安全；
- HTTP identity 与测试分层的设计理由成立；
- CI 和故障诊断显著成熟。

本轮需要特别避免两个误区：

1. 不能因为 state journal 文件层 CAS 已通过，就认为 progress 聚合与 receipt 一定正确；
2. 不能因为测试工具不同，就把合理的分层测试架构判为缺陷。

下一步最值得做的是：

> **修复 progress aggregate operation ID、让 receipt 与实际业务行保持一致，并为 schema v5 的既有状态建立 v6 baseline receipt。**

完成这三项后，项目的本地数据路径将更接近真正可长期升级、可故障恢复的桌面应用；之后再集中投入档案体验、可访问性、玩法和三平台发行。
