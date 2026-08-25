# Classic Games Hub 第十五次代码审查与本地优先优化任务书

> 审查日期：2026-08-25
> 当前基线：`main` 分支 commit `4ada144e165b324a24bd2a474b89aa0e28a43394`（`4ada144`）
> 对比基线：第十四次审查 commit `9302416e3f650a35f08999c33cf5a77400b7c312`
> 增量提交：3 个；核心实现提交为 `d05ff83260726c39079414f56a80c050019dd39a`
> 当前包版本：`0.7.0`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 明确非目标：账号、公网排行、匹配、赛季、实时联机、反作弊、强制联网、云同步和默认遥测

---

## 0. 执行摘要

本轮修复总体有效，而且上一轮最重要的几个缺口确实进入了代码和测试：

- State reject protocol 升为 v3，在替换旧 winner 之前保存 `previous + incoming`；
- 完整有效的 reject temp 会尝试提升为正式 transaction；
- 2048 owner claim 不再仅依据 `ok=true`，而会核对权威 owner token/epoch；
- Sokoban campaign 与 practice 的 ledger、generation 和 progress key 已拆分；
- Archive 升为 v3，包含应用版本、reader contract、ruleset 目录和能力声明；
- 严格 manifest format 2 的 v2 archive 可以通过显式命令升级；
- 历史 ruleset 不再要求与当前 ruleset 字符串完全相同；
- ImportTransaction 使用同一批已校验 bytes 进行 publish/rollback；
- Transaction v1 默认停止自动恢复，多 transaction 不再猜测顺序；
- `restore-replace` 会清理未知 table/view/trigger/index，并比较当前 schema fingerprint；
- Score/state journal 根目录开始执行安全目录检查；
- Standalone 游戏、launcher 和可选 Flask 均使用统一恢复入口；
- 包版本已升至 `0.7.0`；
- 当前 head 的 GitHub Actions CI 已成功。

现有架构不需要推倒，也不应继续增加 journal、receipt 或 migration 层：

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
    ├── application lifetime lease
    ├── maintenance lock
    ├── Archive v3
    └── ImportTransaction

Optional Flask
    └── 仅作为本机成绩 API 调试适配器
```

### 当前四项发布阻断

1. **POSIX application lease 的 EX→SH `flock` 转换不具备代码所假设的原子保证。**
2. **缺失 `journal.json` 的 import transaction root 会被静默递归删除。**若 journal 是在 DB_APPLIED 后丢失，唯一 rollback image 会一并删除，应用随后打开部分导入数据库。
3. **State journal 的语义 `StoreError` 仍可绕过 journal 直接写 SQLite。**现有 journal 与数据库可能绑定同一 operation identity 的两个 payload。
4. **Import planner 对同一 state operation order、不同 payload 的冲突会静默保留目标 pending，丢弃 archive pending。**

### 当前结论

> **普通游戏、正常保存和正常恢复路径已经明显收敛。下一轮只应关闭上述协议不变量及关键兼容边界，然后正式冻结存储层。**

---

## 1. 审查方法、证据等级与限制

### 1.1 本轮已检查

- 当前 `main`、提交历史与完整 diff；
- `game_service/maintenance.py`
- `game_service/import_transaction.py`
- `game_service/data_cli.py`
- `game_service/local_backend.py`
- `game_service/store.py`
- `game_service/progress.py`
- `game_service/save_slot_validation.py`
- `game_service/catalog.py`
- `game_service/version.py`
- `server/app.py`
- `client/common/ui.py`
- `client/launcher.py`
- Tetris、Snake、2048、Sokoban、Zuma；
- storage v2–v12 tests；
- regression、stress、release runner；
- CI workflow、README、CHANGELOG、NOTICE、协议文档和任务记录；
- GitHub 当前 CI 与分支保护设置；
- Linux `flock(2)` 官方语义。

### 1.2 重点组合推演

```text
Import recovery
→ POSIX EX→SH conversion
→ 等待中的另一维护进程
→ 维护进程 DB_APPLIED 后崩溃

state outbox已有operation A
→ incoming B复用同order/identity
→ journal返回StoreError
→ backend仍调用apply_state_operation

archive pending B
→ target pending A
→ order完全相同、hash不同
→ planner选择A
→ import返回成功

ImportTransaction已DB_APPLIED
→ journal.json丢失
→ 普通启动执行recover
→ root被递归删除

state prepared reject marker
→ canonical target丢失/损坏
→ marker被当作已提交残留清理

campaign已有累计进度
→ 新关卡刚载入、moves=0
→ 进入practice
→ 无明确返回campaign路径
```

### 1.3 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题主路径与关键边界均已闭环 |
| **基本到位** | 原问题主体关闭，但组合边界仍有缺口 |
| **部分到位** | 有结构/API，但端到端不变量仍不成立 |
| **代码路径确定** | 可由当前控制流直接推出 |
| **系统调用契约** | 底层系统调用明确不保证代码依赖的性质 |
| **故障组合** | 需要崩溃、损坏、并发或升级组合 |
| **产品任务** | 不是当前 Bug，但符合本地单机定位 |

### 1.4 审查限制

本次环境没有本地完整 pygame/Flask 仓库运行环境，因此没有独立重跑全部测试。

可以确认：

- 当前 head 的 GitHub Actions run 已完成且结论为 success；
- 仓库记录 208 项 storage、107 项 gameplay，以及完整 stress/release profile；
- 上述数字属于项目自测与远端 CI 证据，不冒充当前容器独立执行。

---

## 2. 第十四次审查修复验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| application handoff | **部分到位** | 同一描述符转换已实现；POSIX `flock` 转换不保证原子 |
| reject prepare-before-replace | **主体到位** | previous 已先写 marker；prepared marker 异常状态仍有歧义 |
| valid reject temp | **基本到位** | 完整 temp 可提升；普通 journal temp 仍无恢复协议 |
| reject crash matrix | **明显改善** | 新增多阶段测试；缺 DB/target 损坏组合 |
| 2048 superseded claim | **修复到位** | 需要权威 owner token/epoch 才进入 ready |
| 2048 claim SaveState | **修复到位** | COMMITTED/SUPERSEDED/pending/recovery/failure均有转移 |
| Archive v2 upgrader | **修复到位** | 严格 format 2 可升级；format-less v2保持merge-only |
| historical ruleset | **主体到位** | manifest不要求版本值相同；行级validator仍非ruleset-specific |
| Archive app version | **修复到位** | package version进入manifest |
| transaction v1 policy | **基本到位** | 默认阻止；显式恢复未强制先保存证据 |
| verified bytes | **修复到位** | v2 publish/rollback使用已校验bytes |
| multiple transactions | **修复到位** | 默认停止，不猜rollback顺序 |
| Windows reparse lock | **明显改善** | WinAPI reparse检查；仍需持续真机与ACL测试 |
| secure journal roots | **部分到位** | 最终目录检查已有；DB symlink/canonical路径仍可分裂 |
| migrated legacy score inventory | **修复到位** | migrated evidence进入recovery清单 |
| invalid legacy restore | **修复到位** | 进入quarantine并提示 |
| fresh schema replace | **基本到位** | schema object可重建；仍依赖可初始化目标数据库 |
| Sokoban practice ledger | **修复到位** | campaign/practice数据与generation已分开 |
| practice unlock污染 | **修复到位** | practice不再提高campaign unlock |
| selector状态门禁 | **修复到位** | selector只在playing状态生效 |
| level-1 practice reset | **主体到位** | ledger保留；仍缺安全返回campaign路径 |
| standalone recovery UI | **修复到位** | launcher/单游戏共用恢复页 |
| `init_db` maintenance | **修复到位** | helper保留lease |
| SemVer | **修复到位** | `0.7.0` |
| required checks | **未完成** | main仍未保护 |
| hash/platform lock | **未完成** | 精确闭包已有，hash与三平台锁缺失 |
| LICENSE | **未完成** | NOTICE已有，许可证需权利人决定 |

---

# 3. 当前发布阻断问题

## 3.1 CG15-F01：POSIX `flock` EX→SH 转换不保证原子

- **优先级**：P0
- **证据**：系统调用契约
- **位置**：`InactiveApplicationLease.handoff`

当前 POSIX handoff：

```python
fcntl.flock(descriptor, fcntl.LOCK_SH)
```

代码和存储协议文档把它描述为“同一描述符原子降级”。

但是 Linux `flock(2)` 明确说明：

```text
shared/exclusive lock conversion is not guaranteed atomic
existing lock is removed first
another pending request may be granted
```

### 可发生顺序

```text
A 持 application EX，完成旧 transaction rollback
B 已等待 application EX

A 发起 EX→SH
→ 内核暂时移除 A 的 EX
→ B 获得 EX
→ B 开始 import
→ B 在 DB_APPLIED 后崩溃
→ B 的锁释放
→ A 最终获得 SH
→ A 未再次检查 transaction，打开部分导入 DB
```

当前测试只证明代码没有再次调用 `ApplicationSession.acquire()`，没有用真实等待者验证转换期间的调度。

### 修复要求

最小改法：

```text
exclusive recovery
→ handoff取得shared
→ 在shared lease仍持有时重新扫描 transaction roots
→ 若发现transaction：
     释放shared
     重新进入exclusive recovery
→ 无transaction才返回backend
```

更强方案是独立 startup transition gate，但不要再增加持久化格式。

---

## 3.2 CG15-F02：缺失 journal 的 transaction root 被静默删除

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`recover_import_transactions`

当前：

```text
root匹配 .<db>.import-*
→ journal.json不存在
→ shutil.rmtree(root)
→ 继续启动
```

代码注释假定：

```text
journal是prepare最后一步
没有journal就不可能开始DB事务
```

该推理只覆盖“正常崩溃在prepare完成前”的情况，不能覆盖：

- journal文件损坏后丢失；
- 用户/清理程序误删journal；
- 文件系统只丢失该目录项；
- DB_APPLIED后transaction root被部分破坏。

### 最坏结果

```text
DB已部分导入
rollback database仍在root
journal恰好缺失
→ 启动递归删除root和唯一rollback image
→ 应用打开部分导入DB
```

### 修复要求

区分两种目录：

```text
.<db>.preparing-*   # 尚未发布journal，可安全清理
.<db>.import-*      # 已发布transaction identity；journal缺失即recovery-required
```

或者为root增加独立、先于内容的prepare marker。

---

## 3.3 CG15-F03：State journal语义错误仍会绕过journal写SQLite

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `_publish_state_journal`
  - `_durable_state_write_locked`

当前 `_publish_state_journal()` 捕获所有：

```text
OSError
StoreError
```

并返回：

```text
receipt=None
journal_error=exc
```

`_durable_state_write_locked()` 随后仍然调用：

```python
apply_state_operation(journal_operation)
```

### 确定性场景

```text
现有journal A:
  revision=7
  operation_id=same
  payload=true

incoming B:
  revision=7
  operation_id=same
  payload=false

state_outbox.put(B)
→ state_operation_conflict

backend仍写SQLite B
→ DB=B
→ journal=A
```

如果DB尚无对应receipt，B可以提交；之后A replay才会因为同identity不同payload而失败。

### 修复要求

将journal错误分类：

#### 永久语义错误

```text
state_operation_conflict
state_key_conflict
state_ruleset_conflict
invalid_state_operation
value_too_large
```

必须：

- 不写SQLite；
- 不覆盖existing journal；
- incoming进入证据隔离或明确permanent failure。

#### I/O/临时错误

才允许：

- 直接尝试SQLite；
- 失败则进入non-durable memory fallback。

Score path已经按此原则处理 request-ID StoreError，state path应保持一致。

---

## 3.4 CG15-F04：Import planner静默丢弃同identity不同payload的state pending

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`data_cli._planned_file_operations`

当前：

```python
elif order(incoming) <= order(current):
    incoming = current
```

因此当：

```text
order(incoming) == order(current)
payload_hash(incoming) != payload_hash(current)
```

planner不会报冲突，而是保留target current。

### 结果

- Preview仍可能显示 `ok=true`；
- Import返回成功；
- Archive中唯一pending state被静默丢弃；
- 与正常 `PersistentStateOutbox.put()` 的409冲突语义不一致；
- 协议文档“任何identity conflict拒绝整次import”的声明不成立。

### 修复

planner必须复用一个共享函数：

```python
resolve_state_journal(existing, incoming)
```

规则：

```text
same order + same hash → exact duplicate
same order + different hash → conflict
incoming lower order → superseded并在preview中明确报告
incoming higher order → incoming wins
progress → component merge
```

---

# 4. 高优先级恢复与一致性问题

## 4.1 CG15-F05：Prepared reject marker在target缺失时会被直接删除

`_recover_prepared_reject()` 当前把以下状态解释为“DB已提交后残留marker”：

```text
current is None
```

并直接删除marker。

但 `current is None` 也可能来自：

- canonical文件被意外删除；
- canonical损坏后被另一路径隔离；
- 磁盘丢失target目录项；
- 尚未执行DB写入。

这时previous只存在于marker，删除marker会失去最后一份可恢复值。

### 改进

Prepared marker清理前至少核对：

```text
SQLite receipt/business row
incoming payload hash
operation identity
```

无法证明DB已提交时：

- 保留marker；
- 恢复previous；
- 或进入manual recovery。

---

## 4.2 CG15-F06：Replay无法完成reject时没有稳定状态

`_replay_state_entries_locked()` 对永久StoreError调用：

```text
reject_and_restore_if_current(...)
```

若其返回 `False`：

- 没有稳定event；
- 不一定标记blocked；
- journal继续留在active目录；
- 后续自动重试可能反复执行同一永久错误。

### 修复

区分：

```text
winner_changed → SUPERSEDED
filesystem recovery failed → RECOVERY_REQUIRED
marker corrupt → QUARANTINED
```

不得静默继续。

---

## 4.3 CG15-F07：普通score/state临时文件没有恢复和盘点协议

除 reject temp 外，仍可能留下：

```text
pending/.<request>.<uuid>.tmp
pending-state/.<digest>.json.<uuid>.tmp
pending-state/.state-clock.<uuid>.tmp
*.upgrade
```

这些文件可能包含完整且已fsync的最近成绩、进度或autosave，但：

- 正常startup不恢复；
- complete archive不盘点；
- `restore-replace`不统一清理；
- privacy replace后可能残留旧数据。

### 任务

建立“orphan temp”协议：

- 校验后promotion/merge；
- 冲突进入quarantine；
- complete archive遇未解决temp必须失败；
- replace清理或保存为evidence。

---

## 4.4 CG15-F08：Legacy `pending_saves.json`读取仍会跟随symlink

Legacy迁移使用：

```text
legacy.is_file()
legacy.stat()
legacy.read_text()
```

如果 `pending_saves.json` 是symlink：

- 可能读取数据目录外JSON；
- 外部内容可被导入成绩库；
- symlink本身随后被rename为migrated evidence。

应改为：

- `lstat`
- `O_NOFOLLOW`
- inode/fstat复核
- 单链接普通文件要求。

---

## 4.5 CG15-F09：DB文件symlink可使数据库与pending目录分裂

`recovered_application_session()`内部会resolve数据库路径，但 LocalBackend仍保留调用方传入的lexical path来创建：

```text
pending/
pending-state/
```

若数据库文件本身是symlink：

```text
/alias/games.db → /real/other.db
```

可能形成：

```text
lease / Data CLI：/real/other.db 与 /real/pending
LocalBackend DB：/alias/games.db
LocalBackend pending：/alias/pending
```

Data CLI可锁住正确数据库，却导出错误pending目录。

### 修复

- 初始化最开始只生成一个canonical database identity；
- Store、lease、outbox、Data CLI全部使用同一canonical parent/name；
- 或明确拒绝database-file symlink。

---

## 4.6 CG15-F10：Score outbox把所有StoreError当成request conflict

Score path目前对 `outbox.add_mutation()` 的任意 `StoreError` 直接返回，不再尝试DB。

但StoreError可能是：

- request ID真实冲突；
- existing pending文件损坏；
- spool lock timeout；
- journal结构错误。

损坏pending文件会永久阻塞一个本可由SQLite幂等恢复的有效成绩。

### 修复

- request conflict：稳定409；
- lock timeout：retryable；
- corrupt existing：保留原件，查询DB receipt/attempt，再决定修复或重新发布；
- 不把所有StoreError归为同一种永久决策。

---

## 4.7 CG15-F11：LocalBackend构造后续失败的lease释放依赖对象回收

ApplicationSession在构造早期取得，随后还会：

- 初始化Store；
- 验证pending目录；
- 创建workers；
- 扫描状态。

若后续步骤抛异常，构造函数没有显式：

```python
except:
    application_session.close()
    raise
```

CPython通常会通过对象析构最终释放，但不应把跨进程锁生命周期依赖于垃圾回收时机。

---

# 5. Archive与升级兼容问题

## 5.1 CG15-F12：Reader contract会让未来reader拒绝当前v3 archive

Exporter当前写：

```text
reader.min_version = 3
reader.max_version = 3
```

Validator使用：

```text
min <= 当前MANIFEST_FORMAT_VERSION <= max
```

未来若当前程序将manifest格式升到4，而仍希望兼容读v3：

```text
3 <= 4 <= 3 → false
```

今天生成的v3 archive会被未来reader拒绝。

### 正确模型

- 按archive自身format dispatch到独立validator；
- `reader`字段使用与manifest format不同的、单调reader capability version；
- future reader应能读取旧format，而不是拿自身当前format做等值比较。

---

## 5.2 CG15-F13：Ruleset目录要求游戏ID集合完全等于当前catalog

当前 `_valid_ruleset_catalog()` 要求：

```text
set(archive.rulesets) == set(current VALID_GAME_IDS)
```

未来：

- 新增第六款游戏；
- 移除一款游戏；
- 拆分或重命名游戏ID；

都会使旧archive整体不可读，即使五款旧数据本身完全兼容。

应允许：

```text
archive catalog是当前catalog的历史子集
未知历史game保留为legacy evidence
新增当前game不要求archive必须包含
```

---

## 5.3 CG15-F14：历史progress/slot仍用当前validator解释

Manifest已经允许历史ruleset字符串，但行级验证仍调用：

```text
validate_progress(game_id, key, value)
validate_save_slot_payload(game_id, state)
```

它们没有接收 `ruleset_version`。

未来规则变化时：

- 历史progress字段可能被当前validator拒绝；
- 历史slot可能被错误解释为当前结构；
- “历史ruleset可恢复”只在元数据层成立。

需要：

```python
validate_progress(game_id, ruleset_version, key, value)
validate_slot(game_id, ruleset_version, state_version, state)
```

以及历史adapter registry。

---

## 5.4 CG15-F15：`restore-replace`仍依赖可初始化的目标数据库

当前 replace流程先：

```python
LocalGameStore(database)
```

因此：

- 非SQLite文件；
- 严重corrupt DB；
- 无法repair的known table；
- newer unsupported schema；

会在创建rollback transaction前失败。

这限制了它作为真正灾难恢复工具的价值。

### 推荐架构

```text
构建全新current-schema DB
→ 导入archive
→ schema/integrity/fingerprint
→ exclusive locks内原子交换
→ 旧DB整体保留为rollback evidence
```

不需要再增加业务协议，只更换恢复执行方式。

---

## 5.5 CG15-F16：Transaction v1显式恢复未绑定证据确认

CLI建议先 `export-transaction`，再：

```text
recover-transactions --allow-legacy-v1
```

但代码并不要求提供刚导出的evidence hash。

用户可能直接恢复未认证before/staged bytes。

建议要求：

```text
--evidence <file>
--evidence-sha256 <hash>
```

或在交互式数据管理页中先生成证据，再解锁恢复动作。

---

## 5.6 CG15-F17：缺journal的transaction root与preparing root未分型

与P0 F02同根。Archive/status/cleanup也无法区分：

- prepare尚未完成的无害目录；
- journal丢失的危险事务；
- 用户手工创建的同名前缀目录。

应使用不同命名空间与root-level marker。

---

# 6. 2048专项剩余问题

## 6.1 CG15-F18：Claim权威检查未绑定slot revision/value hash

当前claim成功核对：

```text
owner_status
owner_token
owner_epoch
```

但没有核对：

```text
slot_revision
authoritative value_hash
```

同一个owner/epoch下若返回较旧状态，仍可能被视为成功。

建议claim提交时保存expected claimed revision/hash，并在ACK中完整核对。

---

## 6.2 CG15-F19：一个profile仍只有一个autosave槽

Owner协议防止静默覆盖，但产品仍只允许：

```text
profile + 2048 + autosave
```

这不是当前Bug，但必须明确：

1. 一个profile每次只允许一个活动2048窗口；或
2. 每attempt一个自动槽；或
3. 提供多槽UI。

保持完全本地即可。

---

## 6.3 CG15-F20：Claim失败/重载应保留可解释的本地状态证据

当前claim未证明时会重载远端slot，输入已经被门禁，因此不会丢移动，这是正确的。

仍建议记录：

- claim operation ID；
- remote owner/revision；
- reload原因；

用于本地日志与恢复页面，默认不上传。

---

# 7. Sokoban专项剩余问题

## 7.1 CG15-F21：进入practice可能无提示放弃进行中的campaign

当前进入practice的二次确认主要依据：

```text
self.moves > 0
```

场景：

```text
玩家完成第1关
→ 进入第2关，moves=0
→ 选择practice
→ 不要求确认
→ practice_mode=true
→ 当前实现没有“返回原campaign棋盘”的路径
```

Campaign ledger虽未清空，但当前run实际上无法继续。

### 修复方案

推荐彻底分离：

```text
CampaignSession
PracticeSession
```

或者在进入practice前保存：

- campaign level；
- player/boxes/history；
- score ledger；
- attempt context；

并提供“返回闯关”。

---

## 7.2 CG15-F22：Campaign/practice共用一个progress write Future

虽然load generation已分开，但仍共用：

```text
_progress_write_future
_progress_status_key
progress_save_message
```

交错写入时：

- 后一个Future覆盖前一个引用；
- 前一个错误/最终result不再被游戏层消费；
- 状态消息可能对应错误的key。

底层journal大多能保住数据，但UI和最终权威值可能延迟。

---

## 7.3 CG15-F23：Practice progress schema仍允许 `unlocked_level`

当前Sokoban validator对campaign/practice使用同一字段集合。

即使游戏不再写practice unlock，Import、direct store或旧客户端仍可写入：

```json
{"unlocked_level": 16}
```

建议practice schema只允许：

```text
completed_levels
level_scores
best_moves
best_pushes
```

---

## 7.4 CG15-F24：Practice overlay的主score仍显示campaign total

Practice通关不计入ranked total是正确的，但BaseGame overlay的主“得分”仍使用：

```text
self.total_score
```

可能出现：

```text
本关 +900
主得分 0
```

应显示“练习成绩”或隐藏ranked score行，避免误导。

---

# 8. 其他游戏与产品完善

## 8.1 Tetris

本轮确认：

- 7-bag主体正确；
- ghost落点计算正确；
- hold每个piece generation限一次；
- ruleset已分区；
- 未发现明显核心回退。

后续：

- lock delay；
- 可选strict rotation；
- hold区显示方块图形；
- seed replay；
- 规则页；
- 静态网格缓存。

## 8.2 Snake

当前核心稳定：

- 双转向queue；
- 180°门禁；
- 长停顿保护；
- 吃食物后速度更新；
- 尾格碰撞语义。

后续：

- RNG注入；
- 速度/穿墙/障碍；
- 双人同屏；
- 色弱纹理；
- 模式独立最佳。

## 8.3 Zuma

当前核心未见新回退：

- swept collision；
- 多pending reaction；
- 长帧余量；
- 路径连续；
- 进度合并。

后续：

- reaction FSM；
- RNG注入；
- `incoming` deque；
- 训练/选关；
- 属性测试；
- 原创道具与轨道编辑器。

---

# 9. 工程、仓库与发行问题

## 9.1 CG15-F25：Main仍未启用required checks

当前CI成功，但仓库设置仍允许绕过。

至少要求：

```text
release-gate
core-only
Ubuntu
macOS
Windows
```

## 9.2 CG15-F26：CI可复现性仍不完整

- workflow先无约束升级pip；
-普通平台test安装 `.[dev]` 时不使用release constraints；
- 没有 `--require-hashes`；
- 没有per-platform lock；
- build bootstrap仍可漂移。

## 9.3 CG15-F27：Coverage门槛仍为60%

当前已有大量高质量故障测试，但正式稳定版建议分阶段：

```text
storage/archive/import核心 ≥ 90%
全项目 ≥ 80%
```

不能用覆盖率替代并发、崩溃和升级fixture。

## 9.4 CG15-F28：尚无静态类型门禁

优先覆盖：

- service协议；
- StoreError/result contract；
- Archive manifest；
- ImportTransaction journal；
- Future/SaveState；
- game controllers。

## 9.5 CG15-F29：LICENSE仍缺失

NOTICE已有，但正式分发前需权利人确认：

- 代码权利；
- AI辅助代码政策；
- 名称/商标；
- 未来字体、图形、音效；
- 依赖许可。

## 9.6 CG15-F30：审查任务书重复留在仓库根目录

当前根目录仍有一份完整审查任务书，而 `docs/audits/` 已有正式归档体系。

建议：

- 根目录只保留README、CHANGELOG、CONTRIBUTING、SECURITY、NOTICE等项目入口；
- 审查材料只放 `docs/audits/`；
- 避免重复版本漂移。

## 9.7 CG15-F31：模块职责过于集中

重点文件已承担过多职责：

```text
local_backend.py
store.py
data_cli.py
ui.py
game_2048.py
zuma.py
```

拆分必须行为不变，不再借机改协议。

---

# 10. 明确不建议建设

- 注册登录；
- 云端账号；
- 公网排行榜；
- 匹配；
- 赛季；
- 实时联机；
- 反作弊；
- 服务端权威判定；
- 在线商城；
- 默认遥测；
- 云同步和多设备合并。

Flask继续只用于：

- 教学；
- 调试；
- 本机成绩API示例。

也不建议：

- 继续增加journal/receipt类型；
- 强制把unittest、pygame subprocess regression和stress统一成pytest；
- 将本地档案改成在线账号；
- 因极端恢复问题重写五款游戏规则。

---

# 11. 完整优化任务清单

## 优先级

- **P0**：可能打开部分恢复状态、让journal与DB分裂，或让Import静默丢pending。
- **P1**：恢复硬化、升级兼容、正式发行基础。
- **P2**：维护性、桌面体验、性能和可访问性。
- **P3**：玩法内容与三平台发行。
- **S/M/L/XL**：相对工作量。

---

## 11.1 P0：协议不变量

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG15-P0-01 | POSIX handoff后二次transaction检查 | startup loop | EX→SH间维护崩溃不打开部分DB | M |
| CG15-P0-02 | 真实等待者handoff测试 | multiprocess barrier | 不用mock模拟flock转换 | L |
| CG15-P0-03 | Linux/macOS handoff测试 | CI | 两类POSIX均覆盖 | M |
| CG15-P0-04 | `.preparing-*` 与 `.import-*`分型 | txn root protocol | 无journal import root不被静默删除 | L |
| CG15-P0-05 | Missing-journal recovery-required | startup/CLI | rollback evidence保留 | M |
| CG15-P0-06 | State journal error分类 | backend | 语义StoreError不写SQLite | M |
| CG15-P0-07 | Journal conflict不落库测试 | fixture | DB空时也不能提交冲突payload | M |
| CG15-P0-08 | Import state conflict共享resolver | planner | same order/diff hash稳定409 | M |
| CG15-P0-09 | Preview silent-drop回归 | import test | archive pending不静默丢失 | S |
| CG15-P0-10 | Planner/live outbox语义一致测试 | contract test | duplicate/superseded/conflict一致 | M |
| CG15-P0-11 | P0 fault matrix | release tests | handoff/journal/import均有确定性用例 | L |
| CG15-P0-12 | P0 release gate | CI | 任一失败禁止release | S |

---

## 11.2 P1：恢复、兼容与发行基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG15-P1-01 | Prepared reject核对DB receipt | reject recovery | target缺失时不盲删marker | L |
| CG15-P1-02 | Reject recovery三态 | state machine | committed/recover/ambiguous明确 | M |
| CG15-P1-03 | Replay reject failure event | LocalStateEvent | false返回不静默 | S |
| CG15-P1-04 | Replay永久错误backoff | retry policy | 不无限热重试 | S |
| CG15-P1-05 | Score orphan temp恢复 | spool scanner | valid temp可promote | M |
| CG15-P1-06 | State orphan temp恢复 | journal scanner | valid temp可merge/promote | L |
| CG15-P1-07 | Orphan temp inventory | archive/replace | complete备份与privacy replace覆盖 | M |
| CG15-P1-08 | Legacy pending no-follow | migration | symlink不被读取 | M |
| CG15-P1-09 | Legacy corrupt target修复 | score outbox | 保留证据后可继续有效成绩 | M |
| CG15-P1-10 | Score StoreError分类 | backend | conflict/lock/corrupt语义分开 | M |
| CG15-P1-11 | Canonical DB identity | bootstrap | DB/lease/pending/CLI同一路径 | L |
| CG15-P1-12 | DB symlink策略 | docs/code | 拒绝或canonicalize | M |
| CG15-P1-13 | Custom outbox边界 | API docs | 明确仅测试/高级调用 | S |
| CG15-P1-14 | Constructor显式释放lease | lifecycle | 后续初始化失败无锁泄漏 | S |
| CG15-P1-15 | Secure parent chain | filesystem policy | pending parent alias有明确策略 | M |
| CG15-P1-16 | Windows directory reparse检查 | secure roots | junction不能重定向outbox | L |
| CG15-P1-17 | Control hard-link检查 | lock opener | 多链接lock拒绝或安全处理 | M |
| CG15-P1-18 | Archive per-format validator | reader registry | future reader可读旧v3 | L |
| CG15-P1-19 | Reader version语义重设计 | Archive v4 ADR | 不与manifest format混用 | M |
| CG15-P1-20 | Catalog子集兼容 | manifest policy | 新增游戏不破坏旧archive | M |
| CG15-P1-21 | Removed-game legacy策略 | archive/import | 历史数据可保留 | M |
| CG15-P1-22 | Ruleset-aware progress validator | adapter registry | 历史progress可解释 | L |
| CG15-P1-23 | Ruleset-aware slot validator | adapter registry | 历史slot可解释 | L |
| CG15-P1-24 | Unknown historical ruleset policy | ADR/UI | preserve/reject行为明确 | S |
| CG15-P1-25 | Fresh-DB replace restore | restore engine | corrupt target也能恢复 | XL |
| CG15-P1-26 | Atomic DB swap | restore executor | rollback image可恢复 | L |
| CG15-P1-27 | V1 evidence-bound recovery | CLI | 未导出证据不能apply | M |
| CG15-P1-28 | State transaction证据CLI | data tool | marker可list/export/retry | L |
| CG15-P1-29 | Claim ACK绑定revision/hash | 2048 controller | owner与slot版本均权威 | M |
| CG15-P1-30 | Claim/close矩阵 | 2048 tests | pending/recovery/close全覆盖 | M |
| CG15-P1-31 | 2048活动槽策略 | ADR | 单实例/多槽明确 | S |
| CG15-P1-32 | Sokoban独立PracticeSession | game model | 不放弃campaign状态 | L |
| CG15-P1-33 | Campaign返回入口 | UI | 可安全返回原闯关 | M |
| CG15-P1-34 | Campaign放弃确认 | UX | 任一累计进度均提示 | S |
| CG15-P1-35 | Progress write Future按key分离 | controller | campaign/practice互不覆盖 | M |
| CG15-P1-36 | Practice schema收紧 | validator | 不接受unlocked_level | S |
| CG15-P1-37 | Practice overlay语义 | UI | 显示练习成绩而非ranked total | S |
| CG15-P1-38 | Main required checks | repo settings | main不可绕过CI | S |
| CG15-P1-39 | Release branch/tag policy | governance | release只来自受保护commit | S |
| CG15-P1-40 | Per-platform lock files | dependencies | 三平台解析可复现 | L |
| CG15-P1-41 | Hash-locked installs | release | 支持require-hashes | M |
| CG15-P1-42 | Pin pip/build bootstrap | workflow | CI工具链可审计 | S |
| CG15-P1-43 | Dev matrix constraints | CI | 测试依赖不随机漂移 | M |
| CG15-P1-44 | Static type checking | pyright/mypy | service/store/archive边界通过 | L |
| CG15-P1-45 | Storage coverage 90%路线 | CI | 核心模块分阶段提升 | M |
| CG15-P1-46 | State/import model tests | model suite | CAS/merge/conflict不变量 | L |
| CG15-P1-47 | Recovery drill | release | export→replace→replay闭环 | L |
| CG15-P1-48 | Structured recovery logs | logging | transaction/operation ID可追踪 | M |
| CG15-P1-49 | LICENSE权利核对 | owner checklist | 可安全选许可证 | M |
| CG15-P1-50 | 加入LICENSE | owner decision | 正式分发前完成 | S |
| CG15-P1-51 | NOTICE素材门禁 | release check | 新素材未登记即失败 | S |
| CG15-P1-52 | 名称/商标审查 | owner checklist | 发行风险有记录 | M |
| CG15-P1-53 | 存储协议封板ADR | architecture | P0关闭后冻结协议 | S |

---

## 11.3 P2：维护性、桌面体验与可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG15-P2-01 | 拆分local_backend.py | spool/state/workers/status | 行为不变 | XL |
| CG15-P2-02 | 拆分store.py | repositories/migrations | 行为不变 | XL |
| CG15-P2-03 | 拆分data_cli.py | archive/planner/executor | 可独测 | L |
| CG15-P2-04 | DataManagementService | service API | GUI不直接操作SQLite | L |
| CG15-P2-05 | 数据管理页 | desktop UI | backup/import/recovery/cleanup | XL |
| CG15-P2-06 | Transaction恢复页 | UI | list/export/recover | L |
| CG15-P2-07 | 档案列表页 | profile UI | 新建/切换/重命名/进度 | L |
| CG15-P2-08 | Export-before-delete | profile flow | 删除可恢复 | L |
| CG15-P2-09 | Profile merge工具 | local UI | 冲突预览 | XL |
| CG15-P2-10 | GameState Enum | state model | 减少magic string | L |
| CG15-P2-11 | AttemptSaveController | common layer | 五款保存控制统一 | XL |
| CG15-P2-12 | LocalStateController | common layer | progress/slot/settings统一 | L |
| CG15-P2-13 | InputManager | action map | 五款输入统一 | L |
| CG15-P2-14 | 完整IME控件 | widget | 光标/选择/组合输入 | M |
| CG15-P2-15 | 键位重映射 | settings UI | 冲突检测 | L |
| CG15-P2-16 | 全键盘菜单 | focus model | 无鼠标完整操作 | L |
| CG15-P2-17 | 手柄支持 | controller | launcher/五款可用 | L |
| CG15-P2-18 | 音频系统 | BGM/SFX | 无设备安全降级 | L |
| CG15-P2-19 | 设置页面 | UI | 音量/窗口/按键/辅助项 | L |
| CG15-P2-20 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG15-P2-21 | 高DPI | DPI handling | 图文清晰 | M |
| CG15-P2-22 | CJK字体fallback | licensed chain | 缺系统字体仍可读 | M |
| CG15-P2-23 | 色弱图案 | patterns/shapes | 颜色非唯一信息 | L |
| CG15-P2-24 | 高对比/降低动态 | accessibility | 动画强度可调 | L |
| CG15-P2-25 | Clock/RNG统一注入 | deterministic services | seed+输入可复现 | L |
| CG15-P2-26 | 纯规则Engine抽取 | rules modules | 核心测试无需SDL | XL |
| CG15-P2-27 | Launcher拆分 | app/state/render | main职责清晰 | L |
| CG15-P2-28 | 首页改为进度中心 | dashboard | best/recent/progress/continue | L |
| CG15-P2-29 | 静态Surface缓存 | profiler-driven | 有失效规则 | L |
| CG15-P2-30 | Benchmark CLI | benchmark | OS/版本/seed完整 | M |
| CG15-P2-31 | 30–60分钟soak | stability suite | 线程/FD/内存稳定 | M |
| CG15-P2-32 | 审查文档归档 | repo hygiene | 根目录无重复taskbook | S |

---

## 11.4 P3：玩法内容与三平台发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG15-P3-01 | Tetris lock delay | optional ruleset | 测试完整 | M |
| CG15-P3-02 | Tetris strict rotation | optional mode | 与assist分榜 | M |
| CG15-P3-03 | Tetris规则页/hold图形 | UI | 规则可查看 | S |
| CG15-P3-04 | Snake速度/穿墙/障碍 | local modes | 最佳分开 | L |
| CG15-P3-05 | Snake双人同屏 | local multiplayer | 无网络 | L |
| CG15-P3-06 | 2048撤销 | undo model | 与attempt/slot一致 | L |
| CG15-P3-07 | 2048多存档槽 | save UI | 查看/继续/删除 | L |
| CG15-P3-08 | 2048棋盘尺寸 | modes | ruleset分离 | M |
| CG15-P3-09 | Sokoban正式选关UI | progress UI | campaign/practice清楚 | L |
| CG15-P3-10 | Sokoban星级/最佳推动 | metrics | 规则明确 | M |
| CG15-P3-11 | Sokoban死锁检测/提示 | analysis | 可关闭 | XL |
| CG15-P3-12 | Sokoban编辑器 | XSB import/export | 地图验证 | L |
| CG15-P3-13 | Zuma reaction FSM | game model | 连锁可属性测试 | L |
| CG15-P3-14 | Zuma训练/选关 | practice | 不混入通关 | L |
| CG15-P3-15 | Zuma原创道具/轨道 | content | 确定性测试 | XL |
| CG15-P3-16 | Zuma轨道编辑器 | path tool | 版本化/预览 | L |
| CG15-P3-17 | 本机成就 | local achievements | 无账号/遥测 | L |
| CG15-P3-18 | 离线每日挑战 | date seed | 完全离线 | L |
| CG15-P3-19 | 本地replay | command log | 复盘/调试 | L |
| CG15-P3-20 | 中英文 | localization | 长文本布局测试 | L |
| CG15-P3-21 | Windows桌面包 | installer/portable | 无Python运行 | XL |
| CG15-P3-22 | macOS app bundle | package | 数据目录/签名smoke | XL |
| CG15-P3-23 | Linux package | AppImage/等效 | XDG正确 | L |
| CG15-P3-24 | 自动发布/签名/校验和 | release pipeline | smoke通过才发布 | L |

---

# 12. 必须新增的关键测试

## 12.1 POSIX handoff

```text
test_flock_downgrade_waiter_can_run_before_shared_acquisition
test_handoff_rechecks_transaction_after_waiting_importer_crashes
test_handoff_accepts_completed_import_without_rollback
test_macos_handoff_waiter_matrix
```

## 12.2 Import root

```text
test_missing_journal_import_root_blocks_startup
test_preparing_root_without_journal_can_be_cleaned
test_missing_journal_preserves_database_before_image
test_status_reports_orphan_transaction_root
```

## 12.3 State journal error classification

```text
test_same_identity_different_payload_never_reaches_store
test_state_key_conflict_does_not_write_database
test_state_ruleset_conflict_does_not_write_database
test_io_journal_failure_may_fall_back_to_database
test_semantic_journal_failure_is_permanent_and_preserves_winner
```

## 12.4 Import planner

```text
test_import_same_order_different_state_hash_is_conflict
test_import_same_order_same_hash_is_duplicate
test_import_lower_state_order_reports_superseded
test_planner_and_outbox_use_identical_resolution_function
```

## 12.5 Reject recovery

```text
test_prepared_marker_missing_target_checks_database_receipt
test_prepared_marker_corrupt_target_preserves_previous
test_replay_reject_failure_emits_recovery_required
test_unresolved_reject_does_not_hot_loop
```

## 12.6 Temp recovery

```text
test_score_temp_after_fsync_is_promoted
test_state_temp_after_fsync_is_promoted_or_merged
test_complete_export_detects_orphan_temp
test_replace_removes_or_preserves_temp_as_evidence
```

## 12.7 Path identity

```text
test_database_file_symlink_does_not_split_pending_directory
test_legacy_pending_symlink_is_not_followed
test_constructor_failure_releases_application_session
test_pending_parent_junction_policy
```

## 12.8 Archive future compatibility

```text
test_new_reader_can_dispatch_old_manifest_v3
test_added_game_does_not_invalidate_old_archive
test_removed_game_rows_are_preserved_as_legacy
test_historical_progress_uses_ruleset_specific_validator
test_historical_slot_uses_ruleset_specific_validator
test_replace_restore_works_over_corrupt_target_database
```

## 12.9 Sokoban

```text
test_entering_practice_after_campaign_progress_requires_confirmation
test_practice_can_return_to_campaign_board
test_campaign_and_practice_write_futures_do_not_overwrite
test_practice_progress_rejects_unlocked_level
test_practice_overlay_displays_practice_score
```

---

# 13. 性能与稳定性门槛

1. pygame主线程不得执行：
   - SQLite；
   - journal扫描；
   - archive I/O；
   - lock wait；
   - transaction recovery；
2. score/state enqueue p99 ≤2 ms；
3. status getter p99 ≤0.2 ms；
4. POSIX handoff任意合法调度顺序都不打开部分DB；
5. journal语义冲突永远不写SQLite；
6. Preview成功不得静默丢archive pending；
7. target/journal损坏时previous最后副本不被删除；
8. Archive跨新增游戏和ruleset升级仍可读取历史；
9. 100次游戏切换：
   - 线程回到基线；
   - FD不增长；
   - Surface/内存稳定；
10. 30–60分钟：
    - pending最终收敛；
    - transaction/temp不无限增长；
11. 满盘、只读、坏DB、坏archive、坏transaction：
    - 安全拒绝或恢复；
    - 有用户可见入口；
    - 不静默删除最后副本。

---

# 14. 稳定版本质量门禁

正式稳定版本至少满足：

- 全部P0关闭；
- POSIX handoff不依赖非原子转换假设；
- missing-journal root不会被静默删除；
- state journal语义错误不绕过journal；
- Import不会静默丢same-identity pending；
- reject ambiguity有DB证据判断；
- Archive reader可跨未来catalog/ruleset演进；
- corrupt目标数据库可以replace restore；
- Sokoban practice不会放弃campaign而无提示；
- 当前head三平台CI通过；
- main required checks开启；
- wheel/sdist smoke；
- type/lint/coverage/依赖审计；
- LICENSE/NOTICE完成；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代：

- real multiprocess waiter；
- crash injection；
- journal/store conflict fixture；
- corrupt transaction fixture；
- archive evolution fixture；
- 真实恢复演练。

---

# 15. 推荐实施顺序

## M0：关闭四项协议阻断

1. POSIX handoff二次检查；
2. transaction root分型；
3. state journal错误分类；
4. Import state resolver；
5. 对应multiprocess/fault tests。

## M1：恢复与兼容封板

- reject receipt核对；
- temp recovery；
- canonical DB identity；
- archive per-format reader；
- catalog/ruleset adapter；
- fresh-DB replace；
- Sokoban PracticeSession。

## M2：冻结存储协议

冻结：

```text
score spool
state journal
state receipt
Archive v3
ImportTransaction
2048 slot schema
```

此后只允许：

- 确定性数据丢失修复；
- 安全修复；
- 已发布格式兼容修复。

## M3：桌面体验

- 数据管理页；
- 档案页；
- InputManager；
- 键位/手柄；
- 音频；
- DPI/字体；
- 可访问性；
- 设置页；
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

# 16. 最终判断

本轮仍是明显进步：

- Reject v3、2048 claim、Archive v3和Sokoban ledger分离都是真实实现；
- 当前远端CI成功；
- 五款游戏核心玩法未发现大面积回退；
- Tetris新增7-bag/ghost/hold主体正确；
- 本机数据工具已经具有较成熟的事务、验证和证据意识。

剩余严重问题已经明显收敛到少数协议不变量：

```text
POSIX锁转换并非原子
缺journal的transaction root被删除
journal语义冲突仍可绕过journal写DB
Import planner静默丢同identity pending
```

这些完成后，不应继续扩张数据层。

> **下一步只做M0与关键M1；随后把开发重心转向本机档案体验、可访问性、五款游戏内容和三平台桌面发行。**
