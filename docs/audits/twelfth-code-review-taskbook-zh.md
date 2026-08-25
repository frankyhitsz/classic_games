# Classic Games Hub 第十二次代码审查与本地优先优化任务书

> 审查日期：2026-08-25
> 当前基线：`main` 分支 commit `ac45f0024c8e298a0cc2f3d2062050c718fc8e50`（`ac45f00`）
> 对比基线：上一轮审查 commit `8f1b7131002047225f8ad6152478e7d088aa5296`
> 增量提交：3 个，核心内容为数据恢复工具、state rollback、2048 ownership/终局恢复和 release gate
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 非目标：账号、云端排行、匹配、赛季、反作弊、强制联网与默认遥测

---

## 0. 执行摘要

本轮修复总体有效。上一轮提出的大部分发布阻断已经有实际实现和对应回归测试：

- 导出目标不能再指向数据库、WAL/SHM、pending 或 recovery 路径；
- 导出 archive v2 已包含 active score/state pending；
- 数据库表在同一 SQLite 读事务中导出；
- 合并导入时不再复用 attempts 等表的自增主键；
- `attempt_uuid / request_id / source_key` 等 alternate unique 冲突会在 preview 中报告；
- Import 只补缺失 baseline，不再无条件降低现有 receipt；
- Baseline 对旧 latest-value operation 增加发生时间判断；
- State operation 在发布 journal 前做纯语义验证；
- 永久失败的新 operation 会尝试隔离新值并恢复旧 pending；
- 2048 slot schema 5 已加入 owner epoch、owner/revision/value-hash CAS；
- 接管前会重新读取最新 slot；
- 恢复终局 slot 时会补交未确认的最终分数；
- version 4/5 `won_announced` 能正确恢复；
- 2048 autosave 增加 150 ms debounce；
- Tetris 同义键不会重复产生立即移动；
- Release profile 增加逐阶段 timeout、wheel 安装 smoke、SBOM 和依赖审计；
- 当前 head 的 GitHub Actions CI #29 已完成且为 success。

因此，项目的本地优先主架构已经基本定型：

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
    └── 仅作为本机成绩 API 调试适配器
```

不建议再次推倒重构，也不建议把它改造成联网竞技平台。

### 当前最重要的结论

当前仍有三组发布阻断风险：

1. **Import 的数据库写入、pending 恢复和 recovery evidence 恢复不是一个可恢复事务。**数据库可以已经提交，但后续文件恢复失败，命令整体报错并留下部分应用状态。
2. **Archive pending 的验证仍有 apply-time 缺口。**`attempt_count` 没有上限，state journal schema 3 的 `logical_revision` 没有 SQLite int64 上界；preview 可通过，apply 可能陷入海量 fsync 循环或在数据库已提交后失败。
3. **State rejected-operation rollback 仍有崩溃窗口。**被拒绝的新 journal 移入 quarantine 后、旧 journal 恢复前若进程崩溃，旧值只存在于不会被扫描的 `.restore` 临时文件。

此外，数据导出仍需解决：

- 活跃应用内尚未进入 journal 的 operation 无法被 CLI 快照看到；
- 达到 journal 文件/总大小限制时，导出可能不完整；
- state scanner 在累计体积越界后会把后续合法文件移入 quarantine；
- 导出/导入目前还不是真正意义上的“完整备份与还原”，更接近严格合并工具。

本轮判断：

> **游戏规则和普通保存主流程已经明显收敛；下一步应封板数据协议，集中完善备份/导入的原子性、pending 边界验证和 rollback 恢复。**

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的检查

- 锁定当前 `main` 最新提交；
- 对比上一轮基线到当前 head 的全部修改；
- 阅读：
  - 五款游戏；
  - `client/common/ui.py`；
  - `client/launcher.py`；
  - `client/profile_controller.py`；
  - `game_service/local_backend.py`；
  - `game_service/store.py`；
  - `game_service/data_cli.py`；
  - `game_service/maintenance.py`；
  - 可选 Flask 与 HTTP client；
  - storage v2–v9 tests；
  - regression、stress、release runner；
  - CI workflow、README、NOTICE、约束文件和治理文档；
- 逐项复核上一轮任务；
- 组合审查：
  - SQLite transaction + score/state pending；
  - Import commit + filesystem restore；
  - journal replace + permanent rejection + crash；
  - active application + maintenance lock；
  - archive preview + apply；
  - 2048 ownership、terminal score 和 autosave；
- 核对当前 GitHub Actions 与分支保护状态。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题的主要路径和关键边界已闭环 |
| **基本到位** | 原缺陷关闭，但相邻故障组合仍有缺口 |
| **代码路径确定** | 从当前控制流可以确定 |
| **状态模型确定** | 通过等价状态顺序可得到错误最终状态 |
| **极端边界** | 需要磁盘满、崩溃、超大 archive 或跨进程组合 |
| **待真机验证** | 需要三平台桌面构建或真实 GUI 验证 |
| **产品任务** | 非 Bug，但符合本地单机产品方向 |

### 1.3 限制

本次环境未本地安装 pygame/Flask，也未把源码归档下载到执行容器，因此没有独立重跑完整测试。

可以确认的是：

- 当前 GitHub Actions CI #29 为 completed/success；
- 本报告的具体问题来自当前代码控制流和数据协议组合审查；
- 仓库记录的测试数量和性能值属于项目自测，不表述为本次独立测量。

---

## 2. 上一轮修复验收矩阵

| 上一轮问题 | 当前状态 | 判断 |
|---|---|---|
| 导出可覆盖数据库 | **修复到位** | DB/WAL/SHM/pending/recovery 路径被禁止 |
| 普通输出文件无覆盖确认 | **修复到位** | 默认拒绝，需 `--force` |
| Active pending 未进入 archive | **主体到位** | archive v2 包含 score/state pending；异常规模完整性仍有缺口 |
| 数据库导出不是同一快照 | **修复到位** | 同一 SQLite read transaction |
| Attempts 自增 ID 冲突 | **修复到位** | 导入时省略 surrogate ID |
| Alternate unique 冲突被忽略 | **修复到位** | attempt_uuid/request_id/source_key 均规划 |
| Import 无条件降低 receipt | **修复到位** | `missing_only=True` |
| Baseline 高 revision 旧 journal | **修复到位** | baseline 也按 occurred_at 阻止旧 latest-value |
| 新永久失败覆盖旧 pending | **基本到位** | pure validation + previous restore；rollback crash window仍在 |
| 2048 stale takeover | **修复到位** | 重新读取 + owner/revision/hash CAS |
| 旧 owner 复活 | **修复到位** | owner epoch |
| 2048 终局分未补交 | **修复到位** | restore 后按 confirmed_score 补交 |
| v4 `won_announced` 丢失 | **修复到位** | version 2–5 均恢复 |
| Autosave 过度写盘 | **基本到位** | 150 ms debounce；连续操作最大延迟尚无上限 |
| Tetris alias 双移动 | **修复到位** | 按逻辑 action edge |
| Release 无 timeout | **修复到位** | 每阶段单独 timeout |
| Release 无 wheel smoke | **修复到位** | 隔离 venv 安装 wheel |
| Actions 未固定 | **修复到位** | 当前 workflow 使用 commit SHA |
| 直接依赖以外未固定 | **明显改善** | release constraints 包含依赖闭包；尚无 hash/平台 lock |
| Current head CI | **修复到位** | CI #29 success |
| Branch protection | **未完成** | `main` 仍未保护 |
| LICENSE | **未完成** | NOTICE 已有，许可证需所有者决定 |

---

## 3. 当前发布阻断问题

## 3.1 CG12-F01：Import 在数据库提交后才恢复 journal 和 recovery evidence

- **级别**：P0
- **位置**：`game_service/data_cli.py::import_data`

当前顺序：

```text
建立 DB backup
→ BEGIN IMMEDIATE
→ 插入 tables
→ seed baseline
→ COMMIT
→ 恢复 pending score/state
→ 恢复 recovery evidence
```

如果后两步因以下原因失败：

- ENOSPC；
- 只读目录；
- 权限；
- state journal 冲突；
- evidence 路径创建失败；
- 文件系统 I/O 错误；

数据库已经变化，命令却整体返回失败。Pending 和 evidence 还可能只恢复了一部分。

### 风险

- 用户无法仅凭返回值判断哪些数据已经应用；
- 自动重试可能再次创建 evidence 副本；
- 数据库 backup 存在，但错误结果没有形成自动回滚/继续状态；
- “导入失败”不再等于“目标完全未变化”。

### 修复要求

使用 import transaction journal：

```text
PREPARED
DB_APPLIED
PENDING_APPLIED
EVIDENCE_APPLIED
COMPLETED
```

推荐流程：

1. 完整规划和预验证；
2. 将 pending/evidence 写入 staging；
3. 创建 DB backup；
4. 写 DB；
5. 原子发布 staged files；
6. 标记 completed；
7. 任一阶段失败可继续或自动恢复 backup。

---

## 3.2 CG12-F02：Score pending 的 `attempt_count` 没有上限

- **级别**：P0
- **位置**：
  - `PendingSaveEnvelope.parse`
  - `data_cli._restore_pending`

当前只验证：

```text
attempt_count >= 0
```

恢复时：

```python
for _ in range(envelope.attempt_count - current.attempt_count):
    increment_attempt()
```

一个损坏或人工构造的 archive 可设置极大值，导致：

- 极长循环；
- 每轮写文件和 fsync；
- 磁盘磨损；
- Import 无法在合理时间结束。

### 修复要求

- 设置合理上限，例如 1,000 或 10,000；
- 不通过循环恢复；
- 增加 `set_attempt_count_max(request_id, value)` 一次原子写；
- Preview 与 apply 共用同一上限；
- 旧异常值进入 quarantine/报告。

---

## 3.3 CG12-F03：State journal schema 3 缺少 int64 与时间范围校验

- **级别**：P0
- **位置**：
  - `PersistentStateOutbox._parse`
  - `data_cli._plan_import`
  - `_restore_pending`

当前 schema 3 parser 检查：

```text
logical_revision >= 0
updated_at finite
```

但没有检查：

```text
logical_revision <= 2^63-1
updated_at 在仓储可接受范围内
```

因此：

```text
preview 解析成功
→ DB rows 已提交
→ pending restore 调用 put()
→ _operation() 拒绝超界 revision
→ import 在提交后失败
```

### 修复要求

- Journal parser 与 store validator使用同一数值范围；
- Archive planner 必须调用最终 apply 使用的完整规范化函数；
- `updated_at`、component count、operation ID、ruleset 统一验证；
- Preview 通过必须保证 restore 阶段不会再因纯输入语义失败。

---

## 3.4 CG12-F04：Rollback `.restore` 临时文件不会自动恢复

- **级别**：P0
- **位置**：`PersistentStateOutbox.reject_and_restore_if_current`

当前顺序：

```text
写并 fsync previous 到隐藏 .restore
→ 把被拒绝的新 journal 移入 quarantine
→ os.replace(.restore, canonical target)
```

若进程在第二步和第三步之间崩溃：

- 旧 pending 仍有字节副本；
- 但只存在于 `.restore`；
- 正常 scanner 只扫描 `.json`；
- 用户的旧 pending 不会自动补写。

### 修复选择

更稳妥的协议：

```text
先复制/硬链接 rejected evidence 到 quarantine
→ canonical target 仍不动
→ 原子 replace canonical 为 previous
```

或者：

- 为 `.restore` 建立明确 recovery scanner；
- 启动时识别并完成/回滚未完成的 reject transaction；
- 加 transaction marker 与故障注入测试。

---

## 4. 高优先级剩余问题

## 4.1 CG12-F05：维护锁不代表应用已静止

当前 LocalBackend 只在单次 score/state 写入期间取得 shared maintenance lock。

应用可以：

- 已生成 operation；
- operation 尚在 worker 队列；
- 尚未进入 journal；
- 此时 CLI 成功取得 exclusive lock。

Archive 因此是“已持久化状态快照”，并不保证包含应用内存里已发生但尚未发布的操作。

### 建议

- 文档明确区分；
- CLI 默认要求关闭运行中的游戏；
- 或新增进程级 maintenance coordination/IPC flush；
- 桌面数据管理 UI 应在同一进程完成 flush 后导出。

## 4.2 CG12-F06：Import 在取得 maintenance lock 前初始化并可能迁移数据库

`import_data()` 在进入 exclusive lock 前执行：

```python
store = LocalGameStore(database)
```

这一步可能：

- 初始化；
- schema migration；
- repair；
- legacy import；
- 创建 backup。

因此导入锁并没有覆盖整个可能修改数据库的过程。

### 修复

先获取 maintenance lock，再初始化 store、preview、backup 与 apply。

## 4.3 CG12-F07：Active pending 达到限制时，Archive 可能不完整

- Score outbox 超过文件上限时只检查前一部分；
- State outbox 批量扫描也有文件与总大小预算；
- Export 没有把“被截断”作为失败；
- Manifest 只有实际纳入数量，没有 `complete/omitted`。

### 修复

Archive manifest 增加：

```text
complete
source_count
included_count
omitted_count
omitted_reason
```

完整备份模式遇到遗漏必须失败，除非显式 `--allow-partial`.

## 4.4 CG12-F08：State scanner 会把累计体积越界后的合法文件隔离

`list_entries()` 累加 `total_bytes`。超过总量阈值后抛出 `state_journal_too_large`，通用异常处理把当前文件移动到 quarantine。

这意味着“目录总体过大”被误解释为“该文件损坏”。

### 修复

- 单文件越界才隔离单文件；
- 总量越界只停止扫描并报告 incomplete；
- Export/maintenance 不得因为配额而改变合法数据归属。

## 4.5 CG12-F09：Data export 会升级或隔离源 journal

Export 通过正式 `list_envelopes/list_entries` 读取 active pending；这些方法会：

- 升级旧 schema；
- 改 canonical 文件名；
- 隔离坏文件；
- 更新 notice。

备份操作因此不是只读操作。

### 修复

提供只读 snapshot API：

```text
snapshot_entries(read_only=True)
```

升级/隔离属于 maintenance，不应隐式发生在 export 中。

## 4.6 CG12-F10：Pending planner 没有完整模拟目标数据库冲突

当前 planner 对 active pending 主要比较目标 pending 文件，没有完整检查：

- score request ID 与目标 attempts/save receipt；
- state operation ID 与目标 state receipts；
- state operation 对目标业务行的 CAS/ownership；
- pending state 所需 profile 是否会存在。

结果可能是：

```text
preview ok
→ restore pending
→ 后台重放时永久冲突或 quarantine
```

### 修复

在 scratch DB 中实际调用：

```text
record_mutation
apply_state_operation
```

但保留 pending 语义，不提交到真实库。

## 4.7 CG12-F11：恢复工具仍缺少“替换式还原”

当前 import 是严格合并：

- 新记录插入；
- natural key 不同则冲突；
- 不能用旧 archive 恢复已变更的 setting/slot；
- 不能回滚到历史 backup。

### 建议增加两种明确模式

```text
merge-import
restore-replace
```

`restore-replace` 必须：

- 先备份当前 DB/journals；
- 关闭应用；
- 验证 archive；
- 原子恢复；
- 可一键回滚。

## 4.8 CG12-F12：Import preview 与 apply 仍不是同一执行计划

虽然数据表使用同一 planner，但：

- pending restore 不完全模拟；
- evidence filesystem 写入不模拟；
- archive 与目标在 preview 后可能变化；
- maintenance lock 在 preview 与正式 import 命令之间并不持续。

### 修复

生成带 hash 的 plan artifact：

```text
target DB fingerprint
target pending fingerprint
archive hash
planned operations
```

Apply 时重新验证 fingerprint。

## 4.9 CG12-F13：Recovery evidence 路径需进一步防穿越

`_safe_evidence_relative()` 拒绝绝对路径和 `..`，但还应处理：

- Windows drive-relative path，如 `C:foo`;
- ADS/冒号；
- Windows reserved names；
- 预先存在的 symlink parent；
- 最终目标 symlink。

### 修复

- 拒绝 `path.drive`;
- 使用 POSIX archive relative path；
- 使用 `dir_fd`/`O_NOFOLLOW` 或逐层 no-symlink 校验；
- 所有最终路径必须 resolve 在 import root 内。

## 4.10 CG12-F14：Recovery omissions 没有形成完整性状态

Recovery evidence 超过数量、单文件或总大小时会写入：

```text
omitted: ...
```

Import 会忽略这些 marker。

### 修复

- Manifest 增加 `recovery_complete`;
- UI/CLI 显示遗漏数量；
- 完整恢复演练不能把含 omissions 的 archive 标记为完整。

## 4.11 CG12-F15：Archive schema compatibility 尚不严格

Archive 包含 `schema_version`，但当前主要按 `archive_version` 判断。

未来较新 schema 若沿用 archive v2 且字段仍兼容，旧程序可能错误接受语义已经变化的数据。

### 修复

- 增加 `min_reader_version/max_reader_version`;
- 较新 schema 默认拒绝；
- 通过显式 compatibility adapter 导入。

## 4.12 CG12-F16：Archive import 缺少游戏专项 slot 校验

Scratch DB 可以验证通用 JSON、FK 和表约束，但不能证明：

- 2048 棋盘语义正确；
- owner token/epoch 一致；
- game_state 与棋盘一致；
- 将来其他游戏 slot 结构正确。

结果可能是 Import 成功，启动游戏后再隔离。

### 修复

为每个 slot schema提供：

```python
validate_save_slot(game_id, ruleset, payload)
```

Import、store 与 game 使用同一验证器。

## 4.13 CG12-F17：Archive 构造和解析内存峰值偏高

Export 同时持有：

- 所有 table rows；
- pending；
- recovery base64；
- canonical JSON bytes。

Import 同样一次性读取和解析最多 128 MiB archive。

### 修复

- 流式 JSON 或 zip container；
- 表分段；
- 单独文件 manifest；
- 增量 hash；
- 明确内存预算。

## 4.14 CG12-F18：Rollback 本身失败时旧 pending 只存在内存

即使不崩溃，若：

- quarantine 目录不可写；
- restore temp 无法发布；
- 磁盘满；

`reject_and_restore_if_current()` 返回 false。旧 previous operation 没有另一个持久副本。

### 修复

- previous operation 保留到 `_non_durable_state`;
- 退出保护必须看到它；
- 释放空间后自动恢复；
- rejected operation 与 previous 分别保留证据。

## 4.15 CG12-F19：Maintenance 超时发生在 score spool 之前

`_save_mutation()` 在进入维护锁后才写 score spool。

若 exclusive maintenance 持续超过 300 秒：

- Future 抛异常；
- Backend 未建立 `_non_durable` score；
- 只有游戏对象还持有 payload。

### 修复

- 为 maintenance timeout 增加 score unexpected-failure recovery；
- 将 mutation 放入内存 non-durable queue；
- 或先建立 durable intent，再等待 DB maintenance gate。

## 4.16 CG12-F20：LocalBackend 关闭可能被维护锁等待拖延

Worker 可能正在等待最长 300 秒的 maintenance lock；`close()` 只等待 10 秒，但 ThreadPoolExecutor 工作线程仍可能阻止解释器真正结束。

### 修复

- 减少普通写入的 lock wait；
- 支持取消尚未进入 journal 的任务；
- close 时把 operation 转入 non-durable/持久 intent；
- 实测退出时间预算。

## 4.17 CG12-F21：2048 debounce 只有尾沿，没有最大保存间隔

每次移动结束都会把 due time 推迟 150 ms。连续快速操作时，保存可能持续延期。

### 修复

同时使用：

```text
quiet debounce = 150 ms
max dirty age = 1–2 s 或 N 次移动
```

暂停、terminal、返回、关闭立即 flush。

## 4.18 CG12-F22：2048 同时存在多个 save Future 时只轮询最后一个

`_slot_save_future` 是单槽。新的 autosave 会覆盖前一个 Future 引用。

底层 journal 能合并大部分 latest-value，但 UI 可能漏掉前一个请求的：

- non-durable failure；
- ownership conflict；
- recovery-required 状态。

### 修复

- 同一 key 只允许一个 in-flight；
- 新状态 coalesce 到 dirty buffer；
- 完成后提交最新状态；
- UI 观察 semantic key 的 LocalStateEvent，而非单个 Future。

## 4.19 CG12-F23：一个 profile 仍只有一个 2048 autosave

Owner 协议防止静默覆盖，但不能让两个合法长局并存。

产品选择：

- 每档案只允许一个活动 2048；
- 或每 attempt 自动槽；
- 或多存档槽。

不需要联网。

## 4.20 CG12-F24：当前数据状态页不能处理旧 schema

`status` 使用 `initialize=False` 并直接查询当前表。旧库缺表时更可能返回通用错误，而不是：

```text
schema version
migration needed
backup available
```

应建立只读 legacy inspector。

## 4.21 CG12-F25：Merge receipt 保护列表有上限

Maintenance 只保护前 1,000 个 active component ID。极端长期 pending aggregate 可能有更多 component。

虽然 progress merge 本身幂等，不太会丢业务值，但可能：

- 重复更新 version/time；
- status reconstruction 误判；
- 增加不必要重放。

## 4.22 CG12-F26：当前 CI 成功，但 main 仍无 required checks

当前 head CI #29 已成功，但 `main` 仍可绕过 workflow 直接推送。

正式 release 前应：

- 保护 main；
- 要求 release-gate、core-only、三平台 test；
- 禁止失败检查直接合并。

## 4.23 CG12-F27：Release lock 没有 hashes 和平台产物验证

`constraints-release.txt` 已包含依赖闭包，这是明显进步。

仍缺：

- `--require-hashes`;
- 各平台 wheel availability；
- Windows/macOS release profile；
- 构建依赖锁；
- 实际安装 manifest 与 SBOM 对比。

## 4.24 CG12-F28：Wheel smoke 未验证真实用户数据路径

当前 wheel smoke 验证：

- import；
- `classic-games-data --help`.

还应验证：

- console entry points；
- 默认 user data directory；
- 只读安装目录；
- 数据库首次创建；
- 无仓库 cwd 依赖。

## 4.25 CG12-F29：LICENSE 仍未确定

NOTICE 已明确：

- 当前没有打包图像、音频或字体；
- 运行时图形由 Python 绘制；
- 正式发行需核对名称/商标和代码权利。

仓库所有者仍需选择 LICENSE；不能由 AI 代为决定。

---

## 5. 分模块判断

## 5.1 数据 CLI

### 已有优点

- Archive v2；
- manifest hash；
- 路径防撞；
- SQLite snapshot；
- active pending；
- conflict preview；
- auto-ID remap；
- strict JSON 上限；
- maintenance lock；
- backup；
- recovery evidence 隔离恢复。

### 当前重点

- 跨 DB/filesystem 原子性；
- pending 边界；
- 完整性标记；
- restore-replace；
- symlink 安全；
- streaming；
- game-specific validation；
- 数据管理 GUI。

## 5.2 Local backend / journals

### 已有优点

- 真异步；
- score/state 双 journal；
- OS lock；
- logical revision；
- state receipts；
- progress component receipts；
- non-durable fallback；
- SaveEvent/LocalStateEvent；
- maintenance gate；
- status snapshot；
- 自动重试。

### 当前重点

- rollback crash transaction；
- maintenance timeout 前的 durable intent；
- state total-limit 不误 quarantine；
- export read-only snapshot；
- close cancellation；
- semantic-key in-flight coalescing。

## 5.3 SQLite store

### 已有优点

- schema v7；
- explicit migration；
- baseline receipt；
- full slot value hash；
- state receipt repair；
- progress merge；
- attempt idempotency；
- ruleset 隔离；
- legacy import；
- profile identity；
- row constraints和 triggers。

### 当前重点

- direct API/internal API 边界；
- archive compatibility；
- old-schema inspector；
- merge receipt维护；
- restore mode；
- import-specific state operations。

## 5.4 2048

### 已修复

- delayed input；
- win/pause/reset input boundary；
- attempt restore；
- load gate；
- corruption quarantine；
- terminal score补交；
- won overlay恢复；
- owner token/epoch；
- takeover CAS；
- autosave debounce；
- stale row-ID hint移除。

### 后续

- max dirty age；
- single in-flight save；
- multi-slot/single-instance产品决策；
- 撤销；
- RNG 注入；
- 棋盘尺寸；
- terminal result浏览。

## 5.5 Tetris

核心输入和长帧修复保持，alias action edge 已完善。

后续适合单机舒适性：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- RNG 注入；
- 规则页；
- 静态网格缓存。

继续保持“自定义辅助旋转”的诚实命名。

## 5.6 Snake

核心修复保持：

- 双转向队列；
- 长停顿保护；
- 食物后速度间隔；
- 尾格碰撞语义。

后续：

- 速度选择；
- 穿墙；
- 障碍；
- 双人同屏；
- RNG；
- 色弱纹理；
- 静态棋盘缓存。

## 5.7 Sokoban

核心修复保持：

- 重复计分；
- 跳关练习；
- 完整通关；
- 0 分通关；
- campaign/practice；
- progress schema/generation/merge。

后续：

- 正式关卡选择；
- 最少移动/推动；
- 撤销标记；
- 星级；
- 死锁检测；
- 提示；
- 地图闭合/可达/死角；
- XSB 导入；
- 编辑器；
- 固定逻辑窗口。

## 5.8 Zuma

核心修复保持：

- 多 pending reaction；
- swept collision；
- path bisect；
- 长帧余量；
- 临界救场；
- progress schema/generation/merge。

后续：

- reaction FSM；
- 属性测试；
- `incoming` deque；
- RNG；
- 训练/选关；
- 色弱符号；
- 原创道具；
- 轨道编辑器。

## 5.9 启动器与 UI

### 已改善

- ProfileController；
- IME；
- 档案加载门禁；
- 新建/切换/改名；
- local-first；
- recent 非竞争展示；
- 保存状态可见。

### 后续

- 明确档案列表页；
- 数据管理页；
- 全键盘/手柄；
- 设置页；
- launcher 拆分；
- 逻辑分辨率/DPI；
- 字体 fallback；
- 可访问性；
- 结构化崩溃页。

---

## 6. 明确非目标

本任务书不建议建设：

- 注册登录；
- 云端账号；
- 公网排行榜；
- 匹配；
- 赛季；
- 实时联机；
- 反作弊；
- 服务端权威 replay；
- 在线商城；
- 强制联网；
- 默认遥测；
- 复杂权限平台。

Flask 继续只用于：

- 教学；
- 调试；
- 本机成绩 API 示例。

也不建议：

- 因为测试入口不同就强制改写 unittest/regression/stress；
- 继续增加新的 journal/receipt 抽象；
- 在当前恢复工具未封板前增加云同步或多设备合并。

---

## 7. 推荐增量架构

### 7.1 Data maintenance transaction

```text
ImportPlan
    ├── target fingerprint
    ├── archive hash
    ├── DB operations
    ├── score journal operations
    ├── state journal operations
    ├── evidence operations
    └── phase journal

Phases:
PREPARED → DB_APPLIED → FILES_PUBLISHED → COMPLETED
```

### 7.2 Read-only archive snapshot

```text
DataSnapshotService
    ├── SQLite backup snapshot
    ├── read-only score pending snapshot
    ├── read-only state pending snapshot
    ├── completeness report
    └── streaming archive writer
```

### 7.3 State reject transaction

```text
rejected evidence copy
→ canonical previous restore
→ transaction marker complete
```

不得让 previous 只停留在未扫描的临时文件。

---

## 8. 完整优化任务清单

### 优先级

- **P0**：可能留下部分导入、使恢复工具挂死或让 pending 无法自动恢复。
- **P1**：数据完整性、发布工程和跨平台基础。
- **P2**：维护性、UI、性能与可访问性。
- **P3**：玩法内容和桌面发行。
- **S/M/L/XL**：相对工作量。

---

## 8.1 P0：恢复工具和 pending 安全

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG12-P0-01 | Import phase journal | resumable import | 任一阶段失败可继续或回滚 | XL |
| CG12-P0-02 | Pending/evidence staging | staging directories | DB commit 前完成文件预写 | L |
| CG12-P0-03 | Post-commit failure recovery | automatic rollback/resume | import error 不留下未知部分状态 | L |
| CG12-P0-04 | `attempt_count` 上限 | envelope schema | 异常值 preview 阶段拒绝 | S |
| CG12-P0-05 | 原子设置 attempt count | outbox API | 不按次数循环 fsync | M |
| CG12-P0-06 | State revision int64 上限 | journal parser | preview/apply一致 | S |
| CG12-P0-07 | State timestamp范围 | shared validator | poison journal 不进入 import | S |
| CG12-P0-08 | Pending planner/apply共用验证 | validation API | preview 成功后无纯输入失败 | M |
| CG12-P0-09 | Reject transaction marker | rollback protocol | crash 后可自动完成恢复 | L |
| CG12-P0-10 | `.restore` startup recovery | scanner | 隐藏 previous 自动回到 active path | M |
| CG12-P0-11 | Rollback failure non-durable fallback | backend | previous 最后一份不会丢 | M |
| CG12-P0-12 | P0 故障注入 | tests | ENOSPC、crash、huge values均覆盖 | L |

---

## 8.2 P1：数据完整性与正式发行基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG12-P1-01 | Maintenance lock 覆盖 store 初始化 | import flow | migration 在 exclusive lock 内 | M |
| CG12-P1-02 | Active app detection/flush | coordination | 完整备份不遗漏内存 operation | L |
| CG12-P1-03 | Archive 完整性字段 | manifest | complete/omitted 可机器读取 | S |
| CG12-P1-04 | Full export 遇截断即失败 | export policy | 不静默少备份 | M |
| CG12-P1-05 | State 总量越界不 quarantine | scanner fix | 合法文件保持 active | S |
| CG12-P1-06 | Score outbox 截断报告 | scanner result | source/included/omitted一致 | S |
| CG12-P1-07 | Read-only pending snapshot | snapshot API | export 不升级/隔离源文件 | L |
| CG12-P1-08 | Target DB receipt 冲突规划 | import planner | pending score冲突提前发现 | M |
| CG12-P1-09 | Target state receipt冲突规划 | import planner | state pending冲突提前发现 | M |
| CG12-P1-10 | Pending profile依赖规划 | import planner | 无 profile 的 state 不被误报可恢复 | S |
| CG12-P1-11 | Restore-replace 模式 | data CLI | 可从完整 backup 真正回滚 | XL |
| CG12-P1-12 | Import plan artifact | plan JSON | preview/apply共享 fingerprint | L |
| CG12-P1-13 | Archive schema compatibility | reader policy | 新 schema默认拒绝 | S |
| CG12-P1-14 | Archive app/ruleset manifest | metadata | 恢复语义可解释 | S |
| CG12-P1-15 | 游戏专项 slot validator | shared registry | import/store/game共用 | L |
| CG12-P1-16 | Recovery path drive/ADS过滤 | path validator | Windows路径不逃逸 | M |
| CG12-P1-17 | Recovery no-follow写入 | secure writer | symlink parent不能越界 | L |
| CG12-P1-18 | Recovery omissions报告 | import result | 用户知道证据不完整 | S |
| CG12-P1-19 | Archive streaming export | writer | 大历史内存有界 | L |
| CG12-P1-20 | Archive streaming import | parser | 128MiB不多份复制 | XL |
| CG12-P1-21 | Archive总资源预算 | quota | tables/pending/recovery整体受控 | M |
| CG12-P1-22 | Export 临时文件 UUID | writer | 崩溃旧 temp 不阻止下次导出 | S |
| CG12-P1-23 | Import recovery evidence可恢复语义 | docs/API | evidence与active数据明确分离 | M |
| CG12-P1-24 | Pending state logical_revision 上界测试 | tests | schema 1/2/3均覆盖 | S |
| CG12-P1-25 | Pending score created_at范围 | envelope | 恢复前可判定 | S |
| CG12-P1-26 | Permanent state error保留证据 | quarantine policy | 不无证据删除用户状态 | M |
| CG12-P1-27 | Score maintenance timeout recovery | backend callback | timeout 后进入 non-durable | M |
| CG12-P1-28 | Writer close cancellation | lifecycle | 退出不等待300秒 | L |
| CG12-P1-29 | 2048 max dirty age | autosave policy | 连续操作也定期保存 | S |
| CG12-P1-30 | 2048 single in-flight save | save controller | 不遗漏旧 Future错误 | M |
| CG12-P1-31 | 2048 activity-slot policy | ADR | 单实例/多槽选择明确 | S |
| CG12-P1-32 | Legacy DB status inspector | CLI | 不迁移也能展示旧库状态 | M |
| CG12-P1-33 | Merge receipt保护无硬上限误删 | maintenance | active components完整保护 | M |
| CG12-P1-34 | Data recovery GUI前置service | service API | GUI不直接操作SQLite | L |
| CG12-P1-35 | Current head required checks | repo settings | main不可绕过CI | S |
| CG12-P1-36 | Cross-platform release gate | CI | wheel smoke覆盖三平台 | L |
| CG12-P1-37 | Hash-locked dependencies | release lock | 可选 `--require-hashes` | M |
| CG12-P1-38 | Installed dependency manifest | release artifact | 与SBOM可对照 | S |
| CG12-P1-39 | Wheel user-data smoke | packaging test | 只读安装目录可运行 | M |
| CG12-P1-40 | sdist smoke | release test | source包可构建安装 | S |
| CG12-P1-41 | LICENSE权利核对 | owner checklist | 可据此选许可证 | M |
| CG12-P1-42 | NOTICE持续维护 | release checklist | 新素材必须登记 | S |
| CG12-P1-43 | 商标/名称审查 | release checklist | 正式发行风险有记录 | M |
| CG12-P1-44 | 数据恢复演练 | release drill | DB+journals可从archive恢复 | L |

---

## 8.3 P2：维护性、桌面体验和可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG12-P2-01 | 拆分 `store.py` | repositories/migrations | 行为不变、测试全过 | XL |
| CG12-P2-02 | 拆分 `local_backend.py` | spool/workers/status | 边界清晰 | XL |
| CG12-P2-03 | 拆分 Data CLI | planner/archive/restore | 规划和执行可独测 | L |
| CG12-P2-04 | ProfileController完整封装 | controller | launcher不散落Future | L |
| CG12-P2-05 | 本机档案列表页 | UI | 新建/切换/重命名/进度 | L |
| CG12-P2-06 | Export-before-delete | profile flow | 删除可恢复 | L |
| CG12-P2-07 | Profile merge UI | local tool | 冲突预览 | XL |
| CG12-P2-08 | 数据管理页 | UI | status/export/import/recovery | XL |
| CG12-P2-09 | GameState Enum | state model | 无魔法字符串 | L |
| CG12-P2-10 | AttemptSaveController | common layer | 五款游戏共用保存状态 | XL |
| CG12-P2-11 | LocalStateController | common layer | settings/progress/slot统一 | L |
| CG12-P2-12 | InputManager | action map | 五款游戏统一输入 | L |
| CG12-P2-13 | 完整 IME 控件 | widget | 光标/选择/组合输入 | M |
| CG12-P2-14 | 键位重映射 | settings UI | 冲突检测/恢复默认 | L |
| CG12-P2-15 | 键盘菜单导航 | focus model | 不用鼠标完整操作 | L |
| CG12-P2-16 | 手柄支持 | controller layer | launcher/五款游戏可用 | L |
| CG12-P2-17 | 音频系统 | BGM/SFX | 无设备不崩 | L |
| CG12-P2-18 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG12-P2-19 | 高 DPI | DPI handling | 图文清晰 | M |
| CG12-P2-20 | 字体 fallback | licensed font chain | 无系统中文字体仍可读 | M |
| CG12-P2-21 | 色弱符号 | patterns/shapes | 颜色非唯一信息 | L |
| CG12-P2-22 | 高对比/降低动态 | accessibility | 动画强度可调 | L |
| CG12-P2-23 | Clock/RNG 注入 | deterministic services | seed+输入可复现 | L |
| CG12-P2-24 | 渐进抽取纯规则 Engine | rules modules | 核心测试无需SDL | XL |
| CG12-P2-25 | Launcher拆分 | app/state/render/data | main职责清晰 | L |
| CG12-P2-26 | 首页改为本机进度中心 | dashboard | best/recent/progress/continue | L |
| CG12-P2-27 | 静态 Surface缓存 | profiler-driven | 有失效规则 | L |
| CG12-P2-28 | Zuma reaction FSM | explicit model | 连锁可属性测试 | L |

---

## 8.4 P3：玩法与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG12-P3-01 | Tetris 7-bag | optional mode | 独立ruleset | M |
| CG12-P3-02 | Tetris ghost/hold/lock delay | comfort features | 规则测试完整 | L |
| CG12-P3-03 | Snake速度/穿墙/障碍 | local modes | 最佳分开 | L |
| CG12-P3-04 | Snake双人同屏 | local multiplayer | 无网络依赖 | L |
| CG12-P3-05 | 2048撤销 | undo model | slot/attempt语义明确 | L |
| CG12-P3-06 | 2048多存档槽 | save UI | 查看/继续/删除 | L |
| CG12-P3-07 | 2048棋盘尺寸 | modes | ruleset分离 | M |
| CG12-P3-08 | Sokoban正式选关 | progress UI | practice/campaign分开 | L |
| CG12-P3-09 | Sokoban星级/最佳推动 | metrics | 规则明确 | M |
| CG12-P3-10 | Sokoban死锁/提示 | analysis | 提示可关闭 | XL |
| CG12-P3-11 | Sokoban编辑器 | XSB import/export | 地图验证 | L |
| CG12-P3-12 | Zuma训练/选关 | practice mode | 不混入通关 | L |
| CG12-P3-13 | Zuma色弱辅助 | symbols | 球色可辨认 | M |
| CG12-P3-14 | Zuma原创道具/轨道 | content | 确定性测试 | XL |
| CG12-P3-15 | Zuma轨道编辑器 | path tool | 版本化/预览 | L |
| CG12-P3-16 | 本机成就 | local achievements | 无账号/遥测 | L |
| CG12-P3-17 | 离线每日挑战 | date seed | 完全离线 | L |
| CG12-P3-18 | 本地 replay | command log | 复盘/调试 | L |
| CG12-P3-19 | 中英文 | localization | 长文本测试 | L |
| CG12-P3-20 | Windows桌面包 | installer/portable | 无Python运行 | XL |
| CG12-P3-21 | macOS app bundle | package | 数据目录smoke | XL |
| CG12-P3-22 | Linux package | AppImage/等效 | XDG正确 | L |
| CG12-P3-23 | 自动发布 | tag workflow | smoke通过才发布 | L |
| CG12-P3-24 | 签名/校验和 | release integrity | 下载可验证 | M |

---

## 9. 必须新增的关键测试

### 9.1 Import 原子性

```text
test_db_commit_then_score_pending_restore_enospc_is_resumable
test_db_commit_then_state_pending_restore_failure_is_resumable
test_recovery_evidence_partial_failure_records_import_phase
test_rerunning_interrupted_import_is_idempotent
test_import_rollback_restores_database_and_journals
```

### 9.2 Pending 边界

```text
test_score_pending_attempt_count_is_bounded
test_score_pending_attempt_count_restore_is_constant_time
test_state_schema3_revision_above_int64_is_rejected_in_preview
test_state_updated_at_out_of_store_range_is_rejected
test_preview_success_implies_pending_restore_input_validity
```

### 9.3 Reject rollback

```text
test_crash_after_rejected_quarantine_recovers_restore_temp
test_restore_temp_is_detected_on_startup
test_quarantine_failure_keeps_previous_non_durable
test_disk_full_during_restore_does_not_lose_previous_pending
```

### 9.4 Archive 完整性

```text
test_export_fails_when_score_pending_scan_is_truncated
test_export_fails_when_state_pending_scan_is_truncated
test_state_total_quota_does_not_quarantine_valid_file
test_export_does_not_upgrade_or_quarantine_source_journal
test_manifest_reports_recovery_omissions
test_export_with_active_in_memory_operation_is_documented_or_coordinated
```

### 9.5 Planner

```text
test_pending_score_conflict_with_target_attempt_is_detected
test_pending_state_conflict_with_target_receipt_is_detected
test_pending_state_missing_profile_is_reported
test_archive_newer_schema_is_rejected
test_import_preview_and_apply_share_target_fingerprint
```

### 9.6 2048

```text
test_continuous_moves_flush_at_max_dirty_age
test_only_one_autosave_future_per_semantic_key
test_autosave_failure_event_from_superseded_future_is_visible
test_multi_instance_policy_is_enforced
```

### 9.7 Release

```text
test_release_wheel_creates_user_database_outside_install_dir
test_release_wheel_works_from_read_only_cwd
test_sdist_build_install_smoke
test_release_constraints_resolve_on_windows_macos_linux
```

---

## 10. 性能与稳定性门槛

1. pygame 主线程不得执行：
   - SQLite；
   - journal scan；
   - archive I/O；
   - OS lock wait；
   - receipt repair；
   - profile migration；
2. `submit_score_async()` enqueue p99 ≤2 ms；
3. state enqueue p99 ≤2 ms；
4. status getter p99 ≤0.2 ms；
5. Export 100,000 attempts 时内存有明确预算；
6. Import 任一失败阶段均可恢复；
7. Huge/corrupt archive 在有界时间失败；
8. 32 进程同 state key 最终符合 merge/order policy；
9. 2048 连续操作最长 autosave间隔受限；
10. 正常游戏目标 60 FPS，基准机 p95 ≤16.7 ms；
11. 100 次切换：
    - 线程回到基线；
    - FD不增长；
    - Surface/内存稳定；
12. 30–60 分钟：
    - pending最终 committed/superseded/recovery/quarantine；
    - 不无限重试；
13. 满盘、只读、坏 DB、坏 archive、坏 slot、坏 progress：
    - 游戏仍可玩；
    - 最后一份有效数据不静默删除；
    - 有恢复提示。

---

## 11. 稳定版本质量门禁

正式稳定版本至少满足：

- 全部 P0 关闭；
- Import 跨 DB 与 journals 可恢复；
- Archive pending 验证与 apply 完全一致；
- Rollback crash 后 previous 自动恢复；
- 完整导出不会静默遗漏 active pending；
- 数据工具不在未协调应用状态下声称完整备份；
- 2048 autosave有最大脏状态时间；
- 当前 head 三平台 CI通过；
- main required checks开启；
- release wheel/sdist smoke；
- 核心测试、regression、stress可单独运行；
- coverage、lint、依赖审计；
- LICENSE/NOTICE在权利确认后完成；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代：

- crash injection；
- post-commit filesystem failure；
- huge archive；
- 多进程 journal；
- 真实恢复演练。

---

## 12. 推荐实施顺序

### M0：关闭恢复工具发布阻断

1. Import phase journal；
2. Pending输入边界；
3. Rollback transaction recovery；
4. Export完整性；
5. 对应故障注入。

### M1：数据工具封板

- read-only snapshot；
- semantic pending planner；
- restore-replace；
- archive compatibility；
- streaming；
- recovery UI；
- maintenance coordination。

### M2：工程与发行

- required checks；
- 三平台 release smoke；
- hashes/manifest；
- LICENSE权利核对；
- 恢复演练；
- 模块拆分。

### M3：桌面体验

- 档案页；
- InputManager；
- 键位/手柄；
- 音频；
- DPI/字体；
- 可访问性；
- settings UI；
- 本机进度中心。

### M4：玩法与打包

- 五款游戏单机舒适功能；
- Sokoban/Zuma编辑器；
- 成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 基础稳定后再新增游戏。

---

## 13. 存储层封板建议

完成本任务书 P0 与以下 P1 后：

- Archive完整性；
- Restore-replace；
- Pending planner；
- Maintenance coordination；
- Recovery drill；

建议正式冻结：

```text
score spool schema
state journal schema
state receipt schema
archive schema
2048 slot ownership schema
```

除确定性数据丢失或安全缺陷外，不再增加新的 journal、receipt 或 migration 抽象。

后续主要精力转向：

- 档案体验；
- 可访问性；
- 游戏内容；
- 性能；
- 三平台发行。

---

## 14. 最终判断

本轮仍然是明显进步：

- 上一轮数据 CLI 的主要问题已经真实修复；
- 2048 ownership 和终局恢复已经达到较高成熟度；
- Release gate、依赖审计和 wheel smoke 已经进入 CI；
- 当前 head CI 成功；
- 五款游戏主玩法未见明显回退。

新的问题主要不是“游戏越来越坏”，而是恢复工具从简单脚本升级为真正的数据迁移系统后，必须满足更严格的：

```text
跨资源原子性
完整性证明
输入预算
中断恢复
只读快照
```

最推荐的下一步是：

> **停止增加数据功能，先把 Import 做成可恢复事务，并统一 pending 的 preview/apply 验证。**

完成这一步后，存储层应进入封板，项目的主要开发方向转向本地桌面体验和五款游戏本身。
