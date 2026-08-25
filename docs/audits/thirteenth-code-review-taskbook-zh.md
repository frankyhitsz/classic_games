# Classic Games Hub 第十三次代码审查与本地优先优化任务书

> 审查日期：2026-08-25
> 当前基线：`main` 分支 commit `05ba2598bca4cc595bad15f201e75780bf7a2e5b`（`05ba259`）
> 对比基线：第十二次审查 commit `ac45f0024c8e298a0cc2f3d2062050c718fc8e50`
> 增量提交：3 个；核心实现提交为 `10363caba316bffaab2ea3ec54a1076842d7a129`，随后有 Windows rollback-image 同步修复与 CI 记录
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 明确非目标：账号、云端排行、匹配、赛季、实时联机、反作弊、强制联网和默认遥测

---

## 0. 执行摘要

本轮修复是有效的，而且上一轮的主要发布阻断已经大面积关闭：

- Import 已引入 `PREPARED → DB_APPLIED → FILES_PUBLISHED → COMPLETED` 阶段事务；
- 数据库回滚镜像、待发布文件和目标原件会在修改前准备；
- Pending score 的 `attempt_count` 已有上限，并可一次原子提升；
- State journal schema 1/2/3 已统一复用 store 的 int64、时间、key、args、ruleset 和 hash 校验；
- Reject rollback 已有 `.reject-*.txn` 和启动恢复；
- Application lifetime lease 已加入默认 LocalBackend；
- Export 默认要求应用关闭，并使用只读 pending snapshot；
- Archive 已记录 source/included/omitted/complete；
- State 总量越界不再把当前合法文件移入 quarantine；
- Planner 会在 scratch SQLite 中实际执行 score/state operation；
- 已增加严格 merge import 与完整 `restore-replace` 两种语义；
- Recovery evidence 路径已拒绝大部分 Windows/Unix 穿越形式；
- 2048 slot validator 已从 pygame 中抽离；
- 2048 已有 quiet debounce、max dirty age、单 in-flight save、owner epoch 和 takeover CAS；
- Package smoke 已覆盖 wheel、sdist、只读 cwd 与外部 user-data；
- 当前 head 的 GitHub Actions CI 已成功。

项目主架构已基本定型：

```text
pygame Launcher / Game
    │
    ├── GameDataService
    ├── Local read worker
    └── Local write worker
            ├── SQLite
            ├── score spool
            └── keyed local-state journal

Data CLI
    ├── inactive application lease
    ├── maintenance lock
    ├── archive snapshot
    └── import phase transaction

Optional Flask
    └── 仅作为本机成绩 API 调试适配器
```

**不建议再次推倒，不建议再增加新的 journal/receipt 层。**

不过，本轮仍发现 8 类发布阻断或接近发布阻断的问题：

1. 中断 Import 不会在普通游戏启动前自动恢复或阻止启动；
2. Export 尚可覆盖 application/maintenance lock 与 legacy pending 控制文件；
3. Recovery evidence 导出会跟随指向数据目录外的符号链接；
4. Recovery evidence 导入的“先 resolve、后查 symlink”允许数据目录内别名重定向到 active/control 路径；
5. 所谓完整 archive 未覆盖 reject marker、旧 `.restore` 和旧 `pending_saves.json`；
6. `restore-replace` 也不会删除这些 active protocol artifacts，旧状态可能在恢复后重新出现；
7. Reject transaction marker 本身不是原子发布，也没有外层 hash；
8. Windows `--include-recovery` 导出的嵌套路径使用反斜杠，而 importer 明确拒绝反斜杠。

此外还有若干 P1：

- ImportTransaction staged/before 文件没有内容 hash；
- Optional Flask 不参加 application lease；
- 同一 state operation identity 的不同 payload 可在 journal 层被静默忽略；
- 2048 owner claim 尚未等到 CAS ACK 就开放输入；
- 2048 关闭时 queued release 可能没有真正提交；
- `restore-replace` 不清理 `legacy_*` / `scores` 等旧表；
- Archive manifest 的内部计数、应用身份和 complete 关系没有严格验证；
- Recovery evidence 重复路径没有冲突规则；
- `main` 仍未启用 required checks；
- LICENSE 仍需权利人决定。

本轮结论：

> **数据主链已经接近封板；下一步应只处理协议完整性和恢复边界，不再扩大持久化架构。**

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成

- 锁定当前 `main`；
- 对比第十二次基线与当前 head；
- 阅读当前：
  - `game_service/import_transaction.py`
  - `game_service/data_cli.py`
  - `game_service/maintenance.py`
  - `game_service/local_backend.py`
  - `game_service/store.py`
  - `game_service/save_slot_validation.py`
  - `client/launcher.py`
  - `client/common/ui.py`
  - 五款游戏
  - optional Flask
  - storage v2–v10 tests
  - regression、stress、release runner
  - CI、README、NOTICE、constraints 和任务记录；
- 复核上一轮 29 条 finding；
- 组合检查：
  - import crash → 普通 launcher 启动；
  - application/maintenance lock inode 替换；
  - recovery symlink 导出与导入；
  - reject marker 发布中断；
  - archive active-protocol inventory；
  - replace restore 后旧 artifact 复活；
  - Windows evidence path；
  - 2048 owner claim/release；
- 使用等价 POSIX 文件锁模型验证：
  - 旧 inode 仍持 shared lock；
  - 路径被 `os.replace()` 成新 inode；
  - 新 inode 可同时取得 exclusive lock；
- 使用等价路径模型验证：
  - `imported-recovery/<id>` 指向同一数据目录下 `pending/` 的 symlink；
  - 当前“resolve 后查 symlink”逻辑会接受最终 `pending/...` 路径。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题主要路径与关键边界已经闭环 |
| **基本到位** | 原问题已关闭，但相邻组合仍有缺口 |
| **代码路径确定** | 当前控制流直接导出结论 |
| **状态模型复现** | 用等价锁/文件/路径顺序复现最终状态 |
| **跨平台确定性** | 由标准路径语义决定，但仍需目标系统回归测试 |
| **待真机验证** | 需要 Windows/macOS/Linux 桌面构建或 GUI 实测 |
| **产品任务** | 不是 Bug，而是符合本地单机定位的完善项 |

### 1.3 限制

当前执行容器无法直接克隆 GitHub，也没有本地 pygame/Flask 完整环境，因此没有在本次容器中重跑所有测试。

可确认的自动化证据：

- 当前 head 的 GitHub Actions workflow 已 completed/success；
- 仓库自测记录为 161 项 storage、107 项 gameplay，并完成 stress 与 release 八阶段；
- 本报告将这些标为项目/远端自动化证据，不冒充当前容器独立执行。

---

## 2. 第十二次审查修复验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| Import DB commit 后文件恢复失败 | **主体修复** | 阶段事务与 rollback 已有；普通启动器不处理 unfinished txn |
| Score attempt_count 无上限 | **修复到位** | 0–10,000，单次原子提升 |
| State int64/时间边界不统一 | **修复到位** | schema 1/2/3 与 store validator 共用 |
| `.restore` 不会自动恢复 | **修复到位** | reject marker + legacy restore scanner；marker 自身发布仍有窗口 |
| Maintenance 不覆盖内存队列 | **按边界处理** | application lease 要求关闭游戏；snapshot 明确是 persisted scope |
| Store 初始化早于 import lock | **修复到位** | import/replace 在两层独占锁内初始化 |
| Pending scan 截断无报告 | **修复到位** | snapshot completeness + `--allow-partial` |
| State 总量越界误 quarantine | **修复到位** | replay scanner停止而不隔离当前合法文件 |
| Export 会升级/隔离 pending | **修复到位** | read-only snapshot |
| Planner 不模拟目标业务冲突 | **明显改善** | scratch DB 实际执行 score/state operation |
| 没有 replace restore | **修复到位** | `restore-replace` 已有；active hidden artifacts 仍未纳入 |
| Preview/apply TOCTOU | **基本到位** | apply 在锁内重规划；未输出可签名外部 plan |
| Evidence 路径穿越 | **部分到位** | 字符/drive/保留名已加固；同目录 symlink alias 仍可重定向 |
| Recovery omissions | **修复到位** | manifest 和 result 可见 |
| 较新 schema archive | **修复到位** | v2 reader拒绝较新 store schema |
| 2048 共享 slot validator | **修复到位** | pygame-free validator |
| Archive 多份内存峰值 | **有界但未重构** | 仍为单 JSON/多份内存 |
| Reject rollback失败无后备 | **修复到位** | previous 进入 non-durable；marker写入崩溃窗口仍在 |
| Score maintenance timeout | **修复到位** | unexpected callback 保留 mutation |
| Close 等待 300 秒 | **修复到位** | 普通等待降为 5 秒 |
| 2048 纯 trailing debounce | **修复到位** | quiet + max dirty age |
| 2048 Future 覆盖 | **主体修复** | 单 in-flight + dirty buffer；close release仍有缺口 |
| 单 autosave | **产品策略** | 当前明确一个 profile/game autosave |
| Legacy status | **修复到位** | 只读报告 schema/missing tables |
| Merge receipt 前 1000 保护 | **修复到位** | 临时表/NOT EXISTS |
| Required checks | **未完成** | 仓库设置仍关闭 |
| Release lock | **明显改善** | 闭包固定；无 hashes/平台锁 |
| Wheel user-data smoke | **修复到位** | wheel/sdist、只读 cwd、外部数据目录 |
| LICENSE | **未完成** | NOTICE 已有，授权需所有者 |

---

# 3. 当前发布阻断问题

## 3.1 CG13-F01：普通应用启动不会处理 unfinished ImportTransaction

- **优先级**：P0
- **证据**：代码路径确定
- **涉及**：
  - `recover_import_transactions()`
  - `LocalBackendClient.__init__`
  - `ApplicationSession.acquire()`

当前 Import recovery 只由 Data CLI 调用。

场景：

```text
import commit SQLite
→ phase = DB_APPLIED
→ 部分/尚未 publish pending files
→ CLI 进程崩溃
→ OS 释放 exclusive locks
→ 玩家直接启动 launcher
→ LocalBackend 取得 shared application lease
→ 打开部分导入的 DB
```

之后玩家产生新记录；再运行 Data CLI 时，旧 import transaction 会回滚到导入前镜像，可能同时回滚玩家在“部分导入窗口”内产生的新数据库写入。

### 要求

- LocalBackend 取得 shared application lease前检查 import txn；
- 有 txn 时：
  1. 尝试取得 exclusive application + maintenance lock；
  2. 完成 rollback；
  3. 再建立 shared lease；
- corrupt txn 时拒绝打开 DB，显示 recovery-required；
- optional Flask 启动同样遵守。

---

## 3.2 CG13-F02：Export 可覆盖 application/maintenance lock 和 legacy control file

- **优先级**：P0
- **证据**：代码路径 + POSIX inode 模型复现

`_guard_export_target()` 保护：

```text
games.db
games.db-wal
games.db-shm
games.db-journal
pending/
pending-state/
recovery dirs
```

但没有保护：

```text
.games.db.application.lock
.games.db.maintenance.lock
pending_saves.json
```

更关键的是 guard 在锁文件创建前运行；之后 snapshot 完成、锁释放，再执行 `os.replace(output)`。

若 output 是 application lock：

```text
guard 时文件可能不存在
→ export 自己创建并锁旧 inode
→ 释放锁
→ 另一个 launcher取得旧 inode shared lock
→ export用 archive替换路径
→ 新进程打开新 inode并取得 exclusive lock
```

形成 split-brain。

### 要求

- 建立统一 `reserved_control_paths(database)`；
- export/import/evidence 永远不可写入：
  - DB sidecars；
  - application/maintenance locks；
  - legacy pending；
  - import txn roots；
  - journal lock/control files；
- `--force` 也不能覆盖；
- 普通无 `--force` publication 使用原子 no-clobber，不只在开始时检查。

---

## 3.3 CG13-F03：Recovery evidence export 会跟随数据目录外 symlink

- **优先级**：P0/P1（隐私/备份边界）
- **证据**：代码路径确定

`_recovery_paths()` 接受名称匹配的 entry，不拒绝 symlink。

`_export_recovery()` 使用：

```text
is_file()
rglob()
stat()
read_bytes()
```

这些会跟随 symlink。

例如：

```text
pending-quarantine → ~/Documents/private
```

使用 `--include-recovery` 会把外部文件放入 archive。

### 要求

- 对 root 和每个 descendant 使用 `lstat()`；
- symlink 不跟随；
- resolved path 必须位于真实 data directory；
- hard-link/特殊文件策略明确；
- 完整模式遇 symlink应失败，partial 模式报告 omission。

---

## 3.4 CG13-F04：Evidence import 的同目录 symlink alias 可写入 active/control 路径

- **优先级**：P0
- **证据**：路径模型复现
- **涉及**：`ImportTransaction._ensure_safe_target`

当前先：

```python
candidate = target.resolve()
```

再沿 **resolved path** 检查 symlink。

如果：

```text
imported-recovery/<archive-id> → pending/
```

则：

```text
target = imported-recovery/<id>/x.json
resolve = pending/x.json
```

最终路径仍在 data directory，且 resolved ancestor 不再显示原 symlink，因此被接受。

同理可重定向到：

- application/maintenance lock；
- DB sidecars；
- legacy pending；
- active state journal。

### 要求

- FileOperation 目标必须属于明确 allowlist：
  - `pending/<request>.json`
  - `pending-state/<digest>.json`
  - `imported-recovery/<archive-id>/<safe-relative>`
- 对 lexical path 每一层 `lstat()`；
- 使用 `openat/dir_fd/O_NOFOLLOW` 或等效安全写入；
- resolved containment 只是第二层检查；
- 禁止任何 control path。

---

## 3.5 CG13-F05：Complete archive 未覆盖 reject marker 和 legacy pending

- **优先级**：P0
- **证据**：代码路径确定

Score snapshot只扫描：

```text
pending/*.json
```

State snapshot只扫描：

```text
pending-state/*.json
```

但 recoverable active protocol 还包括：

```text
pending_saves.json
pending-state/.reject-*.txn
pending-state/*.restore  # 旧协议兼容
```

场景：

```text
reject transaction已把新值隔离
previous只存在 marker
→ 在 launcher 重启恢复前执行 export
→ snapshot看到0个 state .json
→ manifest.complete=true
→ archive遗漏 previous
```

### 要求

- Active protocol inventory 不等于 `.json` inventory；
- 完整 export：
  - 先显式恢复这些事务；或
  - 把它们规范化进入 archive；
- read-only 模式不能静默忽略；
- manifest 分别记录 active/recovery-transaction/legacy source。

---

## 3.6 CG13-F06：Restore-replace 会保留旧 marker，旧状态可在恢复后复活

- **优先级**：P0
- **证据**：代码路径确定

`_replacement_plan()` 只删除 pending 目录中的 `*.json`。

不会删除：

```text
pending_saves.json
pending-state/.reject-*.txn
legacy .restore
其他 active transaction artifact
```

恢复后创建 `PersistentStateOutbox` 时，marker 会把 archive 之前的 previous operation 恢复到 active journal；旧 shared score file 也可能重新迁移。

### 要求

- 定义完整 active-protocol namespace；
- replace restore 事务性替换整个 namespace；
- control lock 文件保留；
- quarantine/migration evidence默认保留但明确不 active；
- restore 后执行“无旧 active artifact”检查。

---

## 3.7 CG13-F07：Reject transaction marker 本身不是原子发布

- **优先级**：P0
- **证据**：代码路径确定

Marker 直接写到最终：

```text
.reject-<digest>-<uuid>.txn
```

而不是：

```text
temp → fsync → rename
```

崩溃/ENOSPC 可留下半写 marker。

恢复器解析失败时目前主要跳过；previous operation 可能只存在于损坏 marker和进程内存。

### 要求

- marker 使用唯一 temp 原子发布；
- marker 加 schema、hash、size；
- corrupt marker进入专用 quarantine并产生 notice；
- previous operation同时进入 non-durable fallback；
- 覆盖：
  - write中断；
  - marker fsync后崩溃；
  - rejected quarantine后崩溃；
  - previous publish后崩溃。

---

## 3.8 CG13-F08：Windows recovery evidence archive 路径无法自洽

- **优先级**：P0/P1（跨平台恢复）
- **证据**：跨平台确定性

Exporter构造：

```python
str(Path(root.name) / path.relative_to(root))
```

Windows 结果使用：

```text
pending-quarantine\file.json
```

Importer `_safe_evidence_relative()` 明确拒绝任何反斜杠。

因此 Windows 上含嵌套 recovery evidence 的 archive 可能无法被同版本 preview/import。

### 要求

- Archive 路径统一使用 POSIX separator：
  ```python
  PurePosixPath(...).as_posix()
  ```
- 导入时只接受 archive POSIX path；
- 增加 Windows export → preview → import round-trip；
- 同一 archive 必须可跨 Windows/macOS/Linux。

---

# 4. 高优先级残留与新增问题

## 4.1 CG13-F09：ImportTransaction staged/before 内容不受 journal hash保护

Journal hash保护的是元数据，但没有记录：

```text
staged_sha256
before_sha256
rollback_database_sha256
```

磁盘损坏或同用户篡改后，`publish_files()` / `rollback()` 会使用改变后的 bytes。

### 任务

- FileOperation记录 size/hash；
- open/publish/rollback前复核；
- rollback DB 同时做 hash 与 `quick_check`；
- mismatch 进入 manual recovery，绝不盲目发布。

## 4.2 CG13-F10：同一 transaction 可以含重复 target

Recovery evidence 两项可以映射到同一 target。

若 target 原本不存在，当前 planner可能按顺序发布后一个值，没有明确 duplicate/conflict 语义。

### 任务

- `validate_file_operations` 强制 target 唯一；
- 完全相同内容可 deduplicate；
- 不同内容拒绝；
- transaction journal也拒绝重复 target。

## 4.3 CG13-F11：Archive manifest 内部一致性未严格校验

目前主要校验整体 hash、archive version 和 schema上界。

尚未严格确认：

- `application.id == classic-games-hub`
- table_counts 与实际 rows一致；
- pending counts 与数组一致；
- top-level complete 与各 component complete一致；
- recovery count/source/included/omitted一致；
- ruleset manifest 与 rows/当前 reader关系。

`restore-replace` 主要信任：

```text
manifest.complete == true
```

### 任务

建立 `validate_archive_manifest()`，preview/apply/replace共用。

## 4.4 CG13-F12：Optional Flask 不参加 application lifetime lease

Flask 直接持有 `LocalGameStore`，没有 `ApplicationSession`。

因此官方调试服务运行时，Data CLI 可能认为应用 inactive，开始 import/replace。

### 任务

- Flask app lifetime取得 shared application lease；
- app close释放；
- 测试配置可显式关闭；
- 不增加在线账号/状态 API；
- 只用于阻止维护并发。

## 4.5 CG13-F13：Maintenance/import busy 在 launcher 构造阶段可能直接终止程序

Launcher 直接：

```python
LocalBackendClient(defer_initialization=True)
```

`ApplicationSession.acquire()` 失败未转成用户可见恢复界面。

### 任务

- 捕获 `MaintenanceBusyError/import_recovery_required`;
- 显示“数据维护进行中/需要恢复”；
- 提供重试和退出；
- 不打开数据库。

## 4.6 CG13-F14：相同 state operation identity 的不同 payload 可被 journal静默忽略

State outbox ordering：

```text
(logical_revision, operation_id)
```

若 incoming 与 existing 的 order完全相同，但 payload hash不同：

```text
incoming <= existing
→ 直接保留 existing
```

没有返回 conflict。

### 任务

- 相同 order + 不同 hash → `state_operation_conflict`;
- 普通写入、import planner 和 replay共用；
- 不覆盖也不静默成功。

## 4.7 CG13-F15：2048 owner claim 未等 CAS ACK 就开放玩法

Load完成后：

```text
slot_load_state = ready
→ submit owner claim
```

游戏已经接受输入，而 claim Future尚未确认。

若另一个实例先更新 slot：

```text
玩家已移动
→ claim返回 slot_in_use
→ 游戏重新加载远端 slot
→ 本地移动丢失
```

### 任务

增加：

```text
CLAIMING
READY
CONFLICT
```

只有 claim committed 后才能移动。

## 4.8 CG13-F16：2048 关闭时 queued release 可能未真正提交

`before_close()`：

1. 请求 released save；
2. 等待当前 Future 0.5s；
3. poll一次；
4. 再等待 0.5s；
5. 第二次等待后没有再次 poll/提交 queued release。

如果原 in-flight save持续超过第一次等待，release仍只在 game object dirty buffer中，退出后不会再提交。

### 任务

- Backend提供 `flush_state_key()` 或 game-side bounded loop；
- 退出前至少保证 released intent 已进入 durable journal；
- 超时则保持 active并明确提示，不声称已释放；
- 增加 slow Future test。

## 4.9 CG13-F17：Restore-replace 不清理 legacy/unknown DB tables

Replace删除当前业务表行，但可能保留：

```text
scores
legacy_settings_*
legacy_progress_*
legacy_save_slots_*
legacy_state_receipts_*
旧 schema_meta import marker
```

这意味着：

- 旧个人数据仍在 DB；
- “替换”不是隐私意义的完整替换；
- 未来迁移可能再次读取 marker/table。

### 任务

二选一：

1. 从 archive 生成全新 current-schema DB，再原子替换；
2. 明确定义保留清单并将旧表移到单独 evidence DB。

推荐方案 1。

## 4.10 CG13-F18：Recovery evidence 导出和 status 都会跟随 symlink

除 archive 泄露外，`status` 也可能递归计算外部目录大小，产生：

- 启动慢；
- 权限错误；
- 非预期目录遍历。

与 F03 同一修复批次。

## 4.11 CG13-F19：Export 的 schema_version 不在同一 snapshot内捕获

Tables 在锁内、read transaction内读取，但：

```python
schema_version = store.schema_version()
```

发生在锁外的新连接。

虽然同版本正常应用几乎不变，但协议上不是同一快照。

### 任务

在 read transaction 中同时读取：

- schema version；
- tables；
- quick/integrity marker；
- archive metadata high-water。

## 4.12 CG13-F20：无 `--force` 也存在输出 publication TOCTOU

Guard 时 output 不存在；snapshot期间另一个进程创建同名文件；最终 `os.replace()` 仍会覆盖。

### 任务

- 最终 publication 使用 atomic no-clobber；
- `--force` 才允许 replace；
- publish前重新核对 reserved path；
- 与 F02 一并测试。

## 4.13 CG13-F21：Recovery evidence entry 的 size/hash不是强制字段

Current-format archive中的 evidence可以：

- 不提供 sha256；
- size 与 bytes不一致；
- 重复 path。

整体 manifest hash只能证明 archive本身未在 hash后变化，不能表达每项结构契约。

### 任务

v2 evidence强制：

```text
path
size
sha256
content_base64
```

omission marker采用不同 schema。

## 4.14 CG13-F22：Archive 仍为多份内存 JSON

当前 128 MiB上限保证有界，但峰值包括：

- Python object tree；
- rows lists；
- base64字符串；
- canonical bytes；
- JSON decoder临时对象。

峰值可能是 archive大小的数倍。

### 任务

Archive v3再考虑：

- ZIP/container；
- JSONL table streams；
- 单独 manifest；
- 增量 hash；
- 不在当前 v2上继续打补丁。

## 4.15 CG13-F23：Import recovery 缺少面向用户的事务管理入口

`status` 只显示 unfinished count。

Corrupt transaction 会阻止维护，但没有：

- list transactions；
- export raw transaction；
- retry rollback；
- restore rollback image；
- abandon after backup。

### 任务

增加安全 CLI/service，不直接提供危险 delete。

## 4.16 CG13-F24：Backup、transaction root、quarantine 缺少统一保留策略

长期升级/import会累积：

- DB backups；
- imported-recovery；
- quarantine；
- migration backup；
- corrupt DB；
- transaction evidence。

### 任务

- 默认只报告，不自动删除；
- 提供大小、日期、来源；
- 清理前 export；
- active/last-known-good永不误删。

## 4.17 CG13-F25：Application/maintenance lock 路径自身可被 symlink

`os.open(... O_CREAT)` 默认跟随 symlink；Windows分支还可能 `ftruncate(256)`。

同用户恶意/损坏 symlink可让控制代码打开或改写别的文件。

### 任务

- `lstat`；
- POSIX `O_NOFOLLOW`;
- regular-file验证；
- owner/permission验证；
- symlink lock进入 recovery-required。

## 4.18 CG13-F26：Optional direct store callers可绕过 maintenance contract

`LocalGameStore` 是公共类；脚本、调试工具或第三方调用可不取得 application/maintenance lease。

### 任务

- 文档标注 public read / internal write boundary；
- 正式写工具使用 service；
- optional Flask修复；
- 不阻止测试 fixture直接使用。

## 4.19 CG13-F27：Current main 未启用 required checks

当前 CI通过不等于无法绕过。

### 任务

至少要求：

```text
release-gate
core-only
Ubuntu test
macOS test
Windows test
```

## 4.20 CG13-F28：Release dependencies无 hashes和平台锁

精确闭包明显优于范围依赖，但：

- 没有 `--require-hashes`;
- 不保证三平台 wheel都长期可取；
- build bootstrap和 pip本身未完全锁死。

### 任务

生成受审查的 per-platform lock/manifest。

## 4.21 CG13-F29：LICENSE仍需权利人决定

NOTICE 已正确表明：

- 当前没有打包图像、音频、音乐和字体；
- 图形运行时绘制；
- 名称/商标、代码权利和未来素材需核对。

下一步只能由仓库所有者选择 LICENSE。

---

# 5. 五款游戏专项判断

## 5.1 Tetris

当前核心修复保持：

- 同义键逻辑 edge；
- DAS/ARR；
- soft drop；
- 大 dt generation guard；
- top-out；
- 自定义辅助旋转诚实命名。

后续单机完善：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- RNG注入；
- 游戏内规则页；
- 静态网格缓存。

## 5.2 Snake

当前核心保持：

- 双转向队列；
- 不允许180°；
- 长停顿保护；
- 吃食物后间隔更新；
- 尾格碰撞语义。

后续：

- 速度选项；
- 穿墙；
- 障碍；
- 双人同屏；
- RNG注入；
- 色弱纹理；
- 模式独立最佳。

## 5.3 2048

当前已经具备：

- 无效果输入不堵队列；
- load gate；
- attempt恢复；
- score补交；
- corruption quarantine；
- owner token/epoch；
- takeover CAS；
- max dirty age；
- 单 in-flight save。

优先修复：

- claim ACK gate；
- close release durability；
- 单槽/多槽产品决策。

后续：

- 撤销；
- 多存档槽；
- 棋盘尺寸；
- RNG注入；
- 终局历史浏览。

## 5.4 Sokoban

当前核心保持：

- 同关不重复计分；
- practice/campaign分离；
- 全关完成；
- 0分合法通关；
- progress schema/generation/merge。

后续：

- 正式选关；
- 最少移动/推动；
- 星级；
- 死锁检测；
- 提示；
- 地图闭合、可达和死角验证；
- XSB导入；
- 编辑器；
- 固定逻辑窗口。

## 5.5 Zuma

当前核心保持：

- 多 pending reaction；
- swept collision；
- path bisect；
- 长帧余量；
- 临界救场；
- progress schema/generation。

后续：

- reaction FSM；
- 属性测试；
- incoming deque；
- RNG注入；
- 训练/选关；
- 色弱符号；
- 原创道具；
- 轨道编辑器。

---

# 6. 项目完善方向

## 6.1 本地数据产品

- 数据管理页面；
- 档案列表、新建、切换、重命名；
- export-before-delete；
- 家庭成员分档；
- 当前规则与 legacy历史分开；
- backup/quarantine/import transaction 浏览与恢复；
- replace restore预览。

## 6.2 桌面体验

- 全键盘菜单；
- 手柄；
- 键位重映射；
- BGM/SFX；
- 可调窗口；
- 高 DPI；
- CJK字体 fallback；
- 色弱符号；
- 高对比；
- 降低动态效果；
- 崩溃恢复页和日志入口。

## 6.3 工程维护

- 拆分 `store.py`、`local_backend.py`、`data_cli.py`；
- service/repository/migration/archive 分层；
- 类型检查；
- state/import model tests；
- fault injection；
- 性能基准；
- release recovery drill。

## 6.4 完全离线内容

- 本机成就；
- 日期 seed挑战；
- replay/复盘；
- 本地化；
- 三平台桌面包。

---

# 7. 明确不建议做的事情

- 不做账号系统；
- 不做公网排行榜；
- 不做匹配、赛季和联机；
- 不做服务端权威判定和反作弊；
- 不做默认遥测；
- 不做云同步/多设备合并；
- 不新增 journal/receipt 类型；
- 不为了形式统一重写当前合理的 unittest / subprocess regression / stress 分层。

---

# 8. 完整优化任务清单

## 优先级定义

- **P0**：可能让恢复结果不一致、遗漏唯一 pending、破坏锁协议或造成确定性跨平台恢复失败。
- **P1**：恢复硬化、数据边界、工程与正式发行基础。
- **P2**：维护性、桌面体验、性能和可访问性。
- **P3**：玩法内容与桌面发行。
- **S/M/L/XL**：相对工作量。

---

## 8.1 P0：协议完整性与恢复安全

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG13-P0-01 | 普通启动前检测 import txn | startup recovery gate | unfinished txn时不打开DB | L |
| CG13-P0-02 | 自动 rollback 或 recovery-required UI | launcher/backend | 首个启动进程安全恢复；corrupt txn不继续 | L |
| CG13-P0-03 | 统一 reserved control paths | path policy | DB sidecar、locks、legacy pending永不可覆盖 | M |
| CG13-P0-04 | Export atomic no-clobber | output publisher | 无`--force`时竞争创建文件不被覆盖 | M |
| CG13-P0-05 | FileOperation target allowlist | transaction validator | 只允许 pending/state/evidence目标 | L |
| CG13-P0-06 | Lexical no-symlink validation | secure path walker | 同目录alias不能重定向 | L |
| CG13-P0-07 | Recovery export no-follow | snapshot reader | 外部symlink内容不进入archive | M |
| CG13-P0-08 | Archive evidence POSIX path | exporter | Windows导出路径无反斜杠 | S |
| CG13-P0-09 | Active protocol artifact inventory | snapshot model | reject/restore/legacy file被识别 | L |
| CG13-P0-10 | Complete export处理事务artifact | export policy | marker存在时恢复、纳入或明确失败 | M |
| CG13-P0-11 | Replace restore替换完整active namespace | replace planner | 旧marker/legacy pending不复活 | L |
| CG13-P0-12 | Reject marker原子发布 | journal protocol | partial marker永不可见 | M |
| CG13-P0-13 | Reject marker hash与corrupt recovery | schema/test | 损坏marker保留并提示 | M |
| CG13-P0-14 | 2048 ownership claim gate | game state | CAS ACK前不能移动 | M |
| CG13-P0-15 | P0 crash/symlink/cross-platform tests | regression suite | 每个P0有确定性用例 | L |
| CG13-P0-16 | P0 release gate | CI | 任一用例失败禁止release | S |

---

## 8.2 P1：数据硬化与正式发行基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG13-P1-01 | Staged file hash | txn journal v2 | publish前复核size/sha256 | M |
| CG13-P1-02 | Before image hash | txn journal v2 | rollback前复核 | M |
| CG13-P1-03 | Rollback DB hash | transaction | quick_check+hash双验证 | M |
| CG13-P1-04 | Duplicate FileOperation检测 | planner | 同target不同bytes拒绝 | S |
| CG13-P1-05 | Duplicate evidence path规则 | archive validator | exact duplicate跳过，冲突拒绝 | S |
| CG13-P1-06 | Manifest application ID验证 | archive reader | 非本项目archive拒绝 | S |
| CG13-P1-07 | Manifest counts一致性 | archive reader | counts与arrays一致 | M |
| CG13-P1-08 | Manifest complete一致性 | archive reader | top-level与components一致 | M |
| CG13-P1-09 | Ruleset manifest策略 | archive reader | 兼容/历史语义明确 | M |
| CG13-P1-10 | Schema version同snapshot读取 | export | 元数据与tables同事务 | S |
| CG13-P1-11 | Evidence item强制size/hash | archive v2 contract | 输入结构严格 | S |
| CG13-P1-12 | Import transaction管理CLI | data tool | list/export/retry rollback | L |
| CG13-P1-13 | Corrupt transaction恢复页 | UI/service | 不要求用户手删目录 | L |
| CG13-P1-14 | Flask application lease | optional adapter | API运行时维护命令被拒绝 | M |
| CG13-P1-15 | Launcher maintenance-busy UI | startup UI | 不输出Python traceback | M |
| CG13-P1-16 | Lock file no-follow | maintenance | symlink lock不被打开/截断 | M |
| CG13-P1-17 | State identical-order conflict | outbox/store | 同ID/revision不同hash稳定409 | S |
| CG13-P1-18 | 2048 released intent durable flush | game/backend API | 关闭前release进入journal | M |
| CG13-P1-19 | 2048 slow-save close test | test | in-flight>1s不丢release | S |
| CG13-P1-20 | Replace restore新DB构建模式 | restore engine | 不保留legacy/unknown表 | XL |
| CG13-P1-21 | Legacy table evidence导出 | recovery | 旧表可单独保存 | M |
| CG13-P1-22 | Restore privacy语义文档 | ADR/UI | active replace与evidence preserve分开 | S |
| CG13-P1-23 | Recovery symlink status hardening | status | 不递归外部目录 | S |
| CG13-P1-24 | Recovery special-file policy | archive | FIFO/socket/device不读取 | S |
| CG13-P1-25 | Export output final revalidation | publisher | reserved路径竞争仍拒绝 | S |
| CG13-P1-26 | Import target fingerprint | transaction | DB/pending变化可检测 | M |
| CG13-P1-27 | Archive replay drill | release test | export→replace→replay全部通过 | L |
| CG13-P1-28 | Backup retention inventory | service/CLI | 大小、日期、来源可见 | M |
| CG13-P1-29 | Safe cleanup flow | service/UI | export后、确认后、可审计 | L |
| CG13-P1-30 | Merge import与replace restore ADR | docs | 用户不会混淆语义 | S |
| CG13-P1-31 | Archive v3设计冻结 | ADR only | streaming需求不继续污染v2 | S |
| CG13-P1-32 | Archive内存benchmark | benchmark | 128MiB峰值有记录 | M |
| CG13-P1-33 | LocalGameStore写边界文档 | API docs | 正式调用走service | S |
| CG13-P1-34 | Direct writer maintenance test | tests | official writers均参与lease | M |
| CG13-P1-35 | Required checks | repo settings | main不可绕过CI | S |
| CG13-P1-36 | Release branch/tag policy | governance | release只来自通过检查的commit | S |
| CG13-P1-37 | Per-platform dependency lock | release files | Linux/macOS/Windows可复现 | L |
| CG13-P1-38 | Hash-locked dependencies | installer | 支持`--require-hashes` | M |
| CG13-P1-39 | Build bootstrap lock | release | pip/build/setuptools版本明确 | M |
| CG13-P1-40 | Installed manifest vs SBOM | release test | 两者一致 | S |
| CG13-P1-41 | Static type checking | pyright/mypy | service/store/archive边界通过 | L |
| CG13-P1-42 | Fault injection registry | tests | fsync/rename/lock/backup统一注入 | L |
| CG13-P1-43 | Model-based import tests | tests | phase/target/rollback不变量 | L |
| CG13-P1-44 | State journal model tests | tests | CAS/merge/reject不变量 | L |
| CG13-P1-45 | Coverage分阶段提升 | CI | core≥90%，全项目≥80%路线 | M |
| CG13-P1-46 | Structured logs | logging | recovery transaction有trace ID | M |
| CG13-P1-47 | Recovery telemetry仅本地 | local diagnostics | 无上传、可导出 | S |
| CG13-P1-48 | LICENSE权利清单完成 | owner task | 可安全选择许可证 | M |
| CG13-P1-49 | 加入LICENSE | owner decision | 正式分发前完成 | S |
| CG13-P1-50 | NOTICE持续门禁 | release check | 新素材未登记则失败 | S |
| CG13-P1-51 | 名称/商标审查 | owner checklist | 商店/发行风险记录 | M |
| CG13-P1-52 | 存储协议封板记录 | ADR | P0关闭后冻结五类schema | S |

---

## 8.3 P2：维护性、桌面体验与可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG13-P2-01 | 拆分store.py | repositories/migrations | 行为不变 | XL |
| CG13-P2-02 | 拆分local_backend.py | workers/spool/status | 边界清晰 | XL |
| CG13-P2-03 | 拆分data_cli.py | archive/planner/executor | 可单独测试 | L |
| CG13-P2-04 | Data management service | service API | GUI不直接访问SQLite | L |
| CG13-P2-05 | 数据管理页面 | desktop UI | status/export/import/recovery | XL |
| CG13-P2-06 | 档案列表页 | UI | 新建/切换/重命名/查看进度 | L |
| CG13-P2-07 | Export-before-delete | profile flow | 删除可恢复 | L |
| CG13-P2-08 | Profile merge工具 | local UI | 冲突预览 | XL |
| CG13-P2-09 | GameState Enum | state model | 无散落魔法字符串 | L |
| CG13-P2-10 | AttemptSaveController | common layer | 五款保存控制统一 | XL |
| CG13-P2-11 | LocalStateController | common layer | progress/slot/settings统一 | L |
| CG13-P2-12 | InputManager | action map | 五款输入统一 | L |
| CG13-P2-13 | 完整IME控件 | widget | 光标/选择/组合输入 | M |
| CG13-P2-14 | 键位重映射 | settings UI | 冲突检测 | L |
| CG13-P2-15 | 键盘菜单导航 | focus model | 无鼠标完整操作 | L |
| CG13-P2-16 | 手柄支持 | controller | launcher/五款可用 | L |
| CG13-P2-17 | 音频系统 | BGM/SFX | 无设备不崩 | L |
| CG13-P2-18 | 设置页面 | UI | 窗口/音量/按键/辅助项 | L |
| CG13-P2-19 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG13-P2-20 | 高DPI | DPI handling | 图文清晰 | M |
| CG13-P2-21 | CJK字体fallback | licensed chain | 缺系统字体仍可读 | M |
| CG13-P2-22 | 色弱符号 | shape/pattern | 颜色非唯一信息 | L |
| CG13-P2-23 | 高对比/降低动态 | accessibility | 动画强度可调 | L |
| CG13-P2-24 | Clock/RNG注入 | deterministic services | seed+输入可重现 | L |
| CG13-P2-25 | 纯规则Engine渐进抽取 | rules modules | 核心测试无需SDL | XL |
| CG13-P2-26 | Launcher拆分 | app/state/render | main职责清晰 | L |
| CG13-P2-27 | 首页改为进度中心 | dashboard | best/recent/progress/continue | L |
| CG13-P2-28 | 静态Surface缓存 | profiler-driven | 有失效规则 | L |
| CG13-P2-29 | 可重复benchmark | CLI | 环境/seed/version完整 | M |
| CG13-P2-30 | 30–60分钟soak | stability suite | 线程/FD/内存稳定 | M |

---

## 8.4 P3：玩法内容与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG13-P3-01 | Tetris 7-bag | optional ruleset | 测试完整 | M |
| CG13-P3-02 | Tetris ghost/hold/lock delay | comfort features | 规则明确 | L |
| CG13-P3-03 | Snake速度/穿墙/障碍 | local modes | 最佳分开 | L |
| CG13-P3-04 | Snake双人同屏 | local multiplayer | 无网络 | L |
| CG13-P3-05 | 2048撤销 | undo model | 与attempt/slot一致 | L |
| CG13-P3-06 | 2048多存档槽 | save UI | 查看/继续/删除 | L |
| CG13-P3-07 | 2048棋盘尺寸 | modes | ruleset分离 | M |
| CG13-P3-08 | Sokoban正式选关 | progress UI | practice/campaign分开 | L |
| CG13-P3-09 | Sokoban星级/最佳推动 | metrics | 规则明确 | M |
| CG13-P3-10 | Sokoban死锁/提示 | analysis | 可关闭 | XL |
| CG13-P3-11 | Sokoban编辑器 | XSB import/export | 地图验证 | L |
| CG13-P3-12 | Zuma reaction FSM | game model | 连锁可属性测试 | L |
| CG13-P3-13 | Zuma训练/选关 | practice | 不混入通关 | L |
| CG13-P3-14 | Zuma色弱辅助 | symbols | 球色可辨认 | M |
| CG13-P3-15 | Zuma原创道具/轨道 | content | 确定性测试 | XL |
| CG13-P3-16 | Zuma轨道编辑器 | path tool | 版本化/预览 | L |
| CG13-P3-17 | 本机成就 | local achievements | 无账号/遥测 | L |
| CG13-P3-18 | 离线每日挑战 | date seed | 完全离线 | L |
| CG13-P3-19 | 本地replay | command log | 复盘/调试 | L |
| CG13-P3-20 | 中英文 | localization | 长文本布局测试 | L |
| CG13-P3-21 | Windows桌面包 | installer/portable | 无Python运行 | XL |
| CG13-P3-22 | macOS app bundle | package | 数据目录/签名smoke | XL |
| CG13-P3-23 | Linux package | AppImage/等效 | XDG正确 | L |
| CG13-P3-24 | 自动发布/签名/校验和 | release pipeline | smoke通过才发布 | L |

---

# 9. 必须新增的关键测试

## 9.1 Import transaction 与普通启动

```text
test_backend_startup_recovers_prepared_import
test_backend_startup_rolls_back_db_applied_import
test_backend_startup_rolls_back_files_published_import
test_corrupt_import_transaction_blocks_database_open
test_game_writes_cannot_occur_between_import_crash_and_recovery
```

## 9.2 Control path 与锁协议

```text
test_export_refuses_application_lock_path
test_export_refuses_maintenance_lock_path
test_export_refuses_legacy_pending_path
test_output_created_after_guard_is_not_clobbered_without_force
test_lock_path_replacement_cannot_create_two_inodes
test_lock_symlink_is_rejected_without_truncating_target
```

## 9.3 Recovery evidence

```text
test_export_never_follows_recovery_root_symlink
test_export_never_follows_nested_recovery_symlink
test_imported_recovery_alias_to_pending_is_rejected
test_imported_recovery_alias_to_data_parent_is_rejected
test_evidence_cannot_target_db_sidecar_or_control_lock
test_windows_exported_evidence_path_uses_forward_slashes
test_windows_recovery_round_trip
test_duplicate_evidence_path_conflict
```

## 9.4 Active protocol inventory

```text
test_complete_export_detects_reject_transaction
test_complete_export_detects_legacy_restore_file
test_complete_export_detects_pending_saves_json
test_replace_restore_removes_old_reject_transaction
test_replace_restore_prevents_legacy_pending_reimport
test_replace_restore_has_no_old_active_protocol_artifacts
```

## 9.5 Reject transaction

```text
test_reject_marker_is_published_atomically
test_crash_during_marker_write_keeps_previous_recoverable
test_corrupt_reject_marker_is_quarantined_and_reported
test_marker_hash_mismatch_does_not_execute
test_crash_at_every_reject_phase_recovers_previous
```

## 9.6 ImportTransaction内容

```text
test_staged_file_hash_mismatch_blocks_publish
test_before_file_hash_mismatch_blocks_rollback
test_rollback_database_hash_mismatch_requires_manual_recovery
test_duplicate_transaction_target_is_rejected
```

## 9.7 2048

```text
test_owner_claim_blocks_input_until_commit
test_owner_claim_failure_does_not_discard_postload_moves
test_close_with_slow_inflight_save_publishes_release_intent
test_release_timeout_remains_visible_and_recoverable
```

## 9.8 Optional Flask

```text
test_data_maintenance_is_blocked_while_flask_adapter_runs
test_flask_releases_application_lease_on_shutdown
```

---

# 10. 性能与稳定性门槛

1. pygame主线程不得执行：
   - SQLite；
   - journal scan；
   - archive I/O；
   - OS lock wait；
   - import recovery；
2. `submit_score_async()` enqueue p99 ≤2 ms；
3. state enqueue p99 ≤2 ms；
4. status getter p99 ≤0.2 ms；
5. 完整 archive 不遗漏任何 active protocol artifact；
6. Export 不跟随 symlink；
7. Import crash后普通 launcher不能打开部分状态；
8. Reject每个 crash point都可恢复 previous；
9. 2048 claim期间不接受输入；
10. 100次游戏切换：
    - 线程回基线；
    - FD不增长；
    - Surface/内存稳定；
11. 30–60分钟：
    - pending最终 committed/superseded/recovery/quarantine；
    - transaction dirs不无限增长；
12. 满盘、只读、坏 DB、坏 archive、坏 marker、坏 slot：
    - 游戏仍可安全启动或明确拒绝；
    - 最后有效副本不静默删除；
    - 有恢复入口。

---

# 11. 稳定版本质量门禁

正式稳定版本至少满足：

- 全部 P0关闭；
- 普通启动自动处理 unfinished ImportTransaction；
- control lock与legacy active paths不可被archive覆盖；
- recovery export/import无symlink越界；
- complete archive覆盖全部active protocol；
- replace restore后旧状态不会复活；
- reject marker原子且有hash；
- Windows recovery archive round-trip；
- 2048 owner claim ACK gate；
- 当前head三平台CI通过；
- main required checks开启；
- wheel/sdist smoke；
- storage/regression/stress可独立运行；
- coverage、lint、类型、依赖审计；
- LICENSE/NOTICE完成；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代：

- inode replacement test；
- symlink test；
- crash injection；
- unfinished import startup；
- restore-replace protocol inventory；
- Windows path round-trip。

---

# 12. 推荐实施顺序

## M0：关闭协议发布阻断

1. 普通启动 import recovery gate；
2. reserved control path；
3. recovery no-follow/allowlist；
4. active protocol inventory；
5. replace restore完整namespace；
6. reject marker原子发布；
7. Windows evidence path；
8. 2048 claim gate；
9. 对应故障测试。

## M1：恢复硬化

- transaction内容hash；
- manifest一致性；
- Flask lease；
- release durability；
- legacy table replace语义；
- recovery transaction UI；
- backup retention；
- recovery drill。

## M2：存储协议封板

冻结：

```text
score spool schema
state journal schema
state receipt schema
archive v2
import transaction schema
2048 slot schema
```

仅允许修确定性数据丢失、安全问题和已发布兼容问题。

## M3：桌面体验

- 档案页；
- 数据管理页；
- InputManager；
- 键位/手柄；
- 音频；
- DPI/字体；
- 可访问性；
- settings；
- 本机进度中心。

## M4：玩法与发行

- 五款游戏单机舒适功能；
- Sokoban/Zuma编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 自动发布。

---

# 13. 最终判断

本轮仍然是明显进步：

- 第十二次的 P0 不是“文档式修复”，而是进入了 phase transaction、snapshot、planner、rollback、validator 和测试；
- 当前 CI 已通过；
- 五款游戏主玩法未见新回退；
- 2048 的本地恢复成熟度已经很高；
- 测试分层继续合理，不需强制重写。

新发现的问题主要来自**恢复协议最后一公里**：

```text
CLI崩溃后普通应用先启动
锁文件被archive误当普通输出
symlink在数据目录内重定向
完整备份漏掉transaction artifact
replace恢复后旧marker复活
transaction marker自身半写
Windows archive路径不自洽
```

这些问题修复后，存储层应正式封板。

> **推荐停止增加数据功能，先完成 M0；之后把开发重心转向本机档案体验、可访问性、五款游戏内容和桌面发行。**
