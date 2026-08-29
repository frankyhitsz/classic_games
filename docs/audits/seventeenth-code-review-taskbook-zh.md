# Classic Games Hub 第十七次代码审查与本地优先优化任务书

> 审查日期：2026-08-29
> 当前基线：`main` 分支 commit `4e723c7c362d6959b820e70baf7fd5c54978576a`（`4e723c7`）
> 上一轮基线：`d8808d3ae59e7e3ddd0b0ac90b797c9ca450614e`
> 本轮增量：3 个提交；主要功能提交为 `f02d3e5385558535af2cfcc550977b7dca6a9377`
> 当前包版本：`0.8.0`
> Python 声明范围：`>=3.11,<3.14`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 明确非目标：账号体系、公网排行、匹配、赛季、实时联机、云同步、反作弊、在线商城、强制联网和默认遥测

---

# 0. 执行摘要

本轮修复不是“只加测试或只改文档”。上一轮指出的两个确定性 P0：

1. score spool 的 hard-link 发布窗口与 single-link reader 冲突；
2. `set_progress` 被错误当作空 component merge；

都已经在生产代码中获得针对性处理：

- score canonical 改为 request lock 内 `os.replace`，发布后用正式 no-follow reader 重新验证；
- score scanner、remove、quarantine、retry count 等操作开始复用 request lock；
- progress resolver 增加 `set/set`、`merge/set`、`set/merge`、`merge/merge` 方法矩阵；
- 普通 score/state orphan temp 增加锁后 fingerprint 复查；
- lock timeout 不再在普通 orphan 流程中直接隔离 temp；
- 损坏 canonical 会先保存到 quarantine，隔离失败时停止覆盖；
- state clock 改用 bounded no-follow reader；
- `LocalStateEvent` 增加 `operation_id` 和 `payload_hash`；
- raw database fallback 在存在 WAL/SHM/journal 时停止自动恢复；
- export 默认成为 snapshot-only，并新增 `inspect-archive`；
- 2048 支持 RNG 注入，final slot intent 返回 resolver 结果；
- Sokoban 增加 per-key single-flight progress 和 durable practice-return slot；
- control lock file 明确拒绝 hard link；
- build-system、Python 版本范围和非 loopback API 启动边界进一步收紧。

因此：

> **上一轮修复整体有效，工程质量继续提高。**

但是，当前还不能冻结持久化协议或直接认定为稳定版。新的组合审查发现了五个发布阻断族：

1. **Progress aggregate 没有把自身 component 视为已吸收操作；原 `set_progress` 重放可能覆盖 aggregate，丢失已合并进度。**
2. **State reject/restore/list recovery 对 BUSY 的处理仍不一致；合法 marker 或 journal 可能因锁竞争被隔离。**
3. **Archive exporter 与 reader 使用不一致的 JSON node 预算；数千条记录即可生成“成功导出但本程序无法再读取”的备份。**
4. **Terminal ImportTransaction 使用 `rmtree(ignore_errors=True)`；部分删除可能留下缺 `journal.json` 的 `.import-*`，从而阻断以后启动。**
5. **2048 在滑移动画尚未结算时退出，会保存“分数已增加、棋盘合并尚未完成”的不一致自动存档。**

此外还有一批 P1：

- SQLite 已提交后，journal cleanup 的新锁异常可能把成功保存误报成 worker failure；
- state journal 的合法性依赖当前 wall clock；
- historical pending 是 parse-first，而不是先按 ruleset 分类；
- `inspect-archive` 的 no-follow 与“完整验证”语义尚未真正成立；
- terminal transaction classifier 与 export active inventory 不一致；
- per-request score lock file 永久累积；
- Sokoban durable session 的 attempt identity、ruleset、tombstone 生命周期和验证仍不完整；
- recovery completeness 把 active data 与所有 forensic evidence 绑定过紧；
- 大型 recovery file 既无法嵌入完整 archive，也无法满足 cleanup proof；
- imported recovery evidence 会在多轮导入导出中嵌套增长；
- worker 未完全退出时 application lease 仍会被释放；
- branch protection、hash lock、类型门禁、LICENSE 仍未完成。

### 当前建议

下一轮不要继续扩展新的 journal、receipt 或 restore 格式。应按以下顺序收口：

```text
M0：关闭五个发布阻断族
M1：统一持久化、恢复、archive 和 shutdown 契约
M2：冻结现有协议
M3：转向本机数据管理、可访问性和桌面体验
M4：增加单机玩法内容与三平台发行
```

---

# 1. 审查范围、方法与限制

## 1.1 锁定的版本

本任务书只针对：

```text
4e723c7c362d6959b820e70baf7fd5c54978576a
```

后续提交可能已经改变结论；实施任务时应继续把该 SHA 写入测试与验收记录。

## 1.2 重点检查范围

- `game_service/local_backend.py`
- `game_service/store.py`
- `game_service/data_cli.py`
- `game_service/import_transaction.py`
- `game_service/maintenance.py`
- `game_service/service.py`
- `game_service/mutation.py`
- `game_service/progress.py`
- `game_service/save_slot_validation.py`
- `client/common/ui.py`
- `client/launcher.py`
- `client/games/game_2048.py`
- `client/games/sokoban.py`
- Tetris、Snake、Zuma 的回归边界
- `tests/test_storage_v14.py`
- storage、gameplay、stress、release runner
- CI、packaging、README、NOTICE、安全与审查文档
- GitHub Actions 和 main branch protection 状态

## 1.3 方法

本轮使用：

- 当前 SHA 的逐文件控制流审查；
- 与上一轮基线的 commit/diff 对比；
- journal、receipt、SQLite、temp、marker、transaction root 的状态机推演；
- 多进程锁竞争顺序推演；
- Archive export → load → preview → import/replace 的闭环推演；
- 2048 动画状态与自动存档状态的逐帧推演；
- Sokoban progress/session/score identity 的跨重启推演；
- 当前 GitHub Actions 结果核验；
- 现有测试覆盖与缺失故障点对照。

## 1.4 限制

当前执行环境无法通过网络克隆仓库，也没有在本地重新运行完整 pygame、Flask、storage、stress 和 release profile。

可确认的是：

- 当前代码与测试文件已经从 GitHub 当前 SHA 读取；
- 当前 head 的 GitHub Actions 已成功；
- 260 项 storage、107 项 gameplay、stress 和 release 数字来自仓库自己的验证记录；
- 本任务书中的“确定性问题”来自当前控制流和状态模型，不等同于本环境独立执行结果；
- 需要 fault injection 或 Windows 专项验证的项目会明确标记。

---

# 2. 当前架构判断

现有方向仍然适合本地小游戏，不需要改成联网平台：

```text
Launcher / pygame games
    │
    ├── GameDataService
    ├── read worker
    └── write worker
            ├── SQLite
            ├── immutable score pending
            └── keyed local-state pending

Data maintenance
    ├── application lifetime lease
    ├── transition gate
    ├── maintenance lock
    ├── Archive v3
    ├── ImportTransaction
    └── recovery evidence

Optional Flask
    └── 本机调试适配器，不是产品主架构
```

正确的后续方向是：

- 把现有本机数据协议收口；
- 给这些能力加桌面管理界面；
- 把玩法逻辑做得可测试、可重放；
- 提升输入、音频、DPI、字体和无障碍；
- 做三平台本机发行。

不需要：

- 云账号；
- 公网排行榜；
- 匹配与房间；
- 服务端权威逻辑；
- 反作弊；
- 云存档；
- 默认遥测。

---

# 3. 上一轮修复验收矩阵

| 上一轮主题 | 当前状态 | 本轮结论 |
|---|---|---|
| Score hard-link publication | **修复到位** | canonical 改为 request lock 内 replace，发布后重新 no-follow 验证 |
| Score canonical 操作统一 request lock | **主体到位** | scanner/remove/quarantine/retry 已统一；调用方异常契约仍未同步 |
| `set_progress` 空 component merge | **主体修复** | method matrix 已加入；aggregate component 重放和 v1 upgrade 仍有缺口 |
| Ordinary orphan lock timeout | **修复到位** | 普通 score/state temp 在 timeout 时保留 |
| Orphan fingerprint stability | **修复到位** | lock 后复查 dev/inode/size/mtime |
| Corrupt canonical promotion | **修复到位** | 先隔离损坏 current，再提升有效 temp |
| Quarantine failure overwrite | **修复到位** | state current 隔离失败会停止 |
| State clock no-follow | **主体到位** | 正式 clock read 已 bounded no-follow；clock recovery 失败反馈仍不足 |
| State future timestamp | **修复方式不完整** | 直接把“相对当前时间”写入 parser validity，产生新的时间回拨问题 |
| LocalStateEvent identity | **主体到位** | 加入 operation ID/hash；同 identity 的 state reducer 仍可回退 |
| Terminal transaction root classifier | **主体到位** | valid terminal root 不算 active；partial recursive deletion 仍可能制造 unknown root |
| Unsafe transaction root type | **修复到位** | regular file/reparse 会要求恢复 |
| Raw fallback sidecar | **基本到位** | 预检查存在 sidecar 时停止自动 raw rollback |
| Fresh replace staging inventory | **到位** | `.fresh-replace-*` 纳入 recovery inventory |
| Disk-space preflight | **到位但为估算** | 会在 staging 前拒绝；估算模型仍可改进 |
| Reserved export prefixes | **修复到位** | 不存在的 nested reserved path 也拒绝 |
| Snapshot-only export | **基本到位** | 默认不主动 repair；active classifier 与 terminal root 仍不一致 |
| Pure archive inspect | **部分到位** | 不打开目标 DB；final symlink 和深层语义验证仍有缺口 |
| Bounded archive reader | **主体到位** | no-follow bounded read 已有；inspect 入口先 resolve 会绕开 final no-follow |
| Historical pending evidence-only | **部分到位** | 仅 parse 失败后才转 evidence；已知游戏旧 ruleset 仍可被激活 |
| Canonical database path | **修复到位** | Store/LocalBackend 路径 canonical，prebuilt mismatch 拒绝 |
| Constructor cleanup | **修复到位** | 两个 worker 创建失败均释放已有资源 |
| Slot intent resolution | **修复到位** | 返回 requested/winner identity 和 resolution |
| 2048 RNG injection | **修复到位** | 生成路径使用注入 RNG；RNG state 尚未持久化 |
| Sokoban progress single-flight | **主体到位** | 每个 key 保留一个 in-flight + 最新 queued snapshot |
| Sokoban durable campaign session | **部分到位** | durable slot 已有；identity/ruleset/tombstone/validation 仍需收口 |
| Control-file hard link | **修复到位** | `st_nlink != 1` 拒绝 |
| Non-loopback debug API | **CLI 主路径到位** | 非 loopback 需显式 unsafe；WSGI/app factory 边界仍需文档或额外保护 |
| Build-system / Python 范围 | **修复到位** | setuptools 精确固定，Python 声明收窄到 3.11–3.13 |
| Branch protection / hash lock / LICENSE | **未完成** | 仍属于正式发行前门禁 |

---

# 4. 当前发布阻断问题

## CG17-F01：Progress aggregate 会被自身 set component 重放覆盖

- **级别**：P0
- **证据**：确定性代码路径
- **涉及**：
  - `PersistentStateOutbox._merge_progress_operations`
  - `PersistentStateOutbox.resolve_operations`
  - `LocalGameStore.apply_state_operation`
  - `state_merge_receipts`
  - orphan/import/reject replay

### 当前结构

`set + merge` 会生成：

```text
method = merge_progress
operation_id = aggregate-<component digest>
logical_revision = max(set revision, merge revision)
components = [
    original set operation ID/hash,
    original merge operation ID/hash
]
```

但当已有 aggregate 后，原 set operation 再出现时，resolver 走：

```text
(existing merge, incoming set)
→ 只比较 (logical_revision, operation_id)
```

它没有先检查：

```text
incoming operation ID/hash 是否已经在 existing.components 中
```

### 可复现顺序

```text
set A: revision=10, operation_id=f...
merge B: revision=5
→ aggregate: revision=10, operation_id=aggregate-...

原 set A 通过 orphan/import/recovery 再出现
→ (10, "f...") > (10, "aggregate-...")
→ set A 赢得 LWW
→ B 已合并的进度被覆盖
```

随机 hex operation ID 以 `b`–`f` 开头时就可能大于 `aggregate-*`。

更重要的是，Store 只有在 incoming method 为 `merge_progress` 时检查 `state_merge_receipts`。原 set operation 直接 replay 时不会检查自己是否已经被 aggregate 吸收。

### 影响

- 已完成关卡、最佳成绩或 unlock 可能回退；
- Live outbox、orphan recovery、archive import 和 DB receipt 语义不一致；
- “monotonic progress”不变量并未真正成立。

### 正确修复

1. Resolver 在任何 LWW 前先做 component membership：
   - ID/hash 已在 aggregate → duplicate/superseded；
   - 同 ID、不同 hash → conflict；
2. Store 对 incoming set 同样查询 `state_merge_receipts`；
3. aggregate winner 不能仅依靠人工字符串 ID 与普通 operation ID 的字典序；
4. 给 aggregate 明确的 dominance/order 语义；
5. `state_merge_receipts` 的保留期必须覆盖状态生命周期，或把必要 component evidence固化在 authoritative receipt中。

---

## CG17-F02：Legacy v1 upgrade 绕过新的 progress method matrix

- **级别**：P0，归入 progress resolver 阻断族
- **证据**：确定性代码路径
- **位置**：`PersistentStateOutbox._upgrade_v1_locked`

当前升级路径仍使用：

```text
if both kind == progress:
    _merge_progress_operations(existing, operation)
```

没有调用：

```text
resolve_operations(existing, operation)
```

因此：

- legacy set/set 不走 LWW；
- merge/set 不走 replacement；
- component membership/conflict 检查不完整；
- live path、import planner 与 migration path 得到不同 winner。

### 修复

- 所有两 operation 组合只能经过一个 resolver；
- migration 只能负责 schema conversion，不能另写业务冲突规则；
- v1/v2/current、live/import/orphan/reject 需共享同一 table-driven test suite。

---

## CG17-F03：State recovery 仍可能把 BUSY 当 INVALID

- **级别**：P0
- **证据**：确定性代码路径
- **涉及**：
  - `list_entries`
  - `_recover_reject_transactions`
  - `_promote_reject_temporary`
  - `.restore` recovery

### 问题一：`list_entries`

第一次进入 digest lock 若发生：

```text
state_lock_timeout
```

外层 broad catch 会再次尝试同一 lock；若第二次取得成功，它不重新验证，而是直接把 path 隔离为 invalid。

结果：

```text
一个完全有效的journal
→ 仅因另一个进程短暂持锁
→ 被移入quarantine
```

### 问题二：reject marker

`_recover_reject_transactions` 对：

```text
state_lock_timeout
```

没有特殊分支，而是把 marker 移到：

```text
pending-state-quarantine
```

这可能删除上一条 pending operation 的唯一自动恢复证据。

### 问题三：reject temp

Reject marker writer使用固定 `.reject-*.tmp`：

```text
创建文件
→ 写入
→ fsync
→ replace为.txn
```

另一个进程的 startup recovery 可以在写入期间看到该 temp；当前 promotion在解析前：

- 没有 grace；
- 没有 key lock；
- 没有稳定 fingerprint。

部分写入可能被当作 invalid reject temp 隔离。

### 修复原则

```text
BUSY 永远不是 INVALID
```

所有 recovery API 必须返回至少：

```text
ABSENT
VALID
BUSY
INVALID
UNREADABLE
RECOVERY_REQUIRED
```

只有在成功取得对应锁，并完成两次稳定读取后，才允许隔离。

---

## CG17-F04：Archive exporter 可以生成自身 reader 拒绝的文件

- **级别**：P0
- **证据**：确定性预算冲突
- **涉及**：
  - `_bounded_rows`
  - `MAX_TABLE_ROWS`
  - `MAX_JSON_NODES`
  - `export_data`
  - `_load_archive`

### 当前预算

Exporter允许：

```text
每表最多 1,000,000 行
每表最多 64 MiB
archive最多 128 MiB
```

Reader却限制：

```text
整个archive最多 250,000个JSON node
```

`_validate_json_shape`把：

- dict本身；
- 每个key；
- 每个value；

都计为node。

一个 attempts row约有数十个node。大约数千条attempt就可能超过250,000 nodes，而文件大小仍远低于128MiB。

Exporter在发布前只检查encoded bytes，没有对最终payload调用reader的shape validator。

### 结果

```text
export命令返回ok
→ backup文件成功写出
→ inspect/import/restore使用同版本程序
→ archive_too_complex
```

这直接破坏备份可靠性。

### 修复

二选一：

1. Exporter使用与reader完全相同的预算，并在发布前执行完整self-validate；
2. Archive v4改为流式、分块、逐表manifest格式。

在v3不变的前提下，至少必须：

```text
encode
→ reader-equivalent validate
→ in-memory round-trip load
→ publish
```

---

## CG17-F05：Terminal transaction root 可能被半删除成未知事务

- **级别**：P0
- **证据**：确定性文件系统故障路径
- **涉及**：
  - `ImportTransaction.finish`
  - `recover_import_transactions`
  - `has_import_transaction_roots`

当前：

```text
mark COMPLETED
→ shutil.rmtree(root, ignore_errors=True)
```

或：

```text
mark ROLLED_BACK
→ rmtree(ignore_errors=True)
```

在Windows文件占用、杀毒扫描或权限变化时，递归删除可能：

1. 删除 `journal.json`；
2. 随后在另一个locked file上失败；
3. 留下半个 `.import-*` 目录。

下一次启动：

```text
.import-*存在
→ journal.json不存在
→ import_recovery_required
→ 应用无法启动
```

原transaction其实已经terminal，但阶段证据已被cleanup自己删除。

### 修复

不要在active namespace内做可部分完成的递归删除：

```text
mark terminal
→ fsync journal
→ atomic rename .import-* → .transaction-cleanup-*
→ fsync parent
→ best-effort rmtree cleanup namespace
```

Cleanup namespace：

- 不属于active transaction；
- 会出现在status/recovery inventory；
- 删除失败只提示，不阻断应用；
- 不能被export target覆盖。

---

## CG17-F06：2048 动画中退出会写入不一致自动存档

- **级别**：P0
- **证据**：确定性逐帧状态
- **涉及**：
  - `_move`
  - `_tick_animations`
  - `_build_autosave_state`
  - `before_close`

### 当前移动时序

当两个相同tile发生合并时：

```text
_move:
    source被标记dead
    source不再放入grid
    surviving tile仍保持旧value
    score立即增加合并分
    anim_t=0

_tick_animations完成:
    surviving tile才double
    dead tile才从tiles移除
    才spawn新tile
```

但 `before_close()` 在任意 `anim_t` 下都会建立并发布autosave。

因此在动画中关闭：

```text
score已经+4
grid只剩一个2，而不是4
source已经不在grid
新tile尚未spawn
```

该状态通过现有“数值为2的幂、score为整数”等语义检查，却不是一局合法settled 2048状态。

### 修复方案

推荐使用稳定状态边界：

- 每个move保留pre-move settled snapshot；
- 结算时一次性提交：
  - board；
  - score delta；
  - won/gameover；
  - spawn；
  - RNG state；
- autosave只能读取settled snapshot。

退出时可选择：

1. 完成当前已接受的move，并丢弃queued directions；
2. 保存pre-move snapshot；
3. 使用明确的in-flight save schema。

不要保存半结算状态。

---

# 5. P1：高优先级一致性与恢复问题

## CG17-F07：SQLite已提交后，journal cleanup异常会误报保存失败

Score成功分支：

```text
record_mutation commit成功
→ outbox.remove()
```

`remove()`现在会取得request lock，并可能抛：

```text
StoreError("spool_lock_timeout")
```

但调用方只捕获 `OSError`。

结果：

```text
SQLite已成功提交
→ cleanup锁超时
→ Future以异常结束
→ callback重构成pending/worker failure
→ UI把成功保存显示成失败
```

State成功后调用 `remove_if_current()`也有相似问题。

### 修复

数据库commit之后：

- journal cleanup只能是best-effort；
- cleanup失败不能回滚或否认DB成功；
- 返回：
  ```text
  COMMITTED
  cleanup_pending=true
  ```
- 后台scanner最终清理；
- receipt/status重构必须优先承认DB authoritative result。

---

## CG17-F08：State journal有效性依赖当前wall clock

当前parser会拒绝：

```text
updated_at > time.time() + 24h
```

因此相同不可变bytes可以：

```text
写入时有效
→ 系统时钟回拨两天后无效
→ 时钟恢复后又有效
```

运行扫描可能把原本合法journal隔离；跨设备archive也会因为目标设备时钟落后而失败。

### 修复

- parser只验证固定绝对范围，不读取当前时间；
- creation/apply阶段对未来时间进行clamp/adjust；
- 保留 `source_updated_at` 和 `clock_adjusted`；
- ordering主要由logical revision，而不是未校准wall clock；
- 迁移旧journal时不能因当前设备时间不同改变validity。

---

## CG17-F09：State event没有终态优先级

Event cache已经使用：

```text
(logical_revision, operation_id)
```

但同identity、不同state时仍允许相互覆盖。

需要定义：

```text
SAVING
< NON_DURABLE_PENDING
< DURABLE_PENDING
< RECOVERY_REQUIRED
< SUPERSEDED / COMMITTED / PERMANENT_FAILURE / QUARANTINED
```

对同operation：

- terminal不能回退到pending；
- COMMITTED和SUPERSEDED冲突时以authoritative receipt解释；
- reconstructed事件不能覆盖更新鲜的live terminal event。

---

## CG17-F10：State clock恢复仍可能丢弃有效temp或覆盖未保存证据

两项残留：

1. clock orphan temp有效，但current clock损坏时，当前代码会隔离temp而不是先保存损坏current；
2. `_quarantine_clock_locked`不返回成功/失败；隔离失败后流程仍可尝试用新clock替换旧clock。

应复用普通state current的契约：

```text
保存损坏current成功
→ 才能提升valid temp或创建新clock
```

---

## CG17-F11：Worker未完全退出时application lease仍会释放

`LocalWriteWorker.close()`超时后：

```text
executor.shutdown(wait=False)
```

运行中的task可以继续。

`LocalBackendClient.close()`忽略两个worker的drain结果，随后总是关闭application session。

于是维护命令可能认为应用已停止，而旧线程仍可能：

- 读取SQLite；
- 等待maintenance lock；
- 在replace后继续写新数据库；
- 发布journal。

### 修复

- application lease的生命周期必须覆盖最后一个读写task；
- close失败应返回结构化结果；
- UI退出时显示“后台保存尚未结束”；
- 无法终止的task应让lease保持到进程退出，而不是提前释放；
- 增加slow read/write + import barrier测试。

---

## CG17-F12：Score per-request lock file永久累积

每个唯一score request ID都会创建：

```text
pending/.<request_id>.lock
```

这些文件没有回收。

长时间使用后会产生：

- inode/目录项无限增长；
- startup/scandir变慢；
- backup/安全软件扫描成本增长；
- Windows目录操作明显变慢。

直接unlink持锁文件会产生split-lock，因此不能简单删除。

### 推荐

- 使用固定数量的striped lock files，例如256/1024个；
- request ID映射到stripe；
- 迁移期兼容旧lock；
- status报告lock count；
- 安全清理历史per-request locks。

---

## CG17-F13：`inspect-archive`会跟随final symlink

`_load_archive`本身已经no-follow，但 `inspect_archive()` 先：

```text
archive_path.resolve(strict=False)
```

再调用reader，相当于主动把final symlink解析成目标路径。

应传入lexical absolute path：

```text
abspath(expanduser(path))
```

让 `_read_regular_nofollow` 负责拒绝final symlink。

---

## CG17-F14：`inspect-archive`不是完整语义验证

当前主要验证：

- bounded JSON；
- manifest hash；
- table counts；
- manifest结构；
- reader capability。

它没有完整执行：

- 每行semantic validation；
- natural-key collision；
- foreign-key关系；
- pending current/historical分类；
- recovery evidence base64/hash/path；
- planned file target policy；
- archive能否导入空当前schema。

因此“validate an archive”容易被理解为“可恢复”。

应区分：

```text
inspect-header
verify-archive
preview-import
```

其中 `verify-archive` 应完全不打开用户DB，但可使用内存/临时空schema做深度验证。

---

## CG17-F15：Historical pending仍是parse-first

当前只有在current parser失败后才调用：

```text
_is_historical_pending()
```

已知游戏的旧ruleset通常仍能通过score parser；部分旧state payload也可能通过当前validator。

于是：

```text
代码实际把pending激活
返回结果却按ruleset统计为historical_evidence_only
```

### 修复

先分类：

```text
current game + current ruleset → current parser
explicit adapter              → adapter
其他                           → evidence-only
```

不要把“恰好还能解析”当作语义兼容证明。

---

## CG17-F16：Active protocol report与terminal transaction classifier不一致

应用启动的 `has_import_transaction_roots()` 会忽略有效：

```text
COMPLETED
ROLLED_BACK
```

root。

但 export 的 `_active_protocol_report()` 会把每个 `.import-*` 目录都标为 unresolved/unsafe type。

因此：

- 应用可正常打开；
- complete export却失败；
- 同一个root在两个模块中得到相反结论。

应建立共享：

```text
TransactionRootClassifier
```

统一用于：

- startup；
- status；
- export；
- cleanup；
- recovery UI。

---

## CG17-F17：Recovery completeness把active数据与所有forensic evidence绑定过紧

当前manifest的单个：

```text
complete
```

同时要求：

- committed DB snapshot完整；
- active score/state pending完整；
- transaction inventory完整；
- 所有recovery evidence完整。

结果：

- 一个旧backup未选择；
- 一个大于8MiB的corrupt DB；
- 一个historical imported evidence；

都可能让archive不能用于 `restore-replace`，即使active用户数据完全可恢复。

### 新模型

至少分为：

```text
active_data_complete
active_journals_complete
transaction_inventory_complete
forensic_evidence_complete
replace_eligible
```

Replace eligibility应由active data决定，不能被可选forensic bytes无条件否定。

---

## CG17-F18：大型recovery file形成cleanup死锁

Recovery evidence单文件上限为8MiB，但数据库backup很容易超过8MiB。

大文件：

- 无法完整嵌入archive；
- archive recovery complete=false；
- cleanup又要求recovery complete=true；
- 因而永远不能通过现有proof自动清理。

### 修复

允许：

```text
hash-only evidence inventory
```

Archive不必嵌入大文件内容，但可记录：

- path；
- size；
- SHA-256；
- source kind；
- retention class。

Cleanup proof验证现有文件hash与manifest一致即可。

---

## CG17-F19：Imported recovery evidence会递归嵌套

`imported-recovery`本身属于recovery path：

```text
import archive A
→ imported-recovery/A/...

再export
→ 把imported-recovery/A纳入evidence

再import为B
→ imported-recovery/B/imported-recovery/A/...
```

多轮后路径、manifest和磁盘占用持续增长。

需要：

- provenance ID；
- content-addressed evidence store；
- 同hash去重；
- 默认不重新嵌入已导入evidence；
- 用户显式选择“包含全部取证链”时才递归。

---

## CG17-F20：ImportTransaction journal parser缺少完整shape与异常边界

`ImportTransaction.open()`：

- 没有复用bounded JSON shape validator；
- 不拒绝非标准JSON constant；
- 没有捕获 `RecursionError` / `MemoryError`；
- read后缺完整post-fstat一致性检查；
- journal hash序列化异常也不总能转换成structured StoreError。

损坏transaction root可能让startup出现未包装异常。

---

## CG17-F21：Transaction writer未证明自己的journal一定可被reader读取

Writer在 `_write_journal()` 中没有：

- operation count上限；
- encoded journal byte上限；
- writer/reader shape一致性检查。

Reader却有16MiB上限。

虽然当前正常planner多数情况下较小，协议层仍应保证：

```text
任何成功publish的transaction
→ 当前reader必能open
```

---

## CG17-F22：DB行含意外动态类型时export可能返回未包装异常

SQLite允许动态类型。若损坏/人工修改使某个字段成为BLOB：

```text
_bounded_rows
→ canonical_json(bytes)
→ MutationError
```

CLI顶层主要捕获：

- StoreError；
- sqlite3.Error；
- OSError。

应把不支持的DB值转换成：

```text
invalid_archive_source
```

并指出table/row/column，不应输出traceback。

---

## CG17-F23：Sokoban恢复后只更新了一半score identity

Durable session恢复时设置：

```text
attempt_context.attempt_uuid
attempt_context.revision
```

但BaseGame实际提交还使用：

```text
_score_attempt_uuid
_score_attempt_revision
```

若不同步，恢复后的最终通关可能创建新的attempt，而不是继续保存的run。

应提供单一：

```text
restore_attempt_identity(...)
```

禁止各游戏直接修改多个字段。

---

## CG17-F24：Sokoban成功恢复后立即清除durable session

`_poll_campaign_session()`：

```text
恢复到内存成功
→ 立即写active=False
```

若程序在下一次checkpoint之前崩溃，刚恢复的唯一durable return point已被清除。

更安全的是：

- 保持active直到玩家明确返回campaign或结束session；
- 或先写新的active checkpoint，再使旧版本superseded；
- tombstone必须等待authoritative commit。

---

## CG17-F25：Sokoban忽略save-slot外层ruleset

Slot load结果包含：

```text
ruleset_version
```

当前代码取出：

```text
saved["state"]
```

后直接恢复，不先比较outer ruleset。

旧规则地图若坐标仍“看起来有效”，会被当成当前campaign恢复，并可能在当前ruleset下提交分数。

要求：

```text
outer ruleset == current ruleset
```

否则保留为historical evidence，不激活。

---

## CG17-F26：Sokoban durable session验证仍不充分

仍需验证：

- attempt ID字符集；
- revision 63-bit上限；
- score/moves/pushes/MAX_SCORE上限；
- `total_score == sum(level_scores)`；
- completed_levels 与 level_scores key一致；
- 坐标必须是两个exact int；
- history moves/pushes单调合理；
- history每一步board是否是可达状态；
- 当前level必须与ledger一致。

这些检查是本机数据完整性，不是反作弊。

---

## CG17-F27：Sokoban async fallback没有durability证明

若backend没有 `publish_slot_intent`，会退到：

```text
save_slot_async
→ 立即返回True
→ 允许进入practice
```

但Future尚未完成，且当前没有完整消费 `_campaign_session_save_future` 的状态机。

默认LocalBackend有同步journal发布，因此主路径安全；可选adapter和测试替身的契约仍不安全。

应要求明确capability：

```text
durable_slot_intent
```

没有该capability时：

- 等待Future确认durable；
- 或禁止跨campaign practice session；
- 不能仅因Future已创建就报告持久化成功。

---

## CG17-F28：2048旧ruleset autosave缺少明确的新开迁移流程

Save slot自然键不含ruleset：

```text
(profile, game, slot)
```

客户端遇到旧ruleset会拒绝加载，但当前v5 CAS又可能拒绝直接覆盖旧slot。

需要明确本地操作：

```text
保留旧slot为historical/recovery evidence
→ 原子删除/归档旧active slot
→ 创建current ruleset新slot
```

必须二次确认，但不需要联网。

---

## CG17-F29：2048 RNG注入尚未形成可继续重放的存档

当前新tile使用注入RNG，是重要进步。

但autosave没有保存：

- seed；
- RNG state；
- move sequence digest。

因此从同一save继续并不能确定性重放。

这不是当前数据损坏Bug，可作为P2：

- 本地replay；
- bug复现；
-日期挑战；
- 自动化模型测试。

---

## CG17-F30：BaseGame吞掉before_close异常但没有结构化记录

`BaseGame.run()`为保证SDL退出会忽略 `before_close()` 异常，这个安全选择可以保留，但必须：

- 写structured log；
- 更新本机recovery notice；
- 若最后意图未durable，退出页明确提示；
- debug build保留traceback。

---

# 6. P2：工程、桌面体验和可维护性

## 6.1 模块边界

当前大型模块仍包含过多职责：

- `local_backend.py`
- `store.py`
- `data_cli.py`
- `launcher.py`

协议修复完成后按行为边界拆分：

```text
game_service/
    score_outbox.py
    state_outbox.py
    state_resolver.py
    workers.py
    backend_status.py
    archive/
        schema.py
        reader.py
        writer.py
        planner.py
        executor.py
    transaction/
        journal.py
        recovery.py
```

要求：

- 先固定测试；
- 一次只拆一个边界；
- 不同时更改schema；
- 不做机械式全仓重命名。

## 6.2 类型系统

优先加入静态类型：

- `GameDataService`;
- `ScoreMutation`;
- `PendingSaveEnvelope`;
- `StateOperation`;
- `StateReceipt`;
- `LocalStateEvent`;
- `ArchiveManifestV3`;
- `ImportTransactionJournal`;
- `SlotLoadResult`;
- `RecoveryReport`.

动态JSON先用：

```text
TypedDict + validator
```

不必一次把pygame所有代码严格化。

## 6.3 本机数据管理页

GUI应支持：

- 数据库状态；
- active score/state pending；
- quarantine；
- migration backup；
- transaction；
- imported evidence；
- export；
- verify archive；
- preview import；
- merge import；
- replace restore；
- cleanup plan；
- 打开数据目录；
- export-before-delete。

GUI通过 `DataManagementService`，不要直接调用SQLite内部方法。

## 6.4 档案管理

增加：

- 档案列表；
- 新建；
- 重命名；
- 切换；
- 每游戏进度摘要；
- 最后游玩时间；
- 备份单档案；
- 删除前导出；
- 本地家庭成员分档；
- 可选档案合并预览。

不需要账号或云同步。

## 6.5 输入

增加统一 `InputManager`：

- action map；
- 键位重映射；
- 冲突检测；
- 全键盘launcher导航；
- 手柄；
- dead zone；
- focus lost清理；
- overlay input guard；
- 每游戏默认profile；
- 输入提示自动切换键盘/手柄图标。

## 6.6 音频

增加：

- BGM；
- SFX；
- master/music/effects音量；
- mute；
- 无音频设备安全降级；
- focus lost可选降音；
- 原创或明确授权素材；
- 资源license inventory。

## 6.7 显示与无障碍

- 逻辑分辨率与可缩放viewport；
- window resize；
- fullscreen；
- 高DPI；
- CJK字体fallback；
- 字体授权；
- 高对比度；
- 色弱图案/符号；
- 降低动画；
- 屏幕闪烁限制；
- 大字号；
- 清晰focus ring；
- 纯键盘操作；
- 可调输入重复参数。

## 6.8 确定性与纯逻辑engine

为五款游戏统一注入：

```text
Clock
RNG
InputCommand stream
```

逐步抽出：

- Tetris rules engine；
- Snake grid engine；
- 2048 board engine；
- Sokoban board/solver primitives；
- Zuma chain/reaction engine。

pygame只负责：

- 输入；
- 动画；
-渲染；
- 声音。

---

# 7. P3：符合本地单机定位的玩法完善

## 7.1 Tetris

可选功能：

- lock delay；
- strict/assist rotation preset；
- hold和next图形预览；
- 规则说明页；
- 本地seed挑战；
- sprint/40 lines；
- marathon；
- 模式独立本机最佳；
- replay。

不加入联网对战。

## 7.2 Snake

- classic；
- 穿墙；
- 障碍；
- 速度曲线；
- 双人同屏；
- 本地挑战seed；
- 色弱食物图案；
- 模式独立最佳；
- replay。

## 7.3 2048

- undo；
- 多个本地slot；
- slot预览、复制、删除；
- 4×4/5×5模式；
- 规则独立排行；
- seed replay；
- move history；
- 旧ruleset slot归档。

## 7.4 Sokoban

- 正式选关页；
- campaign/practice明显区分；
- 最佳步数/推动数；
- 星级；
- 可关闭的死锁提示；
- XSB导入导出；
- 本地关卡编辑器；
- 自定义关卡与官方campaign分开；
- replay/解法记录。

## 7.5 Zuma

- 明确的reaction FSM；
- training；
- level select；
- 色弱符号；
- 瞄准辅助；
- 本地轨道编辑器；
- 原创道具；
- 关卡规则版本化；
- deterministic chain tests；
- replay。

## 7.6 全局离线内容

- 本机成就；
- 日期seed挑战；
- 本地统计；
- 本地复盘；
- 中英文；
- 无网络情况下完整使用；
- 无默认遥测；
- 导出replay文件；
- 本地“童年游戏展柜”主题与皮肤。

---

# 8. 明确不建设

本项目不应把工程资源投入：

- 注册登录；
- 在线账号；
- 公网排行榜；
- 实时匹配；
- 房间；
- 赛季；
- 云端权威分数；
- 反作弊；
- 云存档；
- 在线商城；
- 好友系统；
- 默认上传日志；
- 默认遥测；
- 强制联网检查；
- 服务端重写五款游戏逻辑。

Optional Flask继续定位为：

```text
本机开发和接口调试适配器
```

非产品核心。

---

# 9. 完整优化任务清单

## 9.1 P0：发布阻断

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG17-P0-01 | Aggregate component membership resolver | 共享resolver | 被aggregate吸收的set重放为duplicate | M |
| CG17-P0-02 | Set component hash冲突检测 | resolver | 同ID不同hash稳定拒绝 | S |
| CG17-P0-03 | Store检查set是否已有merge receipt | Store apply | set不能覆盖包含自己的aggregate | M |
| CG17-P0-04 | Aggregate dominance语义 | ADR+实现 | winner不依赖字符串前缀字典序 | M |
| CG17-P0-05 | Merge receipt生命周期 | schema/maintenance | component证据不早于状态失效 | M |
| CG17-P0-06 | Legacy v1 upgrade统一resolver | migration | 不再直接调用merge helper | S |
| CG17-P0-07 | v2/current upgrade统一resolver | migration tests | 所有版本方法矩阵一致 | M |
| CG17-P0-08 | Import planner统一component规则 | planner | live/import winner完全相同 | M |
| CG17-P0-09 | Orphan replay component测试 | multiprocess/fault | 原component不能回退aggregate | M |
| CG17-P0-10 | Progress model-based tests | property suite | 任意顺序满足幂等/单调/冲突不变量 | L |
| CG17-P0-11 | Reject temp使用唯一temp | state outbox | writer间不争用固定temp | S |
| CG17-P0-12 | Reject temp grace+fingerprint | recovery | 部分写入不隔离 | M |
| CG17-P0-13 | Reject marker lock timeout保留 | recovery | BUSY marker原位保留 | S |
| CG17-P0-14 | Restore file lock timeout保留 | recovery | BUSY restore原位保留 | S |
| CG17-P0-15 | `list_entries`重写异常分类 | scanner | timeout不进行第二次盲隔离 | M |
| CG17-P0-16 | 全state recovery结果枚举 | contract | BUSY/INVALID/ABSENT分开 | M |
| CG17-P0-17 | Reject writer/scanner barrier测试 | multiprocess | scanner不能抢走活跃temp | M |
| CG17-P0-18 | Archive writer/reader预算统一 | archive v3 | 成功export必能load | M |
| CG17-P0-19 | Export final self-validation | writer | publish前执行reader等价验证 | M |
| CG17-P0-20 | 大attempt round-trip测试 | archive tests | 超过250k nodes时安全拒绝或可读 | M |
| CG17-P0-21 | Archive budget machine-readable | manifest/report | 返回实际nodes/bytes/limit | S |
| CG17-P0-22 | Terminal root原子转cleanup namespace | transaction | active namespace无partial rmtree | M |
| CG17-P0-23 | Cleanup namespace classifier | startup/status | cleanup失败不阻断启动 | M |
| CG17-P0-24 | Partial rmtree故障注入 | Windows/POSIX | journal先删场景仍安全 | M |
| CG17-P0-25 | 2048 settled move snapshot | game engine | autosave永远来自settled board | L |
| CG17-P0-26 | 2048 close finalize policy | controller | 当前move完成或回退，绝不半结算 | M |
| CG17-P0-27 | Mid-animation四方向测试 | gameplay | 普通移动、merge、win、gameover覆盖 | M |
| CG17-P0-28 | Mid-animation crash/reload测试 | slot test | score/grid/spawn一致 | M |
| CG17-P0-29 | P0 release gate | CI profile | 任一P0回归禁止release | S |

---

## 9.2 P1：协议与恢复收口

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG17-P1-01 | Score commit与cleanup分离 | backend | cleanup失败仍返回COMMITTED | M |
| CG17-P1-02 | State commit与cleanup分离 | backend | DB authoritative结果不被cleanup异常否定 | M |
| CG17-P1-03 | `cleanup_pending`状态 | event contract | 用户和后台可见 | S |
| CG17-P1-04 | Receipt优先status reconstruction | status | commit后不回退pending | M |
| CG17-P1-05 | State timestamp parser确定化 | journal | validity不依赖当前time | M |
| CG17-P1-06 | Future timestamp creation policy | operation factory | clamp/adjust并报告 | M |
| CG17-P1-07 | Legacy timestamp migration | migration | 时钟不同仍可迁移 | M |
| CG17-P1-08 | Event state precedence reducer | service | terminal不回退 | M |
| CG17-P1-09 | Equal identity event property test | tests | 任意事件顺序结果一致 | M |
| CG17-P1-10 | Clock corrupt current优先隔离 | clock recovery | valid temp可提升 | S |
| CG17-P1-11 | Clock quarantine返回bool/result | clock API | 失败不覆盖证据 | S |
| CG17-P1-12 | Clock failure injection | tests | symlink/readonly/full覆盖 | M |
| CG17-P1-13 | Backend close返回结果 | lifecycle | drained/read/write状态可见 | M |
| CG17-P1-14 | Lease延续到worker真正结束 | lifecycle | maintenance不能越过活跃task | L |
| CG17-P1-15 | Slow read/write shutdown barrier | tests | close/import无重叠 | M |
| CG17-P1-16 | Score striped locks | spool protocol | lock file数量固定有界 | L |
| CG17-P1-17 | Legacy request-lock cleanup | migration/CLI | 不产生split-lock | M |
| CG17-P1-18 | Lock inventory/status | status | lock数量和异常可见 | S |
| CG17-P1-19 | Inspect final symlink no-follow | CLI | lexical path直接交给reader | S |
| CG17-P1-20 | `inspect-header`命令 | CLI | 明确只做结构摘要 | S |
| CG17-P1-21 | `verify-archive`命令 | CLI | 无目标DB的完整语义验证 | L |
| CG17-P1-22 | Archive row semantic verify | verifier | current/historical策略完整 | M |
| CG17-P1-23 | Archive FK verify | verifier | 空当前schema中验证关系 | M |
| CG17-P1-24 | Pending classification-before-parse | planner | 旧ruleset不激活 | M |
| CG17-P1-25 | Historical score adapter registry | archive | 仅显式adapter可激活 | M |
| CG17-P1-26 | Historical state adapter registry | archive | 其余evidence-only | L |
| CG17-P1-27 | Import结果基于实际file plan统计 | result | 统计不靠重复ruleset猜测 | M |
| CG17-P1-28 | Shared transaction root classifier | module | startup/export/status/cleanup共用 | L |
| CG17-P1-29 | Active report忽略valid terminal root | export | complete snapshot语义一致 | S |
| CG17-P1-30 | Active/forensic completeness拆分 | manifest v4 ADR | replace eligibility独立 | M |
| CG17-P1-31 | v3兼容映射 | reader | 不破坏现有archive v3 | M |
| CG17-P1-32 | Large recovery hash-only inventory | archive | >8MiB可证明保留 | L |
| CG17-P1-33 | Cleanup支持hash-only proof | cleanup | 大backup可安全删除 | L |
| CG17-P1-34 | Evidence content-addressed store | recovery | 同hash只保留一份 | L |
| CG17-P1-35 | Imported evidence provenance | manifest | 来源链可追踪 | M |
| CG17-P1-36 | 默认不递归重嵌入evidence | export | 多轮不无限嵌套 | M |
| CG17-P1-37 | Transaction journal shape validator | transaction | depth/nodes/string有界 | M |
| CG17-P1-38 | Transaction JSON constant拒绝 | parser | NaN/Infinity拒绝 | S |
| CG17-P1-39 | Transaction post-read fstat | reader | 增长/替换竞态拒绝 | S |
| CG17-P1-40 | Transaction异常统一StoreError | recovery | 无裸Recursion/Memory/JSON错误 | M |
| CG17-P1-41 | Journal encoded-size preflight | writer | published journal必可open | S |
| CG17-P1-42 | Operation count上限 | transaction | 有明确错误码 | S |
| CG17-P1-43 | SQLite dynamic type export错误 | archive source | table/row/column结构化报告 | M |
| CG17-P1-44 | Export自读闭环 | release test | export→load→verify→preview | M |
| CG17-P1-45 | Sokoban统一restore attempt API | BaseGame | context和private字段同步 | M |
| CG17-P1-46 | Sokoban session outer ruleset检查 | game | 不兼容slot不激活 | S |
| CG17-P1-47 | Sokoban session full validator | validation module | bounds/ledger/identity完整 | L |
| CG17-P1-48 | Sokoban active tombstone lifecycle | controller | 内存恢复后不提前清除 | M |
| CG17-P1-49 | Sokoban session commit status | UI/backend | active/inactive写入结果可见 | M |
| CG17-P1-50 | Sokoban async durable capability | service | Future创建不等于durable | M |
| CG17-P1-51 | Invalid Sokoban session quarantine | game/store | 原始字节保留并提示 | M |
| CG17-P1-52 | 2048 old ruleset slot archive | local data op | 旧slot保留后新开 | L |
| CG17-P1-53 | 2048 new-game CAS test | gameplay/storage | 旧ruleset不造成死锁 | M |
| CG17-P1-54 | before_close structured logging | BaseGame | 失败有操作ID和恢复提示 | S |
| CG17-P1-55 | Recovery scan总时间预算 | scanner | 异常目录不拖慢首帧 | M |
| CG17-P1-56 | Marker glob数量上限 | state outbox | 单key恶意marker有界 | S |
| CG17-P1-57 | Disk preflight明细 | CLI | DB/archive/staging/margin分别报告 | S |
| CG17-P1-58 | 减少重复DB备份 | import design | 复用authenticated image | L |
| CG17-P1-59 | API app-factory暴露策略 | optional adapter | CLI/WSGI行为明确 | M |
| CG17-P1-60 | Debug API可选随机token | optional adapter | 仍保持本机调试定位 | M |
| CG17-P1-61 | Main required checks | repository settings | main不可绕过CI | S |
| CG17-P1-62 | 三平台hash lock | lock files | `--require-hashes`安装 | L |
| CG17-P1-63 | Coverage分层门槛 | CI | storage/archive≥90%，全仓渐进≥80% | M |
| CG17-P1-64 | Static type gate | pyright/mypy | service/archive/transaction通过 | L |
| CG17-P1-65 | LICENSE权利核对 | owner checklist | 代码/AI/名称/素材确认 | M |
| CG17-P1-66 | 正式LICENSE | repository | 稳定分发前完成 | S |
| CG17-P1-67 | 持久化协议封板ADR | docs | P0/P1后冻结格式 | M |

---

## 9.3 P2：桌面体验、维护性与无障碍

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG17-P2-01 | 拆分score outbox | module | 行为不变 | L |
| CG17-P2-02 | 拆分state outbox/resolver | module | 共享resolver可独测 | L |
| CG17-P2-03 | 拆分backend workers/status | module | lifecycle清晰 | L |
| CG17-P2-04 | 拆分archive reader/writer | package | v3兼容 | L |
| CG17-P2-05 | 拆分planner/executor | package | preview/apply共享plan | L |
| CG17-P2-06 | Typed archive manifest | types | 无裸dict边界 | M |
| CG17-P2-07 | Typed state operation/receipt | types | static check通过 | M |
| CG17-P2-08 | DataManagementService | API | GUI不触碰SQLite内部 | L |
| CG17-P2-09 | 数据状态页 | UI | DB/pending/recovery一览 | L |
| CG17-P2-10 | Archive verify UI | UI | hash/语义/eligibility可见 | L |
| CG17-P2-11 | Import preview UI | UI | 冲突和空间可见 | L |
| CG17-P2-12 | Transaction recovery UI | UI | export evidence/recover | L |
| CG17-P2-13 | Recovery cleanup UI | UI | preview后apply | L |
| CG17-P2-14 | 档案列表 | UI | 新建/切换/重命名 | L |
| CG17-P2-15 | 档案进度摘要 | dashboard | 五款游戏可见 | M |
| CG17-P2-16 | Export-before-delete | profile flow | 删除可恢复 | M |
| CG17-P2-17 | 本地档案合并预览 | data tool | 不静默覆盖 | XL |
| CG17-P2-18 | InputManager | common | action map统一 | L |
| CG17-P2-19 | 键位重映射 | settings | 冲突检测 | L |
| CG17-P2-20 | 全键盘launcher | focus system | 无鼠标完整使用 | L |
| CG17-P2-21 | 手柄支持 | controller | launcher/五款基础可用 | L |
| CG17-P2-22 | IME输入控件 | widget | composition/光标/选择 | M |
| CG17-P2-23 | AudioManager | common | BGM/SFX/音量 | L |
| CG17-P2-24 | 无音频设备降级 | audio | 不崩溃 | S |
| CG17-P2-25 | 统一设置页 | UI | 音量/显示/输入/辅助 | L |
| CG17-P2-26 | 逻辑分辨率 | renderer | resize不裁切 | XL |
| CG17-P2-27 | 高DPI | display | 三平台清晰 | M |
| CG17-P2-28 | CJK fallback | fonts | 缺字体仍可读 | M |
| CG17-P2-29 | 字体/素材license inventory | docs/build | 每个资源可追踪 | M |
| CG17-P2-30 | 高对比模式 | accessibility | 信息不只靠颜色 | M |
| CG17-P2-31 | 色弱符号 | accessibility | Snake/Zuma/2048可辨 | L |
| CG17-P2-32 | 降低动画 | accessibility | 所有游戏可关闭强动画 | M |
| CG17-P2-33 | 大字号与focus ring | accessibility | 菜单可读可导航 | M |
| CG17-P2-34 | Clock服务注入 | deterministic | 不依赖全局time | L |
| CG17-P2-35 | RNG服务注入 | deterministic | 五款统一 | M |
| CG17-P2-36 | RNG state持久化 | save/replay | 重启后序列可重现 | L |
| CG17-P2-37 | 纯2048 engine | rules | 无SDL测试 | L |
| CG17-P2-38 | 纯Tetris engine | rules | 无SDL测试 | XL |
| CG17-P2-39 | 纯Snake engine | rules | 无SDL测试 | M |
| CG17-P2-40 | Sokoban board primitives | rules | 状态/undo/验证统一 | L |
| CG17-P2-41 | Zuma reaction FSM | rules | 属性测试可覆盖 | XL |
| CG17-P2-42 | 本地replay格式 | replay | seed+commands+ruleset | L |
| CG17-P2-43 | Structured recovery log | logging | operation/transaction关联 | M |
| CG17-P2-44 | Benchmark CLI | performance | OS/版本/seed完整 | M |
| CG17-P2-45 | 30–60分钟soak | stability | FD/线程/内存稳定 | M |
| CG17-P2-46 | README目录树更新 | docs | 包含test_storage_v14等 | S |

---

## 9.4 P3：单机玩法与发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG17-P3-01 | Tetris lock delay | mode | 有确定性测试 | M |
| CG17-P3-02 | Tetris strict/assist preset | ruleset | 分榜/分统计 | M |
| CG17-P3-03 | Tetris sprint/marathon | local modes | 无网络 | L |
| CG17-P3-04 | Tetris图形hold/next | UI | 清晰预览 | S |
| CG17-P3-05 | Snake速度/穿墙/障碍 | modes | 独立ruleset | L |
| CG17-P3-06 | Snake双人同屏 | local multiplayer | 不联网 | L |
| CG17-P3-07 | 2048 undo | local state | slot一致性完整 | L |
| CG17-P3-08 | 2048多本地slot | save UI | 预览/复制/删除 | L |
| CG17-P3-09 | 2048棋盘尺寸 | modes | ruleset分离 | M |
| CG17-P3-10 | Sokoban正式选关 | UI | campaign/practice明确 | L |
| CG17-P3-11 | Sokoban星级/最佳推动 | metrics | 规则固定 | M |
| CG17-P3-12 | Sokoban死锁提示 | optional helper | 可关闭 | XL |
| CG17-P3-13 | Sokoban XSB编辑器 | local tool | 地图验证 | L |
| CG17-P3-14 | Zuma训练/选关 | practice | 不混入campaign | L |
| CG17-P3-15 | Zuma色弱符号 | accessibility | 球色不唯一 | M |
| CG17-P3-16 | Zuma轨道编辑器 | local tool | 版本化/预览 | L |
| CG17-P3-17 | Zuma原创道具 | content | 确定性逻辑 | XL |
| CG17-P3-18 | 本机成就 | local system | 无账号/遥测 | L |
| CG17-P3-19 | 离线每日挑战 | date seed | 无网络完整可用 | L |
| CG17-P3-20 | 本地复盘浏览器 | replay UI | 可暂停/快进 | L |
| CG17-P3-21 | 中英文 | localization | 长文本布局测试 | L |
| CG17-P3-22 | Windows portable/installer | package | 无Python运行 | XL |
| CG17-P3-23 | macOS app bundle | package | 签名/数据目录正确 | XL |
| CG17-P3-24 | Linux AppImage/包 | package | XDG目录正确 | XL |
| CG17-P3-25 | 自动release与校验和 | pipeline | smoke通过才发布 | L |

---

# 10. 必须新增的定向测试

## 10.1 Progress aggregate

```text
test_aggregate_absorbed_set_replay_is_duplicate
test_aggregate_component_hash_reuse_is_conflict
test_store_rejects_set_already_applied_as_merge_component
test_legacy_v1_set_set_uses_lww
test_legacy_v1_merge_set_uses_method_matrix
test_orphan_component_cannot_replace_aggregate
test_import_order_permutations_produce_same_progress
test_merge_receipt_retention_cannot_break_idempotency
```

## 10.2 State recovery BUSY

```text
test_list_entries_first_lock_timeout_never_quarantines
test_reject_txn_lock_timeout_preserves_marker
test_restore_lock_timeout_preserves_restore_file
test_reject_temp_scanner_cannot_read_active_writer_temp
test_reject_temp_fingerprint_changes_are_preserved
test_busy_is_never_reported_as_invalid
```

## 10.3 Archive self-compatibility

```text
test_exported_archive_is_readable_by_same_version
test_many_attempts_do_not_exceed_reader_without_export_failure
test_export_reports_node_budget_before_publish
test_archive_roundtrip_near_byte_limit
test_archive_roundtrip_near_node_limit
test_verify_archive_checks_rows_fk_pending_and_evidence
```

## 10.4 Terminal transaction cleanup

```text
test_finish_renames_before_recursive_cleanup
test_partial_cleanup_cannot_leave_import_root_without_journal
test_cleanup_namespace_does_not_block_startup
test_cleanup_namespace_is_visible_in_status
test_windows_locked_file_terminal_cleanup
```

## 10.5 2048 stable autosave

```text
test_close_during_nonmerge_slide_restores_settled_board
test_close_during_merge_restores_doubled_tile
test_close_during_merge_preserves_score_consistency
test_close_during_winning_move_restores_win_overlay
test_close_during_final_move_restores_gameover
test_close_discards_queued_directions_after_current_move
test_autosave_never_serializes_anim_t_inflight_state
```

## 10.6 Commit cleanup

```text
test_score_commit_survives_remove_lock_timeout
test_state_commit_survives_remove_lock_timeout
test_cleanup_pending_is_reconstructed_from_receipt
test_committed_event_cannot_regress_to_pending
```

## 10.7 Time

```text
test_state_journal_validity_does_not_depend_on_current_clock
test_clock_rollback_does_not_quarantine_pending
test_archive_from_future_clock_is_adjusted_not_invalidated
test_legacy_timestamp_is_deterministic_across_devices
```

## 10.8 Historical pending

```text
test_known_game_old_ruleset_score_is_evidence_only
test_known_game_old_ruleset_state_is_evidence_only
test_historical_pending_result_matches_actual_file_plan
test_explicit_adapter_is_required_for_activation
```

## 10.9 Sokoban session

```text
test_restored_session_updates_all_attempt_identity_fields
test_session_ruleset_must_match_outer_slot
test_restore_does_not_clear_only_durable_checkpoint
test_inactive_tombstone_waits_for_commit
test_invalid_session_is_quarantined
test_async_slot_future_is_not_treated_as_durable_on_creation
test_ledger_total_matches_level_scores
```

## 10.10 Lifecycle与资源

```text
test_backend_keeps_application_lease_until_running_worker_finishes
test_score_lock_files_are_bounded_by_stripe_count
test_before_close_failure_emits_structured_notice
test_import_cannot_overlap_slow_read_worker
```

---

# 11. 稳定版质量门禁

正式稳定版本至少满足：

1. 五个P0族全部关闭；
2. progress aggregate满足：
   - 幂等；
   - monotonic；
   - component-aware；
   - conflict-safe；
3. BUSY不会触发quarantine；
4. 成功export的archive一定可被同版本load和verify；
5. terminal transaction cleanup失败不阻断启动；
6. 2048永远不保存半结算棋盘；
7. DB commit不会因journal cleanup失败被误报失败；
8. journal validity不依赖当前时间；
9. historical pending不会因“碰巧可解析”被激活；
10. active data completeness与forensic completeness分开；
11. Sokoban durable return session的identity/ruleset/lifecycle完整；
12. backend lease覆盖所有活跃worker；
13. 三平台当前CI通过；
14. main启用required checks；
15. wheel/sdist只读工作目录smoke通过；
16. storage/archive核心覆盖率达到目标；
17. static type gate通过；
18. hash-locked安装通过；
19. LICENSE和素材权利完成；
20. 默认不联网、默认不遥测。

覆盖率不能代替：

- multiprocess barrier；
- crash injection；
- partial recursive delete；
- filesystem lock timeout；
- archive max-bound round-trip；
- gameplay frame-state model。

---

# 12. 性能与稳定性门槛

1. pygame主线程不得执行：
   - SQLite；
   - directory scan；
   - archive I/O；
   - lock等待；
   - transaction recovery；
2. score/state enqueue p99 ≤2 ms；
3. status getter p99 ≤0.2 ms；
4. score lock文件数量固定有界；
5. state marker扫描有文件数和时间预算；
6. 100次游戏切换：
   - thread count回到基线；
   - FD不增长；
   - Surface和内存稳定；
7. 30–60分钟soak：
   - pending最终收敛；
   - lock/temp/marker不无限增长；
8. 10k+ attempts：
   - export可成功且self-readable；
9. recovery evidence多轮export/import：
   - 不递归指数增长；
10. 满盘、只读、坏DB、坏archive、坏journal：
   - 不删除最后有效副本；
   - 返回structured error；
   - 有用户可见恢复入口。

---

# 13. 推荐实施顺序

## M0：发布阻断

按顺序：

1. Progress aggregate/component resolver；
2. Store component replay；
3. Legacy upgrade共享resolver；
4. State BUSY/INVALID recovery分型；
5. Reject temp发布协议；
6. Archive writer/reader预算统一；
7. Terminal cleanup namespace；
8. 2048 settled autosave；
9. 对应fault/multiprocess/model tests。

## M1：协议收口

- commit/cleanup分离；
- timestamp确定化；
- event reducer；
- shutdown lease；
- striped locks；
- verify-archive；
- historical pending classify-first；
- active/forensic completeness；
- evidence dedup；
- transaction parser；
- Sokoban session；
- 2048 old slot迁移。

完成后冻结：

```text
score spool schema 2
state journal schema 3
state receipt schema
Archive v3 reader contract
ImportTransaction v2/v3 reader
2048 slot schema 5
Sokoban practice-return slot schema 1
```

冻结后只允许：

- 数据丢失修复；
- 安全修复；
- 已发布格式兼容；
- 明确版本升级。

## M2：正式发行基础

- required checks；
- hash locks；
- static typing；
- coverage；
- LICENSE；
-三平台安装包；
-签名；
-校验和。

## M3：本机桌面体验

- 数据管理页；
- 档案页；
- input/remap/controller；
- audio；
- DPI/font；
- accessibility；
- settings；
- progress dashboard。

## M4：单机内容

- 五款游戏本地模式；
- 编辑器；
- replay；
- 本机成就；
- 离线日期挑战；
- 本地化。

---

# 14. 最终判断

当前项目相较上一轮继续明显进步：

- 上轮两个P0的直接根因已经修复；
- 普通orphan、clock、event、transaction、archive和路径边界更完整；
- 2048 claim/slot resolution与Sokoban progress/session开始形成真正产品能力；
- CI和release验证继续保持；
- 项目明确维持local-first，而没有被可选Flask适配器带偏。

但目前还不能宣称“数据层已经完全闭环”。

本轮最关键的认识是：

> **局部修复已经越来越正确，剩余风险主要来自协议之间的组合。**

尤其是：

```text
aggregate ↔ component replay
BUSY ↔ quarantine
export budget ↔ reader budget
terminal phase ↔ recursive cleanup
animation state ↔ save state
```

下一轮应只关闭这些组合不变量，随后停止继续增加持久化复杂度。

最终产品路线应从“不断强化底层恢复协议”逐步转向：

- 本机数据管理；
- 档案体验；
- 输入、音频、显示和无障碍；
- 可重放、可测试的纯逻辑engine；
- 五款游戏的单机内容；
- 三平台桌面发行。

这比把项目改造成联网竞技平台更符合当前代码、用户需求和项目规模。
