# Classic Games Hub 第十一次代码审查与本地优先优化任务书

> 审查日期：2026-08-25
> 当前基线：`main` 分支 commit `8f1b7131002047225f8ad6152478e7d088aa5296`（`8f1b713`）
> 对比基线：上一轮审查 commit `9ac703f82b4ec2fddf6b3fef7a656e929f280800`
> 增量提交：`de04abc`、`4a42655`、`8f1b713`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 审查范围：五款游戏、启动器、共享 UI、本机档案、SQLite、score/state journal、迁移、数据 CLI、可选 Flask、测试、CI 和发行治理

---

## 0. 执行摘要

本轮修复总体有效，上一轮最核心的三项数据问题已经进入实际代码：

- Progress journal 使用独立 aggregate operation ID，并保留 component ID/hash；
- State receipt 绑定业务引用与业务值 hash，业务行损坏时会失效 receipt；
- Schema 已升至 v7，为旧业务状态建立 baseline receipt，并修复损坏 receipt；
- State merge receipt 有索引和保留期；
- State clock 会参考数据库 high-water；
- 2048 增加 autosave owner token、显式接管和终局棋盘恢复；
- HTTP 调试模式不会把本机 canonical profile ID错误地强行传给不支持 profiles 的后端；
- Sokoban/Zuma 已读取 progress 写入结果中的权威 `value`；
- Score 延迟补传不再用补传时刻刷新档案活跃时间；
- 新增本机数据 status/export/preview-import/import CLI；
- 新增统一 `fast/full/release` 测试入口、release constraints、依赖审计和机器可读结果；
- 当前 head 的 GitHub Actions CI #26 已完成且结论为 success。

现有架构方向正确，无需推倒：

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

但是，新加入的数据管理 CLI 和 2048 ownership 使新的主要风险集中到“备份/恢复是否真的完整”和“多实例接管是否会覆盖较新存档”。

### 当前发布阻断问题

1. **导出目标可以直接等于当前数据库文件。**CLI 会在读取完成后用 JSON archive 原子替换 SQLite 文件，导致数据库被覆盖。
2. **导出不会包含正在等待提交的 `pending/` 和 `pending-state/`。**即使使用 `--include-recovery`，当前也只包含 quarantine、migration backup 和数据库备份；最近成绩、进度或 autosave 可能不在导出中。
3. **合并导入到非空数据库时，自增 `id` 冲突会让大量本应新增的 attempt 被 `INSERT OR IGNORE` 静默跳过。**Preview 只按 `attempt_uuid` 判断，可能仍显示“new”。
4. **导入结束后调用 `_seed_state_baselines()` 会无条件改写所有 state receipt。**它可能降低既有 winner revision，使旧 journal 重新具备覆盖新值的资格。
5. **Schema baseline 的 revision 只来自业务行时间，不能保证高于升级前的异常高 revision journal。**在历史时钟跳变或 clock 文件曾异常增大的情况下，旧状态仍可能覆盖新业务行。
6. **一个较新的、最终被仓储拒绝的 state operation，可以先替换并删除旧的有效 pending。**典型例子是 2048 `slot_in_use`、非法档案名或非法 slot state。
7. **2048 接管使用冲突发生时缓存的棋盘，没有重新读取并比较最新 slot revision/value hash。**原 owner 在玩家按 K 前继续移动时，较新的棋盘会被旧快照覆盖。
8. **恢复 `won/gameover` slot 后，如果 `confirmed_score` 为空或低于终局分数，游戏不会主动补交该 attempt 的最终成绩。**极窄的 crash window 仍可造成终局分数未记录。

因此，本轮结论是：

> **成绩与状态 journal 主链已经较成熟；下一阶段优先级最高的不是继续扩张功能，而是把数据 CLI、baseline ordering 和 2048 ownership 做成真正不会丢数据的闭环。**

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的检查

- 锁定当前 `main` 最新 commit；
- 对比上一轮基线到当前 head 的全部差异；
- 逐项核对上一轮任务书；
- 阅读当前：
  - `client/common/ui.py`
  - `client/common/network.py`
  - `client/profile_controller.py`
  - `client/launcher.py`
  - 五款游戏
  - `game_service/catalog.py`
  - `game_service/profile.py`
  - `game_service/progress.py`
  - `game_service/mutation.py`
  - `game_service/service.py`
  - `game_service/local_backend.py`
  - `game_service/store.py`
  - `game_service/data_cli.py`
  - `server/app.py`
  - storage v2/v4/v5/v6/v7/v8 tests
  - regression、stress、release runner
  - CI workflow、README、CHANGELOG、ADR 和任务记录；
- 对以下路径进行了控制流/状态模型复核：
  - data export 覆盖数据库；
  - data import 自增 ID 冲突；
  - data import baseline receipt 降级；
  - baseline 与异常高 revision journal；
  - permanent StoreError 之前 journal 已被替换；
  - 2048 缓存快照接管；
  - 2048 version 4 `won_announced`；
  - terminal slot 与最终成绩恢复；
- 查询当前 GitHub Actions：CI run #26 为 completed/success；
- 查询仓库设置：`main` 仍未启用 branch protection 和 required status checks。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题主要路径和关键边界均已闭环 |
| **基本到位** | 原问题关闭，但组合边界仍需修补 |
| **部分到位** | 已有 API/数据结构，但产品语义或故障恢复不完整 |
| **代码路径确定** | 从当前控制流可以确定 |
| **状态模型复现** | 使用等价状态顺序复现最终错误状态 |
| **升级边界** | 只在旧库、旧 journal 或时钟异常后触发 |
| **待真机验证** | 需要 Windows/macOS/Linux 实际构建或运行 |
| **产品任务** | 不是当前 Bug，而是符合单机产品定位的完善项 |

### 1.3 本次限制

当前执行环境没有安装 pygame 和 Flask，也没有把仓库源码下载到本地，因此本次未独立执行：

- gameplay regression；
- storage unittest 全套；
- stress；
- wheel smoke；
- 真实 GUI 性能。

当前 head 的 GitHub Actions CI #26 已成功，这是有效的远端自动化证据；仓库任务记录中的具体测试数量和性能数字仍按“项目自测”处理，不冒充本次环境独立复现。

---

## 2. 上一轮问题修复验收矩阵

| 上一轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| Progress aggregate 复用 component ID | **修复到位** | schema 3 使用 component 集合生成独立 aggregate ID |
| Receipt 与业务行脱节 | **基本到位** | value hash、state ref、隔离时失效 receipt；slot 元数据绑定仍不完整 |
| v5→v6 baseline receipt | **主体到位** | schema v7 已 seed baseline；异常高 revision journal 和 import 重写仍有缺口 |
| Receipt 表结构损坏 | **修复到位** | 检查 PK/CHECK/index，并可重建 |
| Receipt result JSON 损坏 | **修复到位** | 可从权威业务行重建 |
| 晚到 merge 后 winner result 过期 | **修复到位** | winner receipt result/value hash 会更新 |
| Stale merge 状态被误报 committed | **修复到位** | 状态重建检查 component receipts |
| Merge receipt 无限增长 | **基本到位** | 365 天保留期、索引、分批清理；极端 pending 保护仍可加强 |
| State clock 损坏 | **基本到位** | DB high-water 恢复；未纳入所有 pending journal high-water |
| Direct store 写入无 receipt | **部分到位** | 会写 baseline；baseline 不应降低既有 receipt |
| Score 延迟补传刷新 last profile | **修复到位** | 使用 occurred_at，duplicate/no-op 不再刷新 |
| HTTP identity | **修复到位** | 显式 ID 保留；省略时按名字稳定派生；launcher 按 capability 处理 |
| Sokoban/Zuma progress result | **修复到位** | 已读取响应中的权威 `value` |
| 2048 terminal slot | **基本到位** | 能恢复终局棋盘；未确认分数不会自动补交 |
| 2048 多实例 ownership | **部分到位** | active owner、K 接管、release；仍缺快照 CAS 和 owner epoch |
| 数据管理能力 | **部分到位** | CLI 已有；完整性与冲突语义尚未达到可恢复工具标准 |
| Release 测试入口 | **基本到位** | fast/full/release 已有；缺每阶段 timeout 和 wheel 安装 smoke |
| CI current head | **修复到位** | CI #26 success |
| Branch protection | **未完成** | main 仍不受 required checks 保护 |
| 测试分层 | **设计合理** | unittest / SDL 子进程 regression / stress 各有职责，不强制统一框架 |
| LICENSE/NOTICE | **未完成** | 需仓库所有者确认权利后决定 |

---

## 3. 当前发布阻断问题

## 3.1 CG11-F01：导出文件可以覆盖当前 SQLite 数据库

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`game_service/data_cli.py::export_data`

当前导出：

```text
读取数据库
→ 构造 JSON
→ 写临时文件
→ os.replace(temp, output)
```

没有检查：

```text
output == database
output == database-wal
output == database-shm
```

命令：

```bash
classic-games-data --database games.db export games.db
```

会把 SQLite 文件替换成 JSON archive。

### 修复要求

- 对数据库、WAL、SHM、journal 目录、migration backup 目录做路径冲突检查；
- 默认拒绝覆盖任何已有文件；
- 只有显式 `--force` 才允许覆盖普通输出文件；
- 即使 `--force` 也永远不得覆盖当前数据库及其 sidecar；
- 添加相对路径、符号链接和大小写不敏感文件系统测试。

---

## 3.2 CG11-F02：数据导出不包含 active pending records

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`game_service/data_cli.py::_recovery_paths / export_data`

当前 `--include-recovery` 包含：

```text
数据库 backup/corrupt 副本
pending-quarantine
pending-state-quarantine
pending-migration-backup
pending-state-migration-backup
```

但不包含：

```text
pending/
pending-state/
```

因此：

- 数据库暂时不可写时的新成绩；
- 尚未提交的昵称、设置、进度；
- 最新 2048 autosave；

不会进入 archive。

`status` 同样不会明确报告 active pending 数量。

### 修复要求

二选一：

1. 导出前协调后台并可靠 flush，确认无 active pending；
2. 将 score/state pending 作为 archive 的正式组成部分。

推荐 archive v2：

```text
tables
score_pending
state_pending
recovery_evidence
manifest
```

导入时恢复到 journal，而不是直接写业务表。

---

## 3.3 CG11-F03：合并导入常因自增 ID 冲突静默漏掉 attempts

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `NATURAL_KEYS["attempts"] = ("attempt_uuid",)`
  - `preview_import()`
  - `import_data()` 的 `INSERT OR IGNORE`

典型场景：

```text
目标 DB 已有 id=1, attempt_uuid=A
archive 有 id=1, attempt_uuid=B
```

Preview：

```text
attempt_uuid B 不存在
→ new += 1
```

Import：

```sql
INSERT OR IGNORE ... id=1 ...
```

因主键 id 冲突被忽略，导入仍返回 `ok=True`。

这在两个独立数据库合并时非常常见，因为两边自增 ID 通常都从 1 开始。

### 修复要求

- 导入 attempts 时丢弃 archive 的 surrogate `id`，由目标 DB 分配；
- 同时检查：
  - attempt_uuid；
  - request_id；
  - source_key；
- 冲突分为：
  - exact duplicate；
  - alternate unique conflict；
  - semantic conflict；
- `INSERT OR IGNORE` 不得用于掩盖错误；
- inserted/skipped/conflicted 必须逐类报告；
- `invalid_*` 表的 ID也应重分配。

---

## 3.4 CG11-F04：导入会无条件重写既有 state receipts

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `data_cli.import_data()`
  - `LocalGameStore._seed_state_baselines()`
  - `_insert_state_receipt()` 的 upsert

导入事务结束前：

```python
store._seed_state_baselines(connection)
```

会为**所有当前业务行**重写 baseline receipt，不只为新导入行补 receipt。

若原 receipt：

```text
revision = 200
```

但业务行 `updated_at` 生成的 baseline：

```text
revision = 100
```

导入后 winner revision 被降低；revision 150 的旧 journal 将重新具备写入资格。

### 修复要求

- 改为 `_seed_missing_state_baselines()`；
- 已存在 receipt 时不得降低 revision；
- 若业务行因导入确实改变，生成一个全新的 state operation；
- import 只为新插入且无 receipt 的业务行建 baseline；
- 导入后将 state clock 提升到新 high-water；
- 增加“非空目标 DB + 较高 receipt + stale pending”测试。

---

## 3.5 CG11-F05：Baseline receipt 不一定高于升级前的旧 journal

- **优先级**：P0
- **证据**：升级控制流确定
- **涉及**：
  - `_seed_state_baselines`
  - `_write_state_baseline`
  - `apply_state_operation` 对 baseline 的比较

Baseline revision 目前主要来自：

```text
int(business_row.updated_at * 1e9)
```

旧 journal revision 可能因以下原因更高：

- 历史系统时间曾跳到未来；
- `.state-clock` 曾经异常增大；
- receipt 表损坏重建，但 journal 保留；
- 数据从不同设备或备份组合恢复。

若旧 journal revision 高于 baseline，当前逻辑会接受它，即使它的 `updated_at` 早于 baseline。

### 修复要求

对于 baseline receipt：

- latest-value 操作：
  ```text
  incoming.occurred_at <= baseline.occurred_at
  → 无条件 superseded
  ```
- monotonic `merge_progress`：
  - 仍允许旧 component 幂等合入；
- migration 可以扫描 pending state journal high-water；
- direct baseline 写入不得降低现有 revision；
- receipt 表重建时保留可验证的旧 winner high-water。

---

## 3.6 CG11-F06：永久失败的新 state operation 会抹掉旧的有效 pending

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `PersistentStateOutbox.put`
  - `LocalBackendClient._durable_state_write`

非 progress 状态采用 latest-value：

```text
新 operation 先替换旧 journal
→ 再写 SQLite
```

若 SQLite 返回永久错误：

```text
invalid display name
slot_in_use
invalid slot owner/state
state operation conflict
```

backend 会删除当前 journal。

旧的有效 pending 已在第一步被替换，无法恢复。

### 修复方案

- 写入 journal 前执行全部可纯函数完成的语义校验；
- `put()` 返回 previous operation/raw bytes；
- incoming 永久失败时，在同 key lock 下恢复 previous；
- 更稳妥的方案是每 operation 独立文件 + winner index；
- `slot_in_use` 不应删除另一个 owner 的既有 pending；
- 永久失败 operation 应进入 quarantine，而不是无证据删除。

---

## 3.7 CG11-F07：2048 接管可能用旧快照覆盖 owner 的较新棋盘

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `_slot_conflict_saved`
  - `_take_over_conflicting_slot`
  - store 的 `takeover_from` 检查

流程：

```text
B 读取到 A 的 slot revision 10
→ 显示冲突
A 继续移动到 revision 20
B 按 K
→ B 使用缓存的 revision 10 棋盘
→ takeover_from=A，仓储接受
→ revision 20 被旧棋盘覆盖
```

当前检查只证明“被接管 owner token 正确”，没有证明快照仍是最新。

### 修复要求

接管时重新读取当前 slot，并使用 CAS：

```text
expected_owner_token
expected_slot_revision
expected_value_hash
```

若任一变化：

```text
显示“存档已更新，请重新确认”
```

不要直接使用冲突发生时缓存的棋盘。

---

## 3.8 CG11-F08：恢复终局 slot 时不会补交未确认最终分数

- **优先级**：P0/P1
- **证据**：代码路径确定
- **位置**：`Game2048._poll_slot_load`

Slot 保存了：

```text
attempt_uuid
attempt_revision
score
confirmed_score
game_state
```

但恢复 `won/gameover` 后，只重建棋盘和内存状态，没有检查：

```text
confirmed_score is None
confirmed_score < score
```

也没有重新调用 `_submit_score()`。

### 修复要求

- terminal/won slot 恢复后检查 score sync；
- 使用同一 attempt UUID 和下一 revision 补交；
- 保存 `last_score_request_id` 或可靠生成新的 request ID；
- score receipt/attempt 已存在时幂等 no-op；
- 增加“终局 autosave 已提交、score 尚未入 spool 时强制崩溃”的测试。

---

## 4. 其他高优先级问题

## 4.1 CG11-F09：2048 version 4 恢复忽略保存的 `won_announced`

当前：

```python
stored won_announced
→ 仅 version 2/3 使用
version 4 → self._won_announced = self.won
```

存在 crash window：

```text
tile 已合成 2048
第一次 autosave: won=true, won_announced=false, state=playing
程序崩溃
恢复 v4
→ won_announced 被设为 true
→ 不再显示达成 2048 overlay
→ milestone 也可能未提交
```

应对 version 4 同样恢复真实 `won_announced`，并在：

```text
won=true
won_announced=false
state=playing
```

时恢复到待展示胜利状态。

## 4.2 CG11-F10：Owner release 后，已被接管的旧实例可再次自动认领

当前 slot 若为：

```text
owner_status = released
```

任意新 owner 都可直接写入。

场景：

```text
A 运行
B 接管 A
B 退出并写 released
A 仍在运行，下一次移动写 active
→ A 自动重新成为 owner
```

需要 `owner_epoch/ownership_generation`：

- takeover/claim 增加 epoch；
- 旧 owner 只能写自己持有的 epoch；
- released 后的新 claim 必须显式引用前一 owner/epoch；
- 被撤销 owner 永久不能自动复活。

## 4.3 CG11-F11：Save-slot receipt 没绑定完整行语义

`_authoritative_state()` 对 slot 主要 hash：

```text
state_json
```

但没有把以下字段纳入 value hash：

```text
ruleset_version
state_version
```

若 state JSON 正确但 metadata 列损坏：

- receipt 仍可能被视为有效；
- journal duplicate 被删除；
- 游戏读取时却报告规则不兼容或版本异常。

应对完整业务行建立 canonical state value。

## 4.4 CG11-F12：State clock high-water 没包含所有 pending journal

启动时 high-water 来自：

```text
MAX(state_receipts.logical_revision)
```

若 `.state-clock` 丢失，而 pending journal 中存在更高 revision：

- 新用户操作可能取得更低 revision；
- `put()` 会把新操作判为 superseded；
- 用户刚做的设置/存档更新被忽略。

启动时应有界扫描 pending 的 max revision，或在 key put 时以现有文件 revision作为 observed。

## 4.5 CG11-F13：Data export 不是跨表一致快照

Export 使用同一个连接依次执行多个 `SELECT *`，但没有显式 read transaction 或 SQLite backup snapshot。

运行中的游戏可能在两个表读取之间提交：

```text
profiles 是旧快照
attempts 是新快照
progress 又是后一个快照
```

建议：

- `BEGIN` 只读事务；
- 或先使用 SQLite backup API 创建临时 snapshot，再导出；
- 同时对 active journals 做冻结/复制。

## 4.6 CG11-F14：Archive 解析缺少大小、深度和非标准数字限制

`_load_archive()` 当前：

```python
path.read_text()
json.loads()
```

没有：

- 总文件大小上限；
- 最大行数；
- 最大嵌套深度；
- 最大字符串；
- `parse_constant` 拒绝 NaN/Infinity。

本地导入文件仍可能损耗大量内存，或产生随后被 `INSERT OR IGNORE` 静默跳过的数据。

## 4.7 CG11-F15：Import preview 不是语义验证

Preview 只检查：

- 字段是否属于当前表；
- natural key 是否存在。

没有验证：

- UUID/ID/时间/状态/score；
- JSON 列；
- ruleset；
- foreign key；
- alternate unique；
- attempt invariant；
- game-specific progress/slot schema。

Preview 显示 `ok=True` 不代表 apply 能完整导入。

## 4.8 CG11-F16：Import 与正在运行的游戏没有维护锁

导入可能与：

- score replay；
- state replay；
- autosave；
- profile rename；

同时发生。

SQLite 会串行单个事务，但 raw import 和 baseline seed 绕过正常 operation ordering。

建议建立 application maintenance lock：

```text
export/import/migrate
↔ launcher/game writers
```

或明确要求关闭应用，并在 CLI 中探测活动实例。

## 4.9 CG11-F17：Export archive 覆盖普通文件也没有确认

除数据库本身外，当前导出会直接替换同名普通文件。

应默认拒绝已有输出，使用 `--force` 明示。

## 4.10 CG11-F18：Recovery files 被导出但不会被导入

`recovery_files` 进入 JSON archive，但 `import_data()` 完全忽略该字段。

需要明确：

- 这些文件只是证据，还是可恢复内容；
- 若只是证据，文档和 manifest 明示；
- 若需恢复，应恢复到隔离目录，不能直接覆盖 active journal。

## 4.11 CG11-F19：Data export 可能占用无界内存

- 所有表使用 `fetchall`；
- recovery 每文件最大 8 MiB，但没有总数量/总字节上限；
- archive 整体一次性序列化。

应使用流式导出、总大小预算和进度报告。

## 4.12 CG11-F20：Direct state APIs 的 baseline 仍可能降低 winner

以下 public API：

```text
ensure_profile
set_setting
set_progress
merge_progress
save_slot
```

写业务行后调用 `_write_state_baseline()`。

应：

- 统一通过 `apply_state_operation()`；
- 或 baseline upsert 只允许提高 revision；
- 将 direct 方法标记为内部 migration/test primitive。

## 4.13 CG11-F21：State receipt status 读取总是取得写事务

`get_state_receipt()` 使用：

```sql
BEGIN IMMEDIATE
```

即使 receipt 和业务行都正常，也会取得写锁。

它已在 read worker 中，不会冻结 pygame，但 pending HUD 每 0.5 秒刷新时可能与 autosave/score 写入争锁。

建议：

- 第一阶段普通读；
- 只有发现需要修复时才提交短写事务；
- status reconstruction 与 repair 分离。

## 4.14 CG11-F22：构造 LocalBackendClient 时仍同步扫描 score outbox

构造阶段：

```python
had_pending_scores = bool(self.outbox.list())
```

会：

- 扫描；
- 解析；
- 升级；
- quarantine；
- fsync。

即便 `defer_initialization=True`，大量 pending 文件仍可能推迟 launcher 第一帧。

应改为廉价计数或完全放到 read worker。

## 4.15 CG11-F23：Permanent state error 应保留证据

`state_operation_conflict`、`slot_in_use`、非法 state 等目前可能导致 journal 被直接删除。

对于本机恢复系统，更安全的是：

```text
PERMANENT_INPUT_ERROR → quarantine
```

而不是无证据删除，尤其 journal 可能包含用户唯一存档。

## 4.16 CG11-F24：2048 autosave 每次有效移动都产生一次持久写

后台线程避免了帧阻塞，但快速操作会形成大量：

- journal replace/fsync；
- SQLite transaction；
- receipt update。

建议：

- 100–250 ms debounce；
- 最新状态 coalesce；
- 状态切换、暂停、退出立即 flush；
- 不牺牲 crash recovery。

## 4.17 CG11-F25：2048 同一档案仍只有一个 autosave 槽

Ownership 只能减少误覆盖，不能让两个合法长局并存。

产品选择：

- 每档案每游戏只允许一个活动窗口；
- 每 attempt 一个自动槽；
- 或提供多槽。

## 4.18 CG11-F26：Release runner 没有每阶段 timeout

`tests.release` 的每个 `subprocess.run()` 没有 timeout。

本地 release 检查若某一层死锁，会无限等待；CI 只有外层 job timeout。

应为 storage/stress/gameplay/build 分别设置预算并记录 timeout 结果。

## 4.19 CG11-F27：Release gate 没有 wheel 安装 smoke

任务记录曾手工构建 wheel，但正式 `release` profile 当前只包含：

- ruff；
- pip-audit；
- compile；
- storage；
- stress；
- gameplay。

建议增加：

```text
build wheel
创建全新 venv
安装 wheel
classic-games --smoke / import smoke
数据目录 smoke
```

## 4.20 CG11-F28：Main 尚未启用 required checks

当前 CI #26 成功，但 main branch protection 仍关闭。

仓库内 workflow 不能替代 GitHub 仓库设置。

## 4.21 CG11-F29：Action 未按 commit SHA 固定

`actions/checkout@v7`、`actions/setup-python@v7` 使用 major tag。

正式发行可固定到审核过的 commit SHA，并由 Dependabot/Renovate 更新。

## 4.22 CG11-F30：Release constraints 只固定直接依赖

`constraints-release.txt` 精确固定顶层版本，但没有：

- transitive lock；
- hash；
- 平台 wheel manifest。

这是比完全不固定更好，但仍不是完整可复现构建。

## 4.23 CG11-F31：LICENSE/NOTICE 仍缺失

正式分发前需仓库所有者确认：

- 代码权利；
- AI 辅助代码政策；
- 游戏名称和商标；
- 字体、图形、音效；
- 第三方依赖许可。

不能由 AI 擅自替所有者选择许可证。

---

## 5. 游戏专项审查

## 5.1 俄罗斯方块

已经保持的修复：

- 同义物理键独立记录；
- 持续软降；
- 大 dt 不跨 piece generation；
- top-out；
- 自定义辅助旋转不再误称标准 SRS。

仍可改善：

1. 同时按下 `←` 与 `A` 会产生两个立即移动动作；建议按逻辑 action 边沿触发一次。
2. `↓` 与 `S` 同理，可能额外软降一格。
3. 随机块仍非 7-bag。
4. 无 ghost、hold、lock delay。
5. RNG 不可注入。
6. 静态网格和侧栏仍可缓存。
7. 应在游戏内显示当前 ruleset 和旋转规则。

## 5.2 贪吃蛇

已保持：

- 双转向队列；
- 长停顿只前进一步；
- 食物后重新计算 interval；
- 可进入尾部离开位置的正确碰撞语义。

仍可改善：

- RNG 注入；
- 速度选择；
- 穿墙/障碍；
- 双人同屏；
- 色弱纹理；
- 静态棋盘缓存；
- 本机最佳按模式区分。

## 5.3 2048

当前已具备：

- 无效果输入不会堵塞队列；
- win/pause/reset 边界清理；
- typed slot load；
- attempt 恢复；
- slot schema 4；
- owner token；
- quarantine ACK；
- 终局棋盘恢复。

优先修复：

1. stale takeover snapshot；
2. owner epoch；
3. terminal score recovery；
4. version 4 `won_announced`；
5. autosave debounce；
6. 多实例/多槽策略；
7. terminal slot 显式“查看/新开/删除”；
8. receipt 完整绑定。

后续功能：

- 撤销；
- 多存档槽；
- 棋盘尺寸；
- RNG 注入；
- 最高分详情页。

## 5.4 推箱子

已保持：

- 同关不重复累计；
- 跳关进入练习；
- 全部完成才完整通关；
- 0 分合法完整通关；
- campaign/practice 分离；
- progress schema 和 generation。

后续：

- 正式关卡选择；
- 最少移动和推动；
- 是否使用撤销；
- 星级；
- 静态死角和死锁检测；
- 提示；
- 地图闭合与可达性；
- XSB 导入；
- 编辑器；
- 固定逻辑窗口。

## 5.5 祖玛

已保持：

- 多 pending reactions；
- swept projectile；
- path bisect；
- 长帧余量；
- 临界救场顺序；
- progress schema/generation。

后续：

- 显式 reaction FSM；
- 多重反应属性测试；
- `incoming` 改 deque；
- RNG 注入；
- 训练/选关；
- 色弱符号；
- 原创道具；
- 轨道编辑器。

---

## 6. 本轮明确不建议做的事情

不建议把项目扩展成：

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
- 复杂权限平台。

Flask 继续只用于：

- 教学；
- 调试；
- 本机成绩 API 示例。

也不建议：

- 为了形式统一强制把合理分层的 unittest/regression/stress 全部改写为 pytest；
- 用随机 profile ID替代 HTTP 按玩家名生成的稳定身份；
- 删除稳定 inode lock 文件而引入 split-brain；
- 在当前数据层尚未封板前继续增加更多 journal/receipt 类型。

---

## 7. 推荐的增量目标架构

保留现有主架构，新增两个边界层：

```text
DataMaintenanceService
  ├── application maintenance lock
  ├── consistent SQLite snapshot
  ├── active score/state journal snapshot
  ├── archive manifest/checksum
  ├── semantic preview
  └── conflict-aware import

LocalStateService
  ├── pure semantic validation
  ├── previous journal preservation
  ├── monotonic baseline
  ├── full-row state hash
  └── owner epoch / slot CAS
```

### 7.1 Archive v2 建议

```text
archive_version
schema_version
app_version
exported_at
manifest_hash
tables
score_pending
state_pending
recovery_evidence
```

### 7.2 2048 ownership 建议

```text
owner_token
owner_epoch
slot_revision
value_hash
expected_previous_owner
expected_previous_revision
owner_status
```

---

## 8. 完整优化任务清单

### 优先级定义

- **P0**：可能覆盖数据库、遗漏备份、静默丢记录或覆盖较新存档；发布阻断。
- **P1**：数据一致性、自恢复、发布工程和跨平台基础。
- **P2**：维护性、UI、性能与可访问性。
- **P3**：玩法内容与桌面发行。
- **S/M/L/XL**：相对工作量。

---

## 8.1 P0：备份、导入和 autosave 数据安全

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG11-P0-01 | 导出路径防撞 | path guard | 永远不能覆盖 DB/WAL/SHM/journal | S |
| CG11-P0-02 | 默认禁止覆盖输出 | `--force` policy | 已有普通文件需明确确认 | S |
| CG11-P0-03 | Active pending 纳入导出 | archive v2 | score/state pending 均可恢复 | L |
| CG11-P0-04 | 导出前 flush/一致性策略 | maintenance service | archive 明确 committed/pending 状态 | L |
| CG11-P0-05 | 数据库一致快照 | SQLite backup/read transaction | 多表来自同一时点 | M |
| CG11-P0-06 | Attempt surrogate ID 重映射 | importer | 非空目标不因 id 冲突漏记录 | M |
| CG11-P0-07 | Alternate unique 冲突检测 | preview/import | request_id/source_key 冲突明确报告 | M |
| CG11-P0-08 | 移除 raw `INSERT OR IGNORE` | strict importer | 没有静默 skip | M |
| CG11-P0-09 | Import 只补缺失 baseline | seed-missing API | 不降低既有 receipt revision | M |
| CG11-P0-10 | Baseline monotonic upsert | store policy | direct/import/migration 均不能降级 winner | L |
| CG11-P0-11 | Baseline cutoff 语义 | apply policy | 旧 latest-value journal 按 occurred_at 淘汰 | M |
| CG11-P0-12 | Pending journal high-water | migration/bootstrap | 时钟异常后旧值不能覆盖新 baseline | M |
| CG11-P0-13 | State journal previous-value rollback | journal API | 新 operation 永久失败时恢复旧 pending | L |
| CG11-P0-14 | Pure state semantic validator | shared validation | 非法 operation 不先替换有效 journal | L |
| CG11-P0-15 | 2048 takeover CAS | owner/revision/hash check | K 不能用旧快照覆盖新棋盘 | L |
| CG11-P0-16 | 2048 owner epoch | slot schema 5 | 被撤销 owner 不能自动复活 | L |
| CG11-P0-17 | 2048 terminal score recovery | restore controller | 未确认终局分自动幂等补交 | M |
| CG11-P0-18 | 数据 CLI 与游戏维护锁 | application lock | 运行中不能无协调 import/export | L |
| CG11-P0-19 | P0 故障/升级 fixtures | tests | 每个 P0 有确定性回归 | L |
| CG11-P0-20 | Release gate | required job | 任一 P0 失败禁止发布 | S |

---

## 8.2 P1：数据可靠性、迁移和正式发行基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG11-P1-01 | Archive 总大小限制 | parser guard | 巨大 JSON 不 OOM | M |
| CG11-P1-02 | Archive 深度/节点/字符串限制 | safe parser | 极端嵌套可控失败 | M |
| CG11-P1-03 | 拒绝 NaN/Infinity | strict JSON | preview/import 行为一致 | S |
| CG11-P1-04 | Archive manifest/hash | archive v2 | 传输损坏可检测 | M |
| CG11-P1-05 | Archive schema compatibility | version policy | future archive 不盲目导入 | S |
| CG11-P1-06 | 每表语义 validator | importer | UUID、时间、状态、JSON 均校验 | L |
| CG11-P1-07 | Progress/slot game schema 校验 | importer | 导入后不再二次隔离 | L |
| CG11-P1-08 | Recovery evidence 语义 | docs/import | 明确证据与可恢复数据差异 | S |
| CG11-P1-09 | Recovery 文件安全恢复 | quarantine restore | 不直接覆盖 active journal | L |
| CG11-P1-10 | Export 流式化 | streaming writer | 大历史内存有界 | L |
| CG11-P1-11 | Export 总 recovery 配额 | quota | 多个 8 MiB 文件不会无限增长 | M |
| CG11-P1-12 | Export 父目录 fsync | durability | archive rename 后目录同步 | S |
| CG11-P1-13 | Export 隐私清单 | manifest/docs | 昵称和绝对路径风险明确 | S |
| CG11-P1-14 | Import 冲突报告 | machine result | exact/semantic/alternate 冲突分开 | M |
| CG11-P1-15 | Import dry-run 与 apply 一致 | shared planner | preview 与实际结果不漂移 | L |
| CG11-P1-16 | Import 自动恢复测试 | release drill | 导入失败后原 DB 不变 | M |
| CG11-P1-17 | Direct state API 内部化 | API boundary | 正常写入只走 operation | M |
| CG11-P1-18 | Full-row slot value hash | receipt schema | ruleset/state version 也受保护 | M |
| CG11-P1-19 | State clock 扫描 pending high-water | bootstrap | clock 丢失后新操作不被旧 pending 压制 | M |
| CG11-P1-20 | Receipt rebuild 保留旧 high-water | migration | malformed table 修复不降级顺序 | M |
| CG11-P1-21 | Permanent state failure quarantine | recovery policy | 用户唯一副本不无证据删除 | M |
| CG11-P1-22 | Receipt read 两阶段事务 | repository | 正常状态查询不取得写锁 | M |
| CG11-P1-23 | 启动 outbox 扫描后台化 | startup controller | 大 pending 目录不延迟首帧 | L |
| CG11-P1-24 | State quarantine 管理 | recovery UI/CLI | 可查看、导出、清理 | L |
| CG11-P1-25 | Merge receipt pending 保护扩展 | maintenance | 全部 active components 不被过早清理 | M |
| CG11-P1-26 | Merge receipt 清理测试 | tests | 365 天边界可验证 | S |
| CG11-P1-27 | 2048 v4 won-announced 修复 | slot restore | crash 后仍显示达成 overlay | S |
| CG11-P1-28 | 2048 owner takeover 重新读取 | controller | 接管前使用最新 slot | M |
| CG11-P1-29 | 2048 released-owner claim 协议 | owner lifecycle | claim 必须引用前一 epoch | M |
| CG11-P1-30 | 2048 terminal slot 明确策略 | ADR/UI | 查看、删除、新开语义清楚 | M |
| CG11-P1-31 | 2048 多实例策略 | single-instance/multislot | 不静默共用一槽 | L |
| CG11-P1-32 | 2048 autosave debounce | coalescing | 降低 fsync，退出仍 flush | M |
| CG11-P1-33 | 2048 score/slot crash matrix | tests | 所有语句间 crash 均可恢复 | L |
| CG11-P1-34 | Release 每阶段 timeout | tests.release | 死锁有界失败 | S |
| CG11-P1-35 | Wheel build/install smoke | release profile | 全新 venv 可启动/import | M |
| CG11-P1-36 | Current head CI 证据 | workflow | 每次 release 记录 run URL | S |
| CG11-P1-37 | Main branch protection | repo settings | required checks 开启 | S |
| CG11-P1-38 | Actions 固定 SHA | workflow policy | 减少供应链漂移 | M |
| CG11-P1-39 | Transitive dependency lock | lock/constraints | 三平台构建可复现 | L |
| CG11-P1-40 | Dependency manifest/SBOM | release artifact | 发布依赖可审计 | M |
| CG11-P1-41 | Dependency audit gate | CI | 高危漏洞阻止发布 | S |
| CG11-P1-42 | LICENSE 权利核对 | owner checklist | 可据此选择许可证 | M |
| CG11-P1-43 | NOTICE/素材清单 | inventory | 字体、图形、音效来源明确 | M |
| CG11-P1-44 | 游戏名称/商标检查 | release checklist | 正式发行风险有记录 | M |
| CG11-P1-45 | SemVer/schema/ruleset 治理 | ADR | 每次兼容变化可追踪 | S |
| CG11-P1-46 | 统一 release result | JUnit/JSON | 三层测试结果可诊断 | M |
| CG11-P1-47 | 属性测试评估 | ADR | 只用于适合的不变量 | S |
| CG11-P1-48 | State/import 属性测试 | model tests | CAS、merge、import 不变量 | L |

---

## 8.3 P2：维护性、桌面体验与可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG11-P2-01 | Data management GUI | desktop page | status/export/import/recovery 可视化 | XL |
| CG11-P2-02 | 本机档案列表页 | profile UI | 新建、切换、重命名、查看进度 | L |
| CG11-P2-03 | Export-before-delete | profile flow | 删除前可恢复 | L |
| CG11-P2-04 | Profile merge UI | local tool | 冲突预览、可回滚 | XL |
| CG11-P2-05 | Duplicate-name UX | profile list | 同名档案可区分 | M |
| CG11-P2-06 | ProfileController 完整封装 | controller | launcher 不散落管理 Future | L |
| CG11-P2-07 | GameState Enum | state model | 无散落魔法字符串 | L |
| CG11-P2-08 | AttemptSaveController | common controller | BaseGame/2048 控制统一 | XL |
| CG11-P2-09 | LocalStateController | common layer | settings/progress/slot 状态统一 | L |
| CG11-P2-10 | ProgressController | common layer | generation、merge、event 统一 | L |
| CG11-P2-11 | SaveSlotController | common layer | loading/retry/ownership 统一 | L |
| CG11-P2-12 | InputManager | action map | 五款游戏统一输入 | L |
| CG11-P2-13 | 完整 IME 控件 | widget | 组合、光标、选择、退格稳定 | M |
| CG11-P2-14 | 键位重映射 | settings UI | 冲突检测、恢复默认 | L |
| CG11-P2-15 | 键盘菜单导航 | focus model | 不用鼠标可操作 | L |
| CG11-P2-16 | 手柄支持 | controller layer | launcher/五款游戏可用 | L |
| CG11-P2-17 | 音频系统 | BGM/SFX | 无设备不崩，音量持久 | L |
| CG11-P2-18 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG11-P2-19 | 高 DPI | DPI handling | 字体和图形清晰 | M |
| CG11-P2-20 | 字体 fallback | licensed font chain | 缺系统 CJK 字体仍可读 | M |
| CG11-P2-21 | 色弱符号 | patterns/shapes | 颜色不是唯一信息 | L |
| CG11-P2-22 | 高对比/降低动态 | accessibility | 脉冲、抖动可关闭 | L |
| CG11-P2-23 | Clock/RNG 注入 | deterministic services | seed+输入可重现 | L |
| CG11-P2-24 | 渐进抽取纯规则 Engine | rules modules | 核心测试无需 SDL | XL |
| CG11-P2-25 | Launcher 拆分 | app/state/render/data | `main()` 职责清晰 | L |
| CG11-P2-26 | 首页改为本机进度中心 | dashboard | best/recent/progress/continue | L |
| CG11-P2-27 | 静态 Surface 缓存 | profiler-driven | 有尺寸/主题失效 | L |
| CG11-P2-28 | Zuma reaction FSM | explicit model | 重叠反应可属性测试 | L |
| CG11-P2-29 | 可重复 benchmark CLI | benchmark | 带 OS、版本、seed | M |
| CG11-P2-30 | 30–60 分钟 soak | stability suite | 线程/FD/内存稳定 | M |
| CG11-P2-31 | 游戏内规则页 | controls/rules/version | ruleset 可查看 | M |
| CG11-P2-32 | 崩溃恢复页 | crash UI | 返回菜单并显示日志 | M |
| CG11-P2-33 | 设置页面 | settings UI | 窗口、音量、按键、辅助项 | L |
| CG11-P2-34 | 历史/legacy 浏览 | history UI | 当前规则和旧记录分开 | M |

---

## 8.4 P3：玩法内容与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG11-P3-01 | Tetris alias action edge | input polish | 同义键不重复立即移动 | S |
| CG11-P3-02 | Tetris 7-bag | comfort mode | 独立 ruleset | M |
| CG11-P3-03 | Tetris ghost/hold/lock delay | comfort features | 规则测试完整 | L |
| CG11-P3-04 | Snake 速度/穿墙/障碍 | local modes | 模式最佳分开 | L |
| CG11-P3-05 | Snake 双人同屏 | local multiplayer | 不依赖网络 | L |
| CG11-P3-06 | 2048 撤销 | undo model | slot/attempt 语义清楚 | L |
| CG11-P3-07 | 2048 多存档槽 | save UI | 可查看、继续、删除 | L |
| CG11-P3-08 | 2048 棋盘尺寸 | modes | ruleset 分离 | M |
| CG11-P3-09 | Sokoban 正式选关 | progress UI | practice/campaign 分开 | L |
| CG11-P3-10 | Sokoban 星级/最佳推动 | metrics | 规则明确 | M |
| CG11-P3-11 | Sokoban 死锁检测/提示 | analysis | 提示可关闭 | XL |
| CG11-P3-12 | Sokoban 编辑器 | XSB import/export | 地图验证和预览 | L |
| CG11-P3-13 | Zuma 训练/选关 | practice mode | 不混入完整通关 | L |
| CG11-P3-14 | Zuma 色弱辅助 | symbols | 球色可独立辨认 | M |
| CG11-P3-15 | Zuma 原创道具/轨道 | content | 每项有确定性测试 | XL |
| CG11-P3-16 | Zuma 轨道编辑器 | path tool | 合法、版本化、可预览 | L |
| CG11-P3-17 | 本机成就 | local achievements | 无账号、无遥测 | L |
| CG11-P3-18 | 离线每日挑战 | date seed | 完全离线 | L |
| CG11-P3-19 | 本地 replay | command log | 用于复盘和调试 | L |
| CG11-P3-20 | 中文/英文 | localization | 长文本布局测试 | L |
| CG11-P3-21 | Windows 桌面包 | installer/portable | 无 Python 可运行 | XL |
| CG11-P3-22 | macOS app bundle | package | 数据目录和关闭 smoke | XL |
| CG11-P3-23 | Linux package | AppImage/等效 | XDG 路径正确 | L |
| CG11-P3-24 | 自动发布 | tagged builds | smoke 通过才发布 | L |
| CG11-P3-25 | 签名与校验和 | release integrity | 下载可验证 | M |
| CG11-P3-26 | 截图/GIF/项目主页 | showcase | README 首屏展示玩法 | M |
| CG11-P3-27 | 谨慎新增游戏 | template/contracts | 同时交付规则、数据、输入、测试 | XL |

---

## 9. 必须新增的关键测试

### 9.1 Data export/import

```text
test_export_cannot_replace_database
test_export_cannot_replace_wal_or_shm
test_export_existing_file_requires_force
test_export_includes_active_score_pending
test_export_includes_active_state_pending
test_export_uses_one_consistent_database_snapshot
test_import_nonempty_database_remaps_attempt_ids
test_import_detects_request_id_conflict
test_import_detects_source_key_conflict
test_preview_and_apply_have_identical_plan
test_import_does_not_lower_existing_state_receipt
test_import_while_launcher_active_is_rejected_or_coordinated
test_archive_nan_infinity_and_deep_nesting_are_rejected
```

### 9.2 Baseline/order

```text
test_high_revision_old_journal_cannot_beat_newer_baseline_time
test_direct_baseline_never_lowers_existing_receipt
test_receipt_rebuild_preserves_old_high_water
test_missing_clock_scans_pending_revision_high_water
```

### 9.3 State permanent failure

```text
test_invalid_new_profile_rename_does_not_destroy_valid_pending
test_slot_in_use_does_not_destroy_previous_pending_autosave
test_permanent_state_failure_is_quarantined_with_evidence
test_previous_journal_is_restored_after_rejected_new_operation
```

### 9.4 2048 ownership and crash recovery

```text
test_takeover_reloads_latest_slot_before_commit
test_takeover_fails_when_slot_revision_changed
test_revoked_owner_cannot_reclaim_after_new_owner_release
test_owner_epoch_increments_on_claim_and_takeover
test_restore_gameover_with_unconfirmed_score_resubmits
test_restore_won_with_unconfirmed_score_resubmits
test_version4_won_announced_false_restores_win_overlay
test_terminal_score_replay_is_idempotent
```

### 9.5 Release engineering

```text
test_release_stage_timeout_is_reported_in_junit
test_release_profile_builds_and_installs_wheel
test_wheel_uses_user_data_directory
test_constraints_resolve_on_all_supported_platforms
```

---

## 10. 性能与稳定性门槛

1. pygame 主线程不得执行：
   - SQLite；
   - score/state journal scan；
   - data archive I/O；
   - OS 文件锁等待；
   - receipt repair；
   - profile migration；
2. `submit_score_async()` p99 ≤2 ms；
3. state write enqueue p99 ≤2 ms；
4. state status getter p99 ≤0.2 ms；
5. 导出 100,000 attempts 时内存有界；
6. 导入非空数据库不因 surrogate ID 丢记录；
7. 32 进程同 state key 最终值符合 ordering/merge policy；
8. 接管期间 owner 继续移动，不覆盖较新 slot；
9. 终局 crash 后 score 与 slot 最终一致；
10. 正常游戏目标 60 FPS，基准机 p95 ≤16.7 ms；
11. 100 次游戏切换：
    - 线程回基线；
    - FD 不增长；
    - Surface/内存稳定；
12. 30–60 分钟：
    - pending 最终 committed/superseded/recovery/quarantined；
    - receipt 表增长受控；
    - 不无限重试；
13. 满盘、只读、坏 DB、坏 archive、坏 slot、坏 progress：
    - 游戏仍可玩；
    - 最后一个有效副本不被静默删除；
    - 有恢复提示。

---

## 11. 稳定版本质量门禁

正式稳定版本至少满足：

- 所有 P0 关闭；
- Export 永远不能覆盖当前数据库；
- Archive 包含或明确 flush 所有 active pending；
- 非空数据库合并导入无静默漏记录；
- Baseline receipt 不会降级 ordering；
- 新 state 永久失败不会删除旧有效 pending；
- 2048 ownership 使用 revision/hash CAS；
- 终局 slot 能补交最终成绩；
- CI 当前 head 三平台通过；
- main required checks 开启；
- release profile 包含 wheel smoke；
- core-only、三层测试均可单独运行；
- coverage、lint、依赖审计；
- LICENSE/NOTICE 在权利确认后加入；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代：

- 非空数据库合并；
- crash-before/after-journal；
- baseline + 高 revision journal；
- 多实例接管；
- 真实备份恢复演练。

---

## 12. 推荐实施顺序

### M0：关闭备份与恢复发布阻断

1. Export 路径防撞；
2. Active pending archive；
3. Import ID/conflict planner；
4. Baseline monotonic；
5. State permanent failure rollback；
6. 2048 takeover CAS；
7. Terminal score recovery；
8. P0 fixtures。

### M1：硬化本地数据工具

- archive v2；
- streaming snapshot；
- semantic preview；
- maintenance lock；
- recovery restore；
- data management UI；
- receipt full-row binding；
- pending high-water。

### M2：正式发行工程

- release timeout；
- wheel smoke；
- branch protection；
- transitive lock；
- dependency/SBOM；
- LICENSE 权利核对；
- recovery drill。

### M3：桌面体验

- 档案页；
- InputManager；
- 键位/手柄；
- 音频；
- DPI/字体；
- 可访问性；
- launcher 拆分；
- RNG/Clock；
- 纯规则 Engine；
- settings UI。

### M4：玩法与打包

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

本轮修改仍然是明显进步：

- 上一轮的 progress aggregate、receipt/business-row 和 v5 baseline 三个核心问题都有真实实现；
- 当前 head CI 已经成功；
- 测试分层和 HTTP identity 的设计理由成立；
- 2048 多实例问题已经从“完全无保护”进入“有 owner 协议”的阶段；
- 本机数据 CLI 为正式桌面产品补上了重要能力。

但数据 CLI 目前仍处于“功能可用、恢复语义尚未封板”的阶段，尤其：

```text
导出可以覆盖数据库
导出遗漏 active pending
非空数据库导入会因自增 ID 静默漏记录
导入可能降低 state receipt
```

这些问题比普通 UI 小瑕疵更优先。

推荐下一步：

> **先把数据导出/导入当作真正的恢复系统进行故障设计，再完成 baseline monotonic 和 2048 ownership CAS。**

完成这些工作后，存储层应进入封板阶段，不再继续增加新的持久化抽象；后续主要精力转向档案体验、可访问性、五款游戏本身和三平台桌面发行。
