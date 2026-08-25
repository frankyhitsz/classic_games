# Classic Games Hub 第十四次代码审查与本地优先优化任务书

> 审查日期：2026-08-25
> 当前基线：`main` 分支 commit `9302416e3f650a35f08999c33cf5a77400b7c312`（`9302416`）
> 对比基线：第十三次审查 commit `05ba2598bca4cc595bad15f201e75780bf7a2e5b`
> 增量提交：5 个；核心实现提交为 `6aad435f65dafd493e389f4b1b40547849b3695c`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 明确非目标：账号、云端排行、匹配、赛季、实时联机、反作弊、强制联网与默认遥测

---

## 0. 执行摘要

本轮修复总体有效。第十三次审查提出的大部分协议与恢复问题已经进入真实代码和测试：

- 普通客户端和可选 Flask 会在数据库初始化前检查并恢复 ImportTransaction；
- application/maintenance lock、SQLite sidecar、legacy pending 和 import roots 已进入 reserved-path policy；
- ImportTransaction v2 为 rollback DB、staged file、before image 和目标原状态记录 size/hash；
- Recovery export 使用 no-follow、单链接普通文件读取，并统一输出 POSIX archive path；
- Archive manifest format 2 会检查 application、ruleset、table/pending/recovery counts 和 complete；
- `restore-replace` 会移除 active score/state journal、reject/restore artifact 和 `pending_saves.json`；
- Reject marker 现在使用 temp→replace 和 marker hash；
- 相同 state operation identity、不同 payload 会报告冲突；
- 2048 在 claim ACK 前保持输入门禁，关闭时优先同步发布 released intent；
- Optional Flask 已加入 application lifetime lease；
- 数据 CLI 新增 transaction list/export/recover 和 recovery cleanup；
- Tetris 新增 7-bag、ghost、hold，并将 ruleset 升到 `tetris-assist-3`；
- Sokoban 新增已解锁关的 practice selector；
- 当前 head 的 GitHub Actions 已完成并通过。

现有架构无需推倒：

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
    ├── archive snapshot
    └── import phase transaction

Optional Flask
    └── 仅作为本机成绩 API 调试适配器
```

### 当前最重要的新发现

本轮仍有三个发布阻断级组合问题：

1. **Import recovery 与 application shared lease 的交接不是原子的。**恢复锁释放后、shared lease 获得前，另一个维护进程可以开始 Import、崩溃并留下新 transaction；原应用随后会在不再次检查 transaction 的情况下启动。
2. **Rejected state 的 previous pending 仍可能在 marker 发布前丢失。**Incoming journal 已替换 previous 后，如果进程在 permanent StoreError 与 reject marker 原子发布之间崩溃，previous 只存在于内存；重启后会用 `previous=None` 隔离 incoming。
3. **2048 将 `superseded` 的 owner claim 当成成功。**两个窗口同时认领空 slot 时，落败窗口可进入 `ready` 并接受移动；下一次保存再发现 `slot_in_use`，刚发生的移动可能被远端棋盘覆盖。

另外，存储协议封板前应解决两项备份兼容性：

- 上一版本生成的 v2 archive 因缺少当前 `manifest.format_version`，只能 merge，不能再 `restore-replace`；
- 当前 manifest 要求五个 ruleset 与当前目录完全一致，未来任一游戏规则版本变化都会拒绝今天生成的完整 archive。

### 总体结论

> **普通玩法和正常保存主路径继续收敛；剩余主要风险是跨进程恢复协议、备份长期兼容，以及新增练习/所有权状态机的组合边界。**
>
> **建议完成本任务书 P0 与关键 P1 后正式冻结存储协议，不再增加新的 journal、receipt 或 migration 层。**

---

## 1. 审查范围、方法和限制

### 1.1 已审查

- 当前 `main`、提交历史和与上一轮基线的 diff；
- `game_service/maintenance.py`
- `game_service/import_transaction.py`
- `game_service/data_cli.py`
- `game_service/local_backend.py`
- `game_service/store.py`
- `game_service/save_slot_validation.py`
- `game_service/catalog.py`
- `server/app.py`
- `client/launcher.py`
- `client/common/ui.py`
- Tetris、Snake、2048、Sokoban、Zuma；
- `tests/test_storage_v11.py`
- storage v2–v10、regression、stress、release runner；
- `.github/workflows/ci.yml`
- README、task、审查答复、NOTICE、constraints 和 pyproject。

### 1.2 组合状态复核

重点检查：

```text
Import recovery → 释放 exclusive locks → 获取 shared lease
state put → permanent StoreError → reject marker
两个 2048 窗口同时认领 NO_SLOT
旧 archive → 新 manifest reader
ruleset 升级 → 旧完整备份
practice selector → campaign ledger / unlock
transaction hash verify → 实际读取 bytes
legacy transaction v1 → 自动 rollback
restore-replace → unknown schema objects
```

### 1.3 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原缺陷主路径和关键边界均已闭环 |
| **基本到位** | 原问题主要关闭，但组合边界仍需修补 |
| **部分到位** | 已有结构/API，但端到端不变量仍不成立 |
| **代码路径确定** | 可直接由当前控制流推出 |
| **状态模型确定** | 按合法调度顺序可得到错误最终状态 |
| **兼容性缺口** | 不一定影响今天的空白安装，但会影响升级/恢复 |
| **待真机验证** | 需 Windows/macOS/Linux 实机证明 |
| **产品任务** | 不是当前 Bug，但符合本地单机定位 |

### 1.4 限制

本次审查环境没有本地仓库归档和完整 pygame/Flask 执行环境，因此没有在本容器中重跑全部测试。

可确认：

- 当前 head 的 GitHub Actions 为 completed/success；
- 仓库自测记录为 181 项 storage、107 项 gameplay，并完成 stress 与 release profile；
- 上述数字按项目自测/远端 CI 证据处理，不冒充本次容器独立测量。

---

## 2. 第十三次审查问题验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| 普通启动前恢复 unfinished import | **基本到位** | `recovered_application_session` 已有；exclusive→shared 交接仍有竞态 |
| Reserved control path | **修复到位** | DB sidecar、locks、legacy pending、import roots已保护 |
| Export no-follow recovery | **修复到位** | symlink/hard-link/special file被拒绝或记 omission |
| Import lexical symlink alias | **修复到位** | lexical lstat + allowlist + resolved containment |
| Complete archive覆盖 protocol artifact | **基本到位** | reject/restore/legacy source会恢复或报告；migrated legacy evidence仍未盘点 |
| Replace完整 active namespace | **基本到位** | journal/marker/legacy source已清理；unknown view/trigger/index仍保留 |
| Reject marker原子/hash | **部分到位** | marker自身已修；previous在marker准备前仍有窗口 |
| Windows evidence POSIX path | **修复到位** | exporter使用POSIX路径 |
| Transaction staged/before hash | **基本到位** | hash已有；verify与实际读取仍是两个步骤 |
| Duplicate FileOperation | **修复到位** | 同target同bytes去重，不同bytes拒绝 |
| Manifest consistency | **基本到位** | counts/complete/application已校验；ruleset策略过严、无reader版本 |
| Flask application lease | **修复到位** | create_app参与lease；`init_db()`仍是直写辅助入口 |
| Launcher maintenance UI | **修复到位** | retry/exit恢复界面 |
| Same state identity conflict | **修复到位** | 相同order、不同hash返回409 |
| 2048 claim ACK gate | **基本到位** | claiming期间禁输入；superseded仍被当成功 |
| 2048 close release durability | **修复到位** | `publish_slot_intent()`先进入durable journal |
| Replace unknown tables | **基本到位** | unknown table会drop；其他schema object仍在 |
| Recovery status no-follow | **修复到位** | lstat/os.walk(followlinks=False) |
| Schema version同snapshot | **修复到位** | schema_meta在read transaction中读取 |
| Export no-clobber | **修复到位** | no-force使用hard-link发布；无hard-link文件系统兼容性待改善 |
| Evidence metadata strict | **修复到位** | current manifest要求size/hash/content |
| Bounded archive memory | **按当前协议接受** | v2仍一次性JSON，但有预算与基准 |
| Transaction management CLI | **修复到位** | list/export/recover |
| Backup retention | **部分到位** | cleanup已有；inventory与GUI仍不足 |
| Lock symlink | **POSIX到位** | Windows reparse-point仍缺专门验证 |
| Official writers maintenance contract | **基本到位** | LocalBackend/Flask参与；direct helper仍可绕过 |
| Required checks | **未完成** | main仍未保护 |
| Hash/platform locks | **未完成** | 精确版本闭包已有，无hash与三平台锁 |
| LICENSE | **未完成** | NOTICE已有，许可证需权利人决定 |

---

# 3. 当前发布阻断问题

## 3.1 CG14-F01：Import recovery 与 application lease 存在交接竞态

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`maintenance.recovered_application_session`

当前：

```text
取得 application exclusive
→ 取得 maintenance exclusive
→ recover_import_transactions
→ 释放 maintenance
→ 释放 application
→ ApplicationSession.acquire(shared)
```

### 失败顺序

```text
进程 A：完成 recovery，释放 exclusive locks
进程 B：取得 exclusive locks，开始 import
进程 B：DB_APPLIED 后崩溃
进程 A：取得 shared application lease
进程 A：打开部分导入状态
```

代码注释说明 maintenance 在窗口中会优先，但没有处理 maintenance **崩溃并留下 transaction** 的情况。

### 修复要求

推荐循环协议：

```text
recover under exclusive locks
→ acquire shared application lease
→ while shared lease held，重新检查 transaction roots
→ 若发现 transaction：释放shared，重新进入exclusive recovery
→ 无transaction才返回
```

或增加不可分割的 startup gate。

### 验收

- 在 handoff 每个指令点强制暂停；
- maintenance 完成：应用看到完成状态；
- maintenance 崩溃：应用不打开 DB，先 rollback；
- corrupt transaction：显示 recovery-required。

---

## 3.2 CG14-F02：Rejected state 的 previous pending 仍可在 marker 前丢失

- **优先级**：P0
- **证据**：状态模型确定
- **位置**：
  - `PersistentStateOutbox.put`
  - `_durable_state_write`
  - `reject_and_restore_if_current`

### 当前顺序

```text
读取 previous
→ incoming 替换 canonical journal
→ 写 SQLite
→ permanent StoreError
→ 创建 reject marker（marker中才保存previous）
```

### 崩溃窗口

若在 incoming 替换后、reject marker成功发布前崩溃：

- canonical只有被拒绝的 incoming；
- previous 只在进程内存；
- 重启 replay 再次被拒绝；
- replay调用 reject 时 `previous_operation=None`；
- incoming被隔离，previous没有恢复来源。

典型场景：

- 2048 `slot_in_use`
- owner epoch/CAS拒绝
- 不能在纯validator阶段判定的DB级永久冲突

### 修复要求

Reject transaction必须在替换 canonical **之前**准备：

```text
PREPARED(previous,incoming)
→ publish incoming
→ apply DB
→ COMMITTED / REJECTED
→ restore previous or remove marker
```

更稳妥的长期方案是每operation独立文件+winner pointer，但不建议在本轮再新增一套抽象；优先用单key prepared marker关闭窗口。

---

## 3.3 CG14-F03：有效 `.reject-*.tmp` 会被直接隔离，而不是完成恢复

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`_recover_reject_transactions`

当前启动时对所有：

```text
.reject-*.tmp
```

直接移动到 quarantine。

如果进程在：

```text
temp完整写入并fsync
→ 尚未os.replace为.txn
```

之间崩溃，temp内已经有有效 version 2 marker和previous，但恢复器不验证它，而是隔离。

### 修复

- 尝试解析/验证 temp；
- marker hash有效且没有对应 final时，原子提升为 final并完成 transaction；
- 无效/半写 temp才隔离；
- previous同时进入 non-durable fallback。

---

## 3.4 CG14-F04：2048 把 superseded owner claim 当成成功

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `LocalBackendClient._durable_state_write`
  - `Game2048._poll_slot_save`

Backend可能返回：

```json
{
  "ok": true,
  "superseded": true
}
```

2048当前只检查：

```text
result.ok is false?
```

否则视为成功，并把 `claiming → ready`。

### 两窗口场景

```text
A、B都读取NO_SLOT
A、B分别提交owner claim
B的revision更高并成为winner
A收到superseded
A仍进入ready并接受移动
A下次autosave才收到slot_in_use
A重新加载B的棋盘
A刚发生的移动丢失
```

### 修复要求

Claim成功必须证明：

```text
state_apply == committed
superseded != true
authoritative owner_token == self token
authoritative owner_epoch == expected epoch
```

不满足时重新加载或进入 conflict，绝不开放输入。

---

# 4. 高优先级兼容性与恢复问题

## 4.1 CG14-F05：Durable-pending claim 的 SUPERSEDED/RECOVERY状态未完整处理

`_poll_slot_save_status()`主要处理：

- COMMITTED
- PERMANENT_FAILURE
- QUARANTINED

尚未完整处理：

- SUPERSEDED
- RECOVERY_REQUIRED
- NON_DURABLE_PENDING

结果可能是：

- claiming永久等待；
- 或错误开放；
- 用户不知道应重试还是返回。

应建立明确的 owner-claim state machine。

---

## 4.2 CG14-F06：上一版本 v2 archive不能再做replace restore

Current reader对缺少 `manifest.format_version` 的旧v2 archive：

```text
legacy_manifest = true
complete = false
```

因此仍可 merge，但不能 `restore-replace`。

这意味着上一版本曾被认为“完整”的备份，在本版本不能恢复到精确时点。

### 要求

新增安全 upgrader：

```text
classic-games-data upgrade-archive old.json new.json
```

流程：

- 验证旧hash；
- 完整语义校验；
- 重建current manifest；
- 显示无法证明的部分；
- 只有能证明complete时才允许replace。

---

## 4.3 CG14-F07：Manifest要求所有ruleset与当前目录完全相等

Current manifest validator要求：

```text
archive.application.rulesets == current catalog rulesets
```

未来只要某一款游戏从：

```text
tetris-assist-3 → tetris-assist-4
```

今天生成的archive就会被整体拒绝，即使：

- 只想恢复Snake；
- rows自身带历史ruleset；
- 旧记录本来可以作为legacy history保留。

本轮Tetris刚发生ruleset升级，说明规则版本变化不是理论问题。

### 正确策略

- Manifest记录“导出时目录版本”，不作为全局等值门禁；
- Row按自身ruleset验证；
- 已知历史ruleset允许恢复为legacy；
- 未知ruleset保留但不参与当前排行榜，或要求显式兼容adapter；
- `restore-replace`允许历史记录，但不把其解释为当前规则记录。

---

## 4.4 CG14-F08：ImportTransaction v1仍会自动恢复未认证文件

Current reader兼容transaction version 1，但v1没有：

- staged hash
- before hash
- rollback DB hash

启动自动recovery可能把损坏的v1 before bytes恢复到active journal。

### 建议

- v1仅在安全条件满足时自动恢复：
  - rollback DB quick_check；
  - pending文件可通过正式parser；
- 不能证明的v1进入manual recovery；
- transaction export保留全部原件；
- 不静默使用未认证evidence bytes。

---

## 4.5 CG14-F09：Transaction先验证hash，随后又按路径重新读取

当前逻辑：

```text
_verify_file(path)
→ 稍后 path.read_bytes()
```

验证和实际使用不是同一descriptor/同一bytes。

同用户并发替换、文件系统异常或磁盘损坏可以发生在两步之间。

### 修复

- `_read_verified_file()`在一个descriptor内：
  - fstat；
  - 读取；
  - hash；
  - 返回同一bytes；
- publish/rollback直接使用返回bytes；
- rollback DB先复制到新临时文件并验证，再用该文件连接。

---

## 4.6 CG14-F10：多个unfinished transaction的rollback顺序没有lineage

Recovery会按目录名排序并rollback所有transaction。

若异常情况下存在相互嵌套的两个valid transaction：

```text
T1 rollback image = DB0
T2 rollback image = DB1
```

错误顺序可能最终得到DB1而不是DB0。

### 要求

- 正常协议保证最多一个active transaction；
- 发现多个时默认recovery-required；
- 或journal记录parent DB fingerprint/transaction ID，并严格逆序恢复；
- 禁止“猜顺序”。

---

## 4.7 CG14-F11：Windows reparse-point no-follow仍缺专门证明

Control-file open只在平台提供 `O_NOFOLLOW` 时使用该标志；当前symlink回归测试只在POSIX执行。

Windows junction/reparse-point alias需要：

- WinAPI `FILE_FLAG_OPEN_REPARSE_POINT`
- reparse attribute检查
- regular-file与owner/ACL策略
- application/maintenance/request/state lock真机测试

该项目前应标为“跨平台验证缺口”，不能宣称完全关闭。

---

## 4.8 CG14-F12：Normal outbox根目录可以是symlink/junction

Score/state outbox会：

```text
path.mkdir(exist_ok=True)
→ 在path下创建journal/lock/probe
```

但没有统一验证：

```text
pending/
pending-state/
quarantine/
migration-backup/
```

本身是真实、受控目录。

如果这些目录是alias，正常游戏保存仍可能写出数据目录。

### 要求

建立 `SecureDataDirectory`：

- lexical + resolved containment；
- no symlink/junction；
- regular directory；
- POSIX owner/mode；
- Windows reparse/ACL检查；
- 所有outbox/quarantine/migration复用。

---

## 4.9 CG14-F13：旧共享pending迁移后的evidence未进入inventory

Legacy migration会留下：

```text
pending_saves.json.migrated-<...>
```

当前recovery prefix和replace cleanup主要认识：

- `pending_saves.json`
- pending migration directories

这个migrated文件：

- 不再active；
- 但可能含旧成绩和昵称；
- 不进入status/export/cleanup统一清单；
- replace restore后仍可能留在数据目录。

应归为score migration evidence。

---

## 4.10 CG14-F14：无效legacy `.restore` 会被静默保留

Legacy restore scanner解析失败后主要continue：

- 不隔离；
- 不更新notice；
- 每次启动重复检查；
- 用户不知道有未恢复previous。

应移入专用quarantine，并提供恢复页面。

---

## 4.11 CG14-F15：Replace restore仍不是完整schema replacement

Current实现会drop未知table，但没有完整处理：

- unknown views
- unknown triggers
- unknown indexes
- malformed known objects
- sqlite_sequence精确状态

更可靠的replace语义：

```text
构建全新current-schema DB
→ 导入archive
→ integrity/schema fingerprint
→ 在application/maintenance锁内原子替换DB
```

旧DB整体保留为evidence/rollback image。

---

## 4.12 CG14-F16：Archive缺少app version与reader compatibility范围

Manifest目前有：

- app ID
- rulesets
- schema version

但没有：

- app version
- min_reader_version
- max_reader_version
- required capabilities
- archive compatibility adapter ID

应在archive v3设计中明确；不要继续在v2堆叠不兼容字段。

---

# 5. 新增玩法功能审查

## 5.1 Tetris：7-bag、ghost、hold主体正确

确认：

- bag为空时重建并shuffle七种方块；
- 14个连续draw分成两个完整bag；
- hold每个piece generation只能使用一次；
- empty hold消费next；
- ghost只绘制，不改变score/board；
- ruleset已升到 `tetris-assist-3`。

未发现确定性核心回退。

### 后续测试

- 10,000 bags每bag恰好七种；
- hold top-out；
- hold后gravity accumulator；
- ghost与hard-drop落点一致；
- seed+输入完整可复现；
- leaderboard按assist-2/assist-3分区。

---

## 5.2 Sokoban：practice selector存在状态污染

### CG14-F17：Practice clear会提高当前会话的unlocked level

`_check_win()`无论campaign/practice都会：

```text
completed_levels.add(level)
unlocked_level = max(completed_levels)+2
self.unlocked_level = max(...)
```

虽然practice progress写入独立key，内存里的 `unlocked_level` 已提高。

玩家可：

```text
完成最高已解锁practice
→ 解锁下一关
→ 再practice下一关
→ 同一会话链式访问全部关卡
```

这与“只访问已解锁关”不一致。

### 修复

- campaign ledger与practice ledger彻底分开；
- practice clear不改变campaign unlock；
- practice可以记录个人最佳，但不扩展选择范围。

---

### CG14-F18：Selector在paused/won状态仍可切关

Bracket、PageUp/Down、数字、K在BaseGame未消费时都会执行selector，没有要求：

```text
state == playing
```

因此可能：

- 暂停时直接载入新关并解除暂停；
- win overlay时绕过“下一关/重玩”确认；
- 丢弃当前局状态。

### 修复

- selector仅在明确practice selection overlay可用；
- active campaign切换使用destructive confirmation；
- paused/won下禁止直接切关。

---

### CG14-F19：选择第1关会重置ranked run而没有确认

`load_level(0)`会：

- begin新score session；
- 清空total score；
- 清空completed ledger；
- practice flag重置。

随后selector再设置practice true。

数字1或wrap到第1关可无确认丢弃当前campaign run。

应通过单独practice session对象，而不是复用campaign `load_level(0)`重置语义。

---

### CG14-F20：N skip与“已解锁选择”产品规则不一致

当前：

- selector只允许unlocked；
- N在active play中可以直接进入下一关practice，不检查unlock。

这可能是有意保留的“自由练习”，但产品规则应二选一：

1. Practice全部关卡自由访问；unlock只约束campaign；
2. Practice也受unlock约束；N不得绕过。

不要同时表达两套规则。

---

# 6. 2048剩余状态机问题

## 6.1 Claim superseded：见P0

必须以authoritative owner证明为准。

## 6.2 CG14-F21：Claim event状态集合不完整

`_poll_slot_save_status()`应处理：

```text
COMMITTED
DURABLE_PENDING
NON_DURABLE_PENDING
SUPERSEDED
RECOVERY_REQUIRED
PERMANENT_FAILURE
QUARANTINED
```

每种状态都有明确UI和下一步。

## 6.3 CG14-F22：Claim尚未完成时关闭可留下stale active owner

`before_close()`等待claim约1秒；仍未完成则不发布release。

若claim之后在后台/重启replay成功，slot会显示active owner，但原窗口已退出。

### 改进

- Claim intent与release intent使用同一semantic key；
- 退出时允许release以更高revision supersede claim；
- 或owner增加session expiry/closed marker；
- 不使用网络心跳，完全本地实现。

---

# 7. 启动、API和工程问题

## 7.1 CG14-F23：Standalone游戏没有maintenance/recovery启动页

Launcher已有安全重试页；但BaseGame在backend为空时直接：

```python
LocalBackendClient()
```

直接运行某款游戏时，maintenance busy或corrupt transaction可能输出traceback。

应复用统一 `create_local_backend_with_recovery_ui()`。

## 7.2 CG14-F24：`server.init_db()`仍绕过application lease

`create_app()`已正确参与lease，但公开helper `init_db()`直接构造store。

应：

- 改为内部migration primitive；
- 或在helper中取得recovered lease；
- 文档明确测试fixture例外。

## 7.3 CG14-F25：项目版本仍为0.6.0

当前已增加：

- archive manifest format
- import transaction v2
- Tetris ruleset assist-3
- 新玩法功能

但pyproject仍是0.6.0。

发布前应：

- 更新SemVer；
- CHANGELOG分版本；
- archive写入app version；
- release tag与包版本一致。

## 7.4 CG14-F26：Main仍未启用required checks

当前CI成功并不能阻止绕过。

至少要求：

- release-gate
- core-only
- Ubuntu
- macOS
- Windows

## 7.5 CG14-F27：Dependencies仍没有hash与per-platform lock

当前精确版本闭包明显优于范围安装，但还缺：

- `--require-hashes`
- Linux/macOS/Windows lock
- wheel availability验证
- pip/build bootstrap pin
- 安装manifest与SBOM一致性门禁

## 7.6 CG14-F28：LICENSE尚未完成

NOTICE已存在；许可证授予必须由权利人决定。

正式分发前完成：

- 代码权利确认；
- AI辅助代码政策；
- 名称/商标；
- 未来字体、图形、音频来源；
- LICENSE选择。

---

# 8. 中低优先级工程问题

## 8.1 Archive no-clobber依赖hard-link支持

无`--force`时通过hard link发布。

在不支持hard link的文件系统上会失败，而不是使用安全fallback。

可提供：

- 平台原子no-replace API；
- 或 `O_EXCL` final + bounded copy + partial marker。

## 8.2 Recovery cleanup有verify→delete时间窗

Cleanup验证hash后再调用递归删除；非协作进程仍可在两步之间添加文件。

建议：

- application lease继续保留；
- 删除前二次fingerprint；
- 逐文件no-follow delete；
- 最后只删除空目录。

## 8.3 模块过大

当前大致职责集中在：

- `local_backend.py`
- `store.py`
- `data_cli.py`
- `ui.py`
- `game_2048.py`
- `zuma.py`

建议行为不变地拆分，不再改协议：

```text
spool/
state_journal/
workers/
status/
repositories/
migrations/
archive/
import_executor/
```

## 8.4 类型和覆盖率

当前Ruff主要检查语法/未定义变量，coverage门槛仍为60%。

建议：

- pyright或mypy；
- core storage/archive ≥90%；
- 全项目≥80%分阶段；
- property/model tests补充，不重写现有unittest/regression/stress。

---

# 9. 项目完善方向：保持完全本地

## 9.1 本机数据产品

- 档案列表、新建、切换、重命名；
- export-before-delete；
- 家庭成员分档；
- 数据管理页面；
- transaction/quarantine/backup浏览；
- merge import与replace restore可视化；
- legacy记录浏览；
- 按游戏清理数据。

## 9.2 桌面可访问性

- 全键盘菜单导航；
- 手柄；
- 键位重映射；
- 高DPI；
- 可调整窗口；
- CJK字体fallback；
- 色弱图案；
- 高对比度；
- 降低动态；
- 用户可见日志和崩溃恢复页。

## 9.3 音频和设置

- BGM/SFX；
- 分组音量；
- 无音频设备安全降级；
- 窗口模式；
- 设置页面；
- 本机持久化。

## 9.4 确定性规则层

- Clock/RNG注入；
- 纯规则engine；
- seed+输入replay；
- pygame仅负责输入/渲染；
- 便于属性测试和关卡验证。

## 9.5 五款游戏

- **Tetris**：lock delay、可选strict rotation、规则页；
- **Snake**：速度、穿墙、障碍、双人同屏；
- **2048**：撤销、多存档槽、棋盘尺寸；
- **Sokoban**：正式选关、星级、最佳移动/推动、死锁、提示、编辑器；
- **Zuma**：reaction FSM、训练、色弱符号、原创道具、轨道编辑器。

## 9.6 完全离线扩展

- 本机成就；
- 日期seed挑战；
- 本地replay；
- 中英文；
- Windows/macOS/Linux桌面包；
- 签名与校验和。

---

# 10. 明确不建议建设

- 注册登录；
- 云端账号；
- 公网排行榜；
- 匹配；
- 赛季；
- 实时联机；
- 服务端权威判定；
- 反作弊；
- 在线商城；
- 强制联网；
- 默认遥测；
- 多设备云同步。

Flask继续只用于：

- 教学；
- 调试；
- 本机成绩API示例。

---

# 11. 完整优化任务清单

## 优先级定义

- **P0**：可能打开部分恢复状态、丢失唯一pending，或让未取得ownership的游戏接受输入。
- **P1**：升级/备份兼容、恢复硬化、正式发行基础。
- **P2**：维护性、桌面体验、性能和可访问性。
- **P3**：玩法内容和三平台发行。
- **S/M/L/XL**：相对工作量。

---

## 11.1 P0：恢复与ownership闭环

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG14-P0-01 | 修复application lease handoff | startup lease loop | maintenance崩溃后应用不打开部分DB | L |
| CG14-P0-02 | Shared lease后二次transaction检查 | recovery gate | 无transaction才返回backend | M |
| CG14-P0-03 | Handoff crash barrier测试 | two-process tests | 每个调度顺序均正确 | L |
| CG14-P0-04 | Reject PREPARED-before-replace协议 | reject txn v3 | previous先durable，再替换winner | XL |
| CG14-P0-05 | 有效reject temp自动提升 | recovery scanner | 完整temp不被误隔离 | M |
| CG14-P0-06 | Reject pre-marker non-durable后备 | backend lifecycle | 任何窗口均保留previous | M |
| CG14-P0-07 | Reject全阶段crash matrix | fault injection | 每个指令点恢复一致 | L |
| CG14-P0-08 | 2048 claim authoritative验证 | claim controller | superseded绝不进入ready | M |
| CG14-P0-09 | 处理SUPERSEDED/RECOVERY claim状态 | UI/state machine | 状态不挂起、不误开放 | M |
| CG14-P0-10 | 双窗口同时NO_SLOT测试 | integration | 只有一个owner可移动 | L |
| CG14-P0-11 | Claim后首个移动不丢失测试 | integration | loser无机会生成本地移动 | M |
| CG14-P0-12 | P0 release gate | CI job | 任一P0失败禁止release | S |

---

## 11.2 P1：兼容性、恢复硬化和发行基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG14-P1-01 | Legacy v2 archive upgrader | CLI command | 上一版完整备份可安全升级 | L |
| CG14-P1-02 | Legacy replace eligibility report | preview | 明确可证明/不可证明部分 | M |
| CG14-P1-03 | Historical ruleset compatibility | archive policy | 规则升级后旧archive仍可读 | L |
| CG14-P1-04 | Row-level ruleset adapter registry | service | 已知历史版本可恢复为legacy | L |
| CG14-P1-05 | Archive app version | manifest v3 | 包版本进入archive | S |
| CG14-P1-06 | min/max reader version | manifest v3 | 新旧reader行为明确 | S |
| CG14-P1-07 | Freeze archive v2 | ADR | v2只修bug，不再加字段 | S |
| CG14-P1-08 | Transaction v1安全恢复策略 | compatibility | 未认证bytes不自动发布 | L |
| CG14-P1-09 | V1 transaction export/manual recovery | CLI | 原件可保存与审查 | M |
| CG14-P1-10 | Verified-byte reader | import transaction | hash与使用同一bytes | M |
| CG14-P1-11 | Verified rollback DB snapshot | recovery | 校验后的文件才连接 | M |
| CG14-P1-12 | Transaction lineage | journal v3 | parent fingerprint/ID可验证 | M |
| CG14-P1-13 | 多transaction默认拒绝猜序 | recovery | 不自动使用不明顺序 | S |
| CG14-P1-14 | Windows reparse-point secure open | maintenance | junction/reparse不能冒充lock | L |
| CG14-P1-15 | Windows control-file真机测试 | CI | app/maintenance/request/state均覆盖 | M |
| CG14-P1-16 | Secure journal root | filesystem policy | pending根不能是alias | L |
| CG14-P1-17 | Secure quarantine/migration roots | filesystem policy | evidence不写出data dir | M |
| CG14-P1-18 | Migrated legacy score inventory | status/export | migrated文件可见 | S |
| CG14-P1-19 | Migrated legacy cleanup/replace | recovery | replace后旧个人数据不残留 | M |
| CG14-P1-20 | Invalid legacy restore quarantine | recovery | 不静默重复失败 | S |
| CG14-P1-21 | Fresh-DB replace restore | restore engine | 不保留unknown schema objects | XL |
| CG14-P1-22 | Post-restore schema fingerprint | verifier | tables/indexes/triggers/views精确 | M |
| CG14-P1-23 | Unknown schema evidence DB | recovery | 旧结构可保存而非混在active DB | M |
| CG14-P1-24 | 2048 claim durable-pending全状态 | controller | 每个SaveState有明确转移 | M |
| CG14-P1-25 | Claim-close superseding release | slot protocol | 退出后不会留下假active owner | M |
| CG14-P1-26 | Stale-owner恢复UX | UI | 明确接管/返回，不误报损坏 | S |
| CG14-P1-27 | Sokoban campaign/practice ledger分离 | game model | practice不污染ranked run | L |
| CG14-P1-28 | Practice不提高campaign unlock | game rule | 链式练习不能解锁下一关 | S |
| CG14-P1-29 | Selector状态门禁 | input | paused/won不直接切关 | S |
| CG14-P1-30 | Level-1 practice不重置run | session model | 切练习前确认或独立session | M |
| CG14-P1-31 | Sokoban N/selector规则ADR | product rule | 自由练习或unlock规则二选一 | S |
| CG14-P1-32 | Practice progress读取/移除 | product implementation | 不留下无消费者状态 | S |
| CG14-P1-33 | Standalone recovery启动页 | common bootstrap | 单游戏运行不traceback | M |
| CG14-P1-34 | `init_db`纳入lease或内部化 | API boundary | 官方helper不绕过维护协议 | S |
| CG14-P1-35 | LocalGameStore写边界文档 | API docs | 正式写入走service | S |
| CG14-P1-36 | Project版本升级 | SemVer | package/tag/changelog一致 | S |
| CG14-P1-37 | Archive写入package version | exporter | 恢复报告可解释来源 | S |
| CG14-P1-38 | Main required checks | repo settings | 合并不可绕过CI | S |
| CG14-P1-39 | Release tag policy | governance | release只来自受保护commit | S |
| CG14-P1-40 | Per-platform dependency lock | lock files | 三平台可复现 | L |
| CG14-P1-41 | Hash-locked dependencies | installer | 支持require-hashes | M |
| CG14-P1-42 | Pin build bootstrap | release | pip/build/setuptools可审计 | S |
| CG14-P1-43 | Installed manifest vs SBOM gate | CI | 二者一致 | S |
| CG14-P1-44 | Static type checking | pyright/mypy | service/archive边界通过 | L |
| CG14-P1-45 | Storage/archive coverage提升 | CI | core逐步达到90% | M |
| CG14-P1-46 | Handoff/reject model tests | model suite | 状态机不变量自动验证 | L |
| CG14-P1-47 | Recovery cleanup二次fingerprint | cleanup | verify后新增文件不被误删 | M |
| CG14-P1-48 | Hard-link-free no-clobber fallback | exporter | FAT/SMB仍安全导出 | M |
| CG14-P1-49 | Structured recovery logs | logging | transaction ID与phase可追踪 | M |
| CG14-P1-50 | Data recovery drill | release test | export→replace→replay闭环 | L |
| CG14-P1-51 | LICENSE权利核对 | owner checklist | 可安全选择许可证 | M |
| CG14-P1-52 | 加入LICENSE | owner decision | 正式分发前完成 | S |
| CG14-P1-53 | NOTICE素材门禁 | release check | 未登记素材阻止release | S |
| CG14-P1-54 | 名称/商标审查 | owner checklist | 发行风险有记录 | M |
| CG14-P1-55 | 存储协议封板ADR | architecture | P0关闭后冻结协议 | S |

---

## 11.3 P2：维护性、桌面体验与可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG14-P2-01 | 拆分store.py | repositories/migrations | 行为不变 | XL |
| CG14-P2-02 | 拆分local_backend.py | workers/spool/status | 边界清晰 | XL |
| CG14-P2-03 | 拆分data_cli.py | archive/planner/executor | 可独测 | L |
| CG14-P2-04 | DataManagementService | service API | GUI不直接操作SQLite | L |
| CG14-P2-05 | 数据管理页面 | desktop UI | status/export/import/recovery | XL |
| CG14-P2-06 | Transaction恢复页面 | UI | list/export/recover可视化 | L |
| CG14-P2-07 | 档案列表页 | profile UI | 新建/切换/重命名/进度 | L |
| CG14-P2-08 | Export-before-delete | profile flow | 删除可恢复 | L |
| CG14-P2-09 | Profile merge工具 | local UI | 冲突预览 | XL |
| CG14-P2-10 | GameState Enum | state model | 无散落magic string | L |
| CG14-P2-11 | AttemptSaveController | common layer | 五款保存状态统一 | XL |
| CG14-P2-12 | LocalStateController | common layer | progress/slot/settings统一 | L |
| CG14-P2-13 | InputManager | action map | 五款输入统一 | L |
| CG14-P2-14 | 完整IME控件 | widget | 光标/选择/组合输入 | M |
| CG14-P2-15 | 键位重映射 | settings UI | 冲突检测 | L |
| CG14-P2-16 | 键盘菜单导航 | focus model | 无鼠标完整操作 | L |
| CG14-P2-17 | 手柄支持 | controller | launcher/五款可用 | L |
| CG14-P2-18 | 音频系统 | BGM/SFX | 无设备不崩 | L |
| CG14-P2-19 | 设置页面 | UI | 音量/窗口/按键/辅助项 | L |
| CG14-P2-20 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG14-P2-21 | 高DPI | DPI handling | 图文清晰 | M |
| CG14-P2-22 | CJK字体fallback | licensed chain | 缺系统字体仍可读 | M |
| CG14-P2-23 | 色弱符号 | shapes/patterns | 颜色非唯一信息 | L |
| CG14-P2-24 | 高对比/降低动态 | accessibility | 动画强度可调 | L |
| CG14-P2-25 | Clock/RNG统一注入 | deterministic services | seed+输入可复现 | L |
| CG14-P2-26 | 纯规则Engine抽取 | rules modules | 核心测试无需SDL | XL |
| CG14-P2-27 | Launcher拆分 | app/state/render | main职责清晰 | L |
| CG14-P2-28 | 首页改为进度中心 | dashboard | best/recent/progress/continue | L |
| CG14-P2-29 | 静态Surface缓存 | profiler-driven | 有失效规则 | L |
| CG14-P2-30 | 可重复benchmark | CLI | OS/版本/seed完整 | M |
| CG14-P2-31 | 30–60分钟soak | stability suite | 线程/FD/内存稳定 | M |
| CG14-P2-32 | Archive v3流式设计 | ADR/prototype | 不修改已发布v2 | L |

---

## 11.4 P3：玩法内容与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG14-P3-01 | Tetris lock delay | optional ruleset | 测试完整 | M |
| CG14-P3-02 | Tetris strict rotation模式 | optional mode | 与assist分榜 | M |
| CG14-P3-03 | Tetris规则页 | UI | bag/hold/kick可查看 | S |
| CG14-P3-04 | Snake速度/穿墙/障碍 | local modes | 最佳分开 | L |
| CG14-P3-05 | Snake双人同屏 | local multiplayer | 无网络 | L |
| CG14-P3-06 | 2048撤销 | undo model | 与attempt/slot一致 | L |
| CG14-P3-07 | 2048多存档槽 | save UI | 查看/继续/删除 | L |
| CG14-P3-08 | 2048棋盘尺寸 | modes | ruleset分离 | M |
| CG14-P3-09 | Sokoban正式选关UI | progress UI | campaign/practice清楚 | L |
| CG14-P3-10 | Sokoban星级/最佳推动 | metrics | 规则明确 | M |
| CG14-P3-11 | Sokoban死锁检测/提示 | analysis | 可关闭 | XL |
| CG14-P3-12 | Sokoban编辑器 | XSB import/export | 地图验证 | L |
| CG14-P3-13 | Zuma reaction FSM | game model | 连锁可属性测试 | L |
| CG14-P3-14 | Zuma训练/选关 | practice | 不混入通关 | L |
| CG14-P3-15 | Zuma色弱辅助 | symbols | 球色可辨认 | M |
| CG14-P3-16 | Zuma原创道具/轨道 | content | 确定性测试 | XL |
| CG14-P3-17 | Zuma轨道编辑器 | path tool | 版本化/预览 | L |
| CG14-P3-18 | 本机成就 | local achievements | 无账号/遥测 | L |
| CG14-P3-19 | 离线每日挑战 | date seed | 完全离线 | L |
| CG14-P3-20 | 本地replay | command log | 复盘/调试 | L |
| CG14-P3-21 | 中英文 | localization | 长文本布局测试 | L |
| CG14-P3-22 | Windows桌面包 | installer/portable | 无Python运行 | XL |
| CG14-P3-23 | macOS/Linux桌面包 | app bundle/AppImage | 数据目录正确 | XL |
| CG14-P3-24 | 自动发布/签名/校验和 | release pipeline | smoke通过才发布 | L |

---

# 12. 必须新增的关键测试

## 12.1 Recovery handoff

```text
test_maintenance_crash_between_recovery_and_shared_lease_is_recovered
test_maintenance_success_in_handoff_is_observed
test_corrupt_transaction_created_in_handoff_blocks_startup
test_no_game_write_can_start_before_second_transaction_check
```

## 12.2 Reject protocol

```text
test_crash_after_incoming_replace_before_reject_prepare_preserves_previous
test_valid_reject_tmp_is_promoted_not_quarantined
test_partial_reject_tmp_is_quarantined
test_reject_marker_fsync_then_crash_restores_previous
test_reject_quarantine_then_crash_restores_previous
test_reject_previous_publish_then_crash_is_idempotent
```

## 12.3 2048 owner claim

```text
test_superseded_claim_never_enters_ready
test_two_no_slot_claims_have_one_authoritative_owner
test_superseded_claim_does_not_accept_move
test_claim_event_superceded_reloads_slot
test_claim_recovery_required_stays_blocked
test_close_during_pending_claim_publishes_or_cancels_owner
```

## 12.4 Archive compatibility

```text
test_previous_v2_archive_can_be_upgraded_for_replace
test_ruleset_bump_does_not_make_unrelated_game_archive_unreadable
test_historical_ruleset_rows_restore_as_legacy
test_unknown_ruleset_is_preserved_or_explicitly_rejected_per_policy
test_archive_reader_version_contract
```

## 12.5 Transaction compatibility

```text
test_v1_transaction_invalid_before_image_requires_manual_recovery
test_transaction_verify_and_use_same_bytes
test_multiple_transactions_require_lineage
test_windows_reparse_control_file_is_rejected
test_pending_root_symlink_is_rejected_during_normal_save
```

## 12.6 Sokoban

```text
test_practice_clear_does_not_increase_campaign_unlock
test_practice_ledger_does_not_change_campaign_total
test_selector_is_disabled_while_paused
test_selector_is_disabled_on_win_overlay
test_selecting_level_one_does_not_reset_campaign_without_confirmation
test_n_skip_matches_documented_practice_policy
```

## 12.7 Replace restore

```text
test_replace_removes_unknown_views_triggers_and_indexes
test_replace_uses_fresh_current_schema_database
test_migrated_legacy_score_file_is_inventory_evidence
test_replace_does_not_leave_old_personal_data_artifacts
```

---

# 13. 性能与稳定性门槛

1. pygame主线程不得执行：
   - SQLite；
   - journal scan；
   - archive I/O；
   - lock wait；
   - transaction recovery；
2. score/state enqueue p99 ≤2 ms；
3. status getter p99 ≤0.2 ms；
4. recovery handoff任意调度顺序均安全；
5. reject任意crash point都保留previous；
6. 两个2048窗口同slot只有authoritative owner可移动；
7. Archive跨ruleset升级仍可恢复历史数据；
8. 100次游戏切换：
   - 线程回基线；
   - FD不增长；
   - Surface/内存稳定；
9. 30–60分钟：
   - pending最终收敛；
   - transaction dirs不无限增长；
10. 满盘、只读、坏DB、坏archive、坏marker：
    - 应用安全拒绝或恢复；
    - 最后有效副本不静默删除；
    - 有用户可见恢复入口。

---

# 14. 稳定版本质量门禁

正式稳定版本至少满足：

- 全部P0关闭；
- recovery→shared lease无交接窗口；
- previous pending在reject前已durable；
- valid reject temp可恢复；
- 2048 superseded claim不会开放输入；
- 上一版archive有升级/恢复路径；
- ruleset变化不使完整备份整体失效；
- transaction v1有安全策略；
- Sokoban practice不污染campaign；
- 当前head三平台CI通过；
- main required checks开启；
- wheel/sdist smoke；
- type/lint/coverage/依赖审计；
- LICENSE/NOTICE完成；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代：

- cross-process barrier；
- crash injection；
- archive upgrade fixture；
- ownership race；
- Windows reparse test；
- 真实备份恢复演练。

---

# 15. 推荐实施顺序

## M0：关闭三个发布阻断

1. application handoff；
2. reject prepare-before-replace；
3. 2048 superseded claim；
4. 对应crash/concurrency测试。

## M1：兼容性封板

- old v2 archive upgrader；
- historical ruleset policy；
- transaction v1策略；
- verified-byte读取；
- secure journal roots；
- fresh-DB replace；
- Sokoban practice分离。

## M2：正式冻结存储协议

冻结：

```text
score spool
state journal
state receipt
archive v2
import transaction
2048 slot schema
```

只允许：

- 确定性数据丢失修复；
- 安全修复；
- 已发布兼容修复。

## M3：桌面体验

- 数据管理页；
- 档案页；
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

# 16. 最终判断

本轮仍然是明显进步：

- 第十三次审查中的多数问题是真实修复，不是文档宣称；
- 当前远端CI通过；
- Tetris新功能主体正确；
- 2048所有权体系接近闭环；
- Import/Archive已达到相当高的本地桌面数据工具成熟度；
- 五款游戏核心玩法没有发现新的大面积回退。

当前问题数量已经明显收敛。新发现的严重问题不是普通玩法bug，而是：

```text
恢复锁交接中的一次崩溃
reject准备之前的一次崩溃
两个窗口同时认领空slot
旧完整备份跨版本恢复
练习模式与campaign状态混用
```

完成M0与关键M1后，应停止继续扩展数据层。

> **下一阶段的正确方向是：冻结底层协议，把主要精力转向本机档案体验、可访问性、五款游戏内容和三平台桌面发行。**
