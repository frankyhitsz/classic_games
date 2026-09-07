# Classic Games Hub 第十八次代码审查与本地优先优化任务书

> 审查日期：2026-08-29  
> 当前基线：`main` commit `984cd846c1e967793af996b2c5a79f235680e50e`（`984cd846`）  
> 核心修复提交：`60ef5572421114224fd1f5343a344a5222388e0e`  
> 上一轮审查基线：`4e723c7c362d6959b820e70baf7fd5c54978576a`  
> 当前包版本：`0.9.0`  
> 当前 Archive：v4；SQLite schema：v7；2048 slot：v6  
> 产品定位：**本地运行、单机优先、默认不联网的经典小游戏合集**  
> 明确非目标：账号体系、公网排行、匹配、赛季、实时联机、云存档、反作弊、在线商城、强制联网和默认遥测

---

# 0. 执行摘要

本轮相对第十七次审查有明显、真实且大规模的改进。修复不是只写在 `task.md`、审查答复或测试里，而是进入了生产代码：

- progress resolver 已开始识别 aggregate/component；
- legacy v1 progress 升级开始复用共享 resolver；
- state reject temp 使用唯一名，并加入 lock、grace 和 fingerprint；
- ordinary BUSY 不再直接等同 INVALID；
- state parser 不再按当前 wall clock 判定既有 journal；
- state event 加入终态优先级；
- score lock 改为固定 256 stripes；
- backend worker 未结束时 application lease 由后台线程继续持有；
- Archive v4 拆分 active 与 forensic 完整性，支持 hash-only recovery inventory；
- export 发布前增加结构自读；
- 增加 `verify-archive`；
- terminal transaction 先迁出 active namespace；
- transaction journal 增加 shape、operation count、encoded-size 与 post-fstat 门禁；
- 2048 保存 settled snapshot、RNG state 和 move digest；
- Sokoban 增加 ruleset、attempt identity、ledger 和逐步移动验证；
- 远端调试 API 增加 token，HTTP 客户端也会发送 token；
- 当前 GitHub Actions 已成功完成七个 job。

因此：

> **第十七次审查提出的五个直接阻断族，主体修复已经到位。**

但当前仍不建议直接冻结为稳定版。新的组合审查发现六个发布阻断族：

1. **progress aggregate 对原始 `merge_progress` component 的重放仍会误判冲突；**
2. **Store 仍允许排序更旧、尚未见过的 merge 越过更新的 set，重新合并被 replacement 清除的进度；**
3. **reject final marker 仍在 key lock 外读取，随后在锁内使用旧快照；**
4. **slot quarantine 没有 expected hash/CAS，也没有 durable tombstone，可删除另一窗口刚写入的有效存档；**
5. **Archive v4 的 replace eligibility、升级路径和输出发布仍存在自证与崩溃窗口；**
6. **ImportTransaction 已提交后的终态清理仍与业务成功结果耦合，且 rollback 可能被重写成 COMPLETED。**

此外还有一批高优先级问题：

- 2048 在 current validator 和 owner logic 之后才判断旧 ruleset；
- v3 archive 没有 v3→v4 升级路径，无法继续用于 replace restore；
- `.preparing-*` 仍在 active namespace 内递归删除；
- recovery tree walker 未统一拒绝 Windows junction/reparse；
- 250 ms recovery budget仍可被单次1秒锁等待突破；
- semantic-key status可能被 absorbed component 的较高identity占据；
- 0.9 stripe lock 与0.8逐request lock不互斥；
- Sokoban validator未把history起点绑定到关卡初始状态；
- Sokoban异步save Future返回`None`仍会被当成成功；
- 恢复后的practice-return checkpoint会随着继续游玩逐渐陈旧；
- recovery/transaction evidence仍有parser与publisher不对称；
- import空间预估不包含全部before-image；
- 缺少真正的replace preview；
- Archive行schema、ruleset catalog和内存峰值仍需收口；
- HTTP backend没有实现`close()`；
- main分支当前显示为未保护，CI门槛仍偏低，类型、hash lock和LICENSE仍未完成。

### 当前建议

下一轮只处理：

```text
R0：六个发布阻断族
R1：恢复/Archive/升级兼容/生命周期收口
```

随后冻结现有持久化格式，不再继续扩展新的 journal、receipt 或 archive 字段；开发重心转向：

- 本机数据管理；
- 档案体验；
- 输入、手柄、音频、DPI和无障碍；
- 可重放的纯逻辑engine；
- 五款游戏的单机内容；
- 三平台桌面发行。

---

# 1. 审查范围、方法与限制

## 1.1 锁定版本

本任务书只针对：

```text
984cd846c1e967793af996b2c5a79f235680e50e
```

实施时必须在修复记录中继续写明最终SHA，避免将后续代码与本次结论混淆。

## 1.2 已检查范围

- `game_service/local_backend.py`
- `game_service/store.py`
- `game_service/data_cli.py`
- `game_service/import_transaction.py`
- `game_service/maintenance.py`
- `game_service/service.py`
- `game_service/save_slot_validation.py`
- `game_service/progress.py`
- `client/common/ui.py`
- `client/common/network.py`
- `client/launcher.py`
- `client/games/game_2048.py`
- `client/games/sokoban.py`
- Tetris、Snake、Zuma 的架构与回归边界
- `tests/test_storage_v15.py` 及既有 storage tests
- CI、package、README、CHANGELOG、NOTICE、审查矩阵
- 当前 GitHub Actions
- 当前 main branch protection/ruleset状态
- 仓库LICENSE状态

## 1.3 方法

- 对比 `4e723c7 → 984cd846` 的真实diff；
- 逐函数检查production path；
- score/state journal、receipt、SQLite、marker、transaction root状态机推演；
- 多进程锁时序推演；
- Archive export→inspect→verify→preview→import→replace闭环；
- 2048逐帧逻辑/动画/slot状态推演；
- Sokoban campaign/practice/session/progress/attempt identity跨重启推演；
- 兼容旧程序、旧archive、旧slot的升级推演；
- 当前测试覆盖与未覆盖组合对照；
- GitHub Actions和仓库设置核验。

## 1.4 限制

当前审查环境没有重新clone并独立执行完整pygame/storage/stress/release测试。

可以确认：

- 当前SHA与源码来自GitHub；
- 当前head的GitHub Actions成功；
- 仓库记录294项storage、107项gameplay、stress和release通过；
- 这些测试数字属于项目远端/本机记录，不表述为本审查环境独立测量；
- 本文“确定性代码路径”来自当前控制流和状态模型；
- Windows junction、杀毒占用、SMB/FAT等项目仍需实机故障测试。

---

# 2. 当前架构判断

现有架构仍适合本地小游戏，不需要联网平台化：

```text
Launcher / pygame games
    │
    ├── GameDataService
    ├── LocalBackendClient
    │     ├── read worker
    │     ├── write worker
    │     ├── SQLite
    │     ├── score spool
    │     └── keyed state journal
    │
    └── optional HttpBackendClient
          └── 仅用于本机/可信LAN调试成绩API

Data CLI
    ├── application lifetime lease
    ├── transition gate
    ├── maintenance lock
    ├── Archive v1–v4 reader
    ├── Archive v4 writer
    ├── ImportTransaction v1–v3
    └── recovery evidence/inventory
```

正确方向：

- 收口本机数据正确性；
- 冻结协议；
- 加GUI数据管理；
- 把游戏规则与pygame分离；
- 提升桌面体验；
- 增加本地玩法和三平台发行。

不需要：

- 云账号；
- 公网排名；
- 实时联机；
- 匹配服务器；
- 服务端权威玩法；
- 反作弊；
- 云同步；
- 默认遥测。

---

# 3. 第十七次审查修复验收矩阵

| 上轮主题 | 当前状态 | 本轮结论 |
|---|---|---|
| Absorbed set replay | **主体到位** | set component已可识别；原始merge component仍误判hash冲突 |
| Store set receipt lookup | **部分到位** | set会查merge receipt；旧merge越过新set仍可重新合并 |
| Legacy v1共享resolver | **到位** | v1 upgrade不再直接调用merge helper |
| BUSY不隔离ordinary state | **主体到位** | list/marker/restore timeout保留；final marker仍锁外读取 |
| Reject unique temp | **到位** | 唯一temp、digest lock、grace、fingerprint已加入 |
| State wall-clock parser | **到位** |既有journal合法性不再依赖当前时间 |
| Clock quarantine fail-closed | **到位** |隔离失败停止覆盖 |
| Event terminal precedence | **主体到位** |同identity不回退；跨identity authoritative winner仍有缺口 |
| Archive writer/reader budget | **普通export到位** |`upgrade-archive`未复用同一self-read gate |
| Archive deep verifier | **到位但未接入replace eligibility** |`verify-archive`存在；普通export仍硬编码active_data_complete |
| Terminal cleanup namespace | **主体到位** |active root先rename；finish结果与cleanup仍耦合 |
| 2048 settled autosave | **到位** |动画中退出使用pre-move settled snapshot |
| 2048 RNG state | **到位但需forward compatibility** |v6可恢复Python Random；未知algorithm仍当corrupt |
| Historical pending classify-first | **Archive planner到位** |2048 runtime旧ruleset slot仍先走current validator |
| Score striped locks | **当前版本到位** |锁数量有界；0.8/0.9跨版本不互斥 |
| Commit/cleanup分离 | **主体到位** |score/state cleanup异常不否认DB commit |
| Application lease延迟释放 | **到位** |worker未drain时后台继续持lease |
| Sokoban attempt identity | **到位** |统一restore helper同步context和private identity |
| Sokoban outer ruleset | **到位** |practice-return先检查outer ruleset |
| Sokoban transition validator | **大幅改善** |逐步walk/push验证；首状态未绑定initial board |
| Sokoban tombstone lifecycle | **部分到位** |恢复后不立即清除；继续游玩时checkpoint会陈旧 |
| Remote debug token | **到位** |server与HttpBackendClient均支持token |
| Main required checks | **未到位** |branch当前显示为未保护 |
| Hash locks / typing / LICENSE | **未到位** |仍属于正式发行门禁 |

---

# 4. 当前发布阻断族

# CG18-F01：Progress component关系仍不完整

- **级别**：P0
- **证据**：确定性代码路径
- **位置**：
  - `PersistentStateOutbox._operation`
  - `_progress_components`
  - `_component_relation`
  - `resolve_operations`

## 4.1 原始merge component重放会误判conflict

一个普通 `merge_progress` operation包含：

```text
top-level payload_hash = hash(完整operation)

components = [{
    operation_id: 当前operation_id,
    payload_hash: hash(component semantic)
}]
```

aggregate保存的是component semantic hash。

当前 `_component_relation(aggregate, operation)` 却比较：

```text
aggregate.components[operation.operation_id]
vs
operation.top_level_payload_hash
```

两个hash定义不同。

结果：

```text
merge M被aggregate吸收
→ 原merge M再次出现
→ aggregate里存在M的component ID
→ component hash != M的top-level hash
→ state_operation_conflict
```

这会影响：

- orphan replay；
- archive import；
- 多进程重复发现；
- DB不可用期间的重复pending；
- aggregate尚未清理时的重试。

## 4.2 Aggregate subset/superset也未按component集合判断

当前逻辑主要检查一个operation的top-level ID是否存在于另一个aggregate中，没有统一比较：

```text
existing component map
incoming component map
```

正确关系应为：

```text
相同ID、不同hash       → conflict
incoming components ⊆ existing → duplicate/superseded
existing components ⊂ incoming → incoming dominates
部分交叠且无冲突       → merge union
互不交叠               → merge union
```

## 4.3 修复原则

创建唯一的：

```text
resolve_progress_components(existing, incoming)
```

输入统一转换成component map：

- set：`{operation_id: top-level payload_hash}`
- merge：`{component_id: component_hash}`

任何live/import/migration/orphan路径不得自行解释component。

---

# CG18-F02：Store仍允许旧merge越过新set

- **级别**：P0
- **证据**：确定性代码路径
- **位置**：`LocalGameStore.apply_state_operation`

当前Store在：

```text
incoming_order < prior_order
```

时，如果incoming method是 `merge_progress`，直接：

```text
merge_stale = True
```

它没有判断prior method。

因此：

```text
旧merge A创建但尚未到达Store
→ 较新的set B提交，replacement清除/重置进度
→ Store删除旧state_merge_receipts
→ A后来首次到达
→ A order < B
→ 因A是merge，被merge_stale
→ A重新合入B
```

这违反outbox resolver已经声明的：

```text
merge + newer set → set LWW
```

并可恢复用户明确replacement掉的：

- 已完成关卡；
- unlock；
-最佳成绩；
- practice统计。

更严重的是，Store在`set_progress`提交时删除该semantic key的merge receipts，因此旧merge首次迟到时也没有component receipt可以阻挡。

## 正确方法矩阵

Store必须复用与outbox相同的矩阵：

```text
prior set + older merge        → superseded
prior merge + older merge      → merge_stale（仅未吸收component）
prior baseline + later merge   → merge
prior set + later merge        → merge（真正发生在set之后）
prior merge + later set        → set replacement
```

不能只看incoming method。

---

# CG18-F03：Reject final marker仍有锁外快照窗口

- **级别**：P0
- **证据**：确定性并发顺序
- **位置**：`PersistentStateOutbox._recover_reject_transactions`

当前final marker流程：

```text
锁外读取并parse .reject-*.txn
→ 从payload取key
→ 等待key lock
→ 使用之前读取的transaction对象执行恢复
```

可能出现：

```text
scanner读取prepared marker
→ writer取得key lock
→ writer把marker更新为rejected/完成恢复/删除marker
→ scanner随后取得key lock
→ 仍使用旧prepared快照
→ 可能恢复previous operation
```

当前marker writer、finalizer和scanner已经有相同key lock基础，但scanner没有把最终读取放在锁内。

损坏marker也可能在没有先取得对应key lock时进入quarantine。

## 修复

1. 从文件名提取64位key digest；
2. 先取得digest lock；
3. 在锁内：
   - lstat；
   - no-follow read；
   - post-fstat；
   - parse；
   - 校验payload key对应digest；
4. 再执行prepared/rejected恢复；
5. 离开锁前复查marker identity；
6. BUSY保留，绝不隔离。

---

# CG18-F04：Slot quarantine缺少CAS与durable tombstone

- **级别**：P0
- **证据**：确定性多窗口竞态
- **涉及**：
  - `LocalBackendClient.quarantine_slot_async`
  - `LocalGameStore.quarantine_save_slot`
  - 2048 historical/invalid slot
  - Sokoban invalid practice-return

当前调用只传：

```text
profile_id
game_id
slot_id
reason
```

Store在任务真正执行时读取“此刻”的slot，随后：

```text
插入 invalid_local_state
删除 save_slots row
删除 state receipt / merge receipt
```

没有核对客户端原先读取的：

- value hash；
- ruleset；
- state version；
- slot revision；
- owner epoch。

## 可复现顺序

```text
窗口A读取损坏/旧slot X
窗口A排队quarantine
窗口B写入新的有效slot Y
窗口A的worker开始执行
→ 读取当前Y
→ 把Y归档并删除
```

这会删除另一窗口刚写入的有效存档。

## 更深层问题

Quarantine是直接Store操作，不是ordered state operation：

- 没有state journal tombstone；
- 并发pending save-slot journal可在删除后再次replay；
- 删除没有winner identity；
-状态查询无法说明“slot被哪个operation有条件删除”。

## 修复

至少增加：

```text
quarantine_slot_if_current(
    semantic_key,
    expected_value_hash,
    expected_ruleset,
    expected_state_version,
    expected_slot_revision?,
    reason
)
```

返回：

```text
QUARANTINED
ABSENT
CHANGED
BUSY
FAILED
```

更完整方案：

- state journal schema下一版引入slot tombstone；
- tombstone有revision/operation ID/hash；
- stale save不能在tombstone之后复活；
-删除与evidence归档在同一SQLite事务；
-客户端遇CHANGED必须reload，不能继续删除。

---

# CG18-F05：2048旧ruleset判断仍在current parser之后

- **级别**：P0
- **证据**：确定性控制流
- **位置**：`Game2048._poll_slot_load`

当前顺序：

```text
读取state
→ validate_2048_state(current rules)
→ version/owner/value_hash检查
→ active owner conflict/takeover逻辑
→ 最后才比较saved.ruleset_version
```

因此旧ruleset slot：

- 如果恰好仍符合current validator：进入historical提示；
- 如果不符合current validator：直接当损坏并quarantine；
- 如果带active owner：可能先显示“另一个窗口使用中”，而不是历史版本提示。

这违反：

```text
旧ruleset原样保留，不用current validator解释
```

## 修复顺序

```text
读取outer slot envelope
→ 核对game_id
→ 核对outer ruleset
   ├─ current：进入current parser/owner/CAS
   ├─ explicit adapter：升级副本
   └─ historical：仅显示摘要/保留/二次确认归档
```

历史slot的归档还必须使用CG18-F04的CAS。

---

# CG18-F06：Archive v4自证、升级和发布仍未闭环

- **级别**：P0
- **证据**：确定性控制流与崩溃窗口
- **涉及**：
  - `export_data`
  - `upgrade_archive`
  - `restore_replace_data`
  - `_publish_output`
  - `export_transaction_data`

## 6.1 `active_data_complete`被硬编码为True

普通export会：

```text
读取表
结构验证
编码
_decode_archive自读
```

但不会执行：

```text
空current schema中的行语义/自然键/FK/完整import plan
```

却固定写：

```text
active_data_complete = True
replace_eligible = active journals complete && transaction inventory complete
```

如果DB被人工编辑、动态类型污染或存在语义异常：

```text
export返回成功且replace_eligible=true
→ verify-archive或restore-replace后续失败
```

成功export不应声明未证明的replace资格。

## 6.2 `upgrade_archive`未复用普通export自读门禁

`upgrade_archive`增加v4 manifest字段后：

- 检查byte limit；
-直接publish；
- 没有再次 `_validate_json_shape`；
- 没有 `_decode_archive(encoded)`；
-没有deep verify。

一个接近node limit的v2 source可能在增加v4字段后超过reader预算，生成同版本无法再读取的output。

## 6.3 缺少v3→v4升级路径

当前reader支持v3，`restore-replace`只接受当前v4，`upgrade-archive`却只接受v2。

结果：

```text
0.8.0生成的完整v3 backup
→ 0.9.0可inspect/merge import
→ 不能upgrade为v4
→ 不能restore-replace
```

这是备份迁移回归。

## 6.4 No-clobber publisher仍有hard-link崩溃窗口

`force=False`时：

```text
link(temp, output)
→ unlink(temp)
```

如果进程在两步之间崩溃：

```text
output nlink=2
hidden temp仍存在
→ archive reader要求single-link
→ output不可读
```

hidden temp也不在正式recovery inventory中。

`export_transaction_data`还有相同发布方式，而且没有普通archive publisher的filesystem fallback。

## 修复

建立唯一：

```text
ArchivePublisher / AtomicOutputPublisher
```

统一用于：

- export；
- upgrade；
- transaction evidence；
- user backup；
- manifest等。

发布前：

```text
encode
→ bounded structure validation
→ reader self-decode
→ deep semantic verify（需要replace eligibility时）
→ publish
```

发布后：

- reader必须可读；
- crash temp进入正式inventory；
-同版本能自动清理/恢复；
-无hard-link双链接窗口。

---

# CG18-F07：Transaction业务结果与terminal cleanup仍耦合

- **级别**：P0
- **证据**：确定性故障路径
- **涉及**：
  - `ImportTransaction.finish`
  - `cleanup_terminal`
  - `import_data`
  - `restore_replace_data`
  - `recover_import_transactions`

## 7.1 成功提交后cleanup失败会把成功报成失败

当前：

```text
DB commit
→ files publish
→ finish()
    → mark COMPLETED
    → rename to cleanup namespace
    → rmtree
```

如果terminal directory rename失败：

- DB和files已经提交；
- CLI却抛失败；
-用户可能重试整个import/replace；
-结果与磁盘真实状态不一致。

## 7.2 Rollback会被重写成COMPLETED

`import_data`异常路径：

```text
transaction.rollback()   # mark ROLLED_BACK
transaction.finish()     # mark COMPLETED
```

审计阶段被抹掉。

## 7.3 Recovery terminal cleanup仍可阻塞

`recover_import_transactions`对terminal root直接调用`cleanup_terminal()`；rename失败会传播并阻断启动/维护，而terminal业务本来已完成。

## 正确API

拆分：

```text
finalize_committed()
finalize_rolled_back()
cleanup_terminal_best_effort()
```

返回：

```text
business_outcome = COMMITTED | ROLLED_BACK
cleanup_state = CLEAN | PENDING | FAILED
cleanup_path = ...
```

业务成功不得因cleanup失败变成失败。

---

# 5. 其他高优先级问题

# CG18-F08：`.preparing-*`仍可能半删除并持续阻断启动

- prepare失败时在active namespace中 `rmtree(ignore_errors=True)`；
- startup recovery也直接递归删除`.preparing-*`；
- Windows locked file可留下partial preparation root；
- `has_import_transaction_roots()`会一直将任何preparing root视为active。

修复：与terminal root相同，先原子rename到cleanup namespace，再best-effort删除。

---

# CG18-F09：Recovery tree walker未统一拒绝Windows reparse/junction

Transaction root classifier已显式检查reparse flag，但：

- recovery export；
- recovery cleanup；
- transaction evidence export；
- status目录计算；

主要只检查：

```text
S_ISDIR
S_ISLNK
```

在Windows junction/reparse目录上的行为与安全策略不一致。

要求共享：

```text
SafeTreeWalker
```

逐层使用：

- lstat；
- FILE_ATTRIBUTE_REPARSE_POINT；
- no-follow；
- ownership/permission policy；
- dev/inode/size/mtime fingerprint；
- file/time/byte budget。

---

# CG18-F10：250 ms recovery budget仍可被一次1秒锁等待突破

Recovery循环会在进入lock前检查deadline，但 `_digest_lock()`仍使用固定1秒timeout。

因此一个contended marker即可让首轮恢复超过250 ms。

修复：

```text
lock_timeout = min(default_timeout, deadline - monotonic())
```

或恢复扫描使用纯nonblocking try-lock。

---

# CG18-F11：Semantic-key状态可能被absorbed component identity占据

Store对absorbed set返回：

```text
state_apply=duplicate
winning_operation_id=aggregate winner
```

但backend成功事件仍以incoming component本身的identity发出COMMITTED。

Event reducer先比较identity，再比较authoritative/precedence。

如果component identity大于aggregate winner：

```text
semantic key cache保留component identity
→ 后续DB authoritative receipt identity更小
→ 无法替换cache
```

要求分开：

```text
operation result event
semantic winner status
```

Store必须返回winner：

- revision；
- operation ID；
- payload hash；
- value hash。

semantic status使用winner identity，incoming result可标为absorbed/superseded。

---

# CG18-F12：0.9 stripe lock与0.8逐request lock不互斥

0.8.x使用：

```text
.<request_id>.lock
```

0.9.0使用：

```text
.score-lock-XYZ.lock
```

两版本同时运行时，对同一个canonical score file不会取得同一OS锁。

可能重新出现：

- publish/remove并发；
- scanner/quarantine并发；
- retry count覆盖；
- misnamed migration竞态。

`cleanup-score-locks`只能在应用全部关闭时删除旧锁，不能解决跨版本并发。

修复过渡：

- 0.9.x同时取得stripe lock和legacy request lock；
- 稳定顺序、去重、防Windows重复锁；
-至少保留一个兼容周期；
-或增加明确single-version protocol gate。

---

# CG18-F13：Transaction evidence parser仍不使用完整shape门禁

`_validated_transaction_evidence`：

- bounded read；
- SHA-256；
- `json.loads`；
-少量字段检查。

但没有：

- depth/nodes/string validator；
-完整file列表schema；
- `RecursionError` / `MemoryError`包装；
-每个embedded file hash复核。

应复用transaction/archive bounded JSON设施。

---

# CG18-F14：`export_transaction_data`仍有publisher与root安全差异

- no-clobber只用hard link；
- 不支持FAT/SMB等不支持hard link的filesystem；
- transaction root只拒绝symlink，没有统一拒绝Windows reparse；
- 失败temp不在inventory。

必须复用共享output publisher和SafeTreeWalker。

---

# CG18-F15：Import空间preflight仍低估真实峰值

当前估算主要包含：

- DB；
- archive若干copies；
- margin。

但transaction还会保存：

- 每个现有pending target的before-image；
- imported evidence target的before-image；
- staged file；
- rollback DB；
-用户可见backup；
- fresh DB；
- sidecar evidence。

正确流程：

```text
先生成只读plan
→ 统计所有operation.data
→ 统计所有existing target snapshot
→ 加DB rollback/fresh DB/user backup/sidecar/margin
→ preflight
→ 再开始staging
```

---

# CG18-F16：缺少真正的replace preview

`preview-import`只预览merge。

`restore-replace`不带`--apply`时只返回confirmation required；真正的replacement plan直到执行时才生成。

用户无法提前查看：

- 哪些表会被整体替换；
- 哪些pending会删除；
- 哪些evidence会写入；
- backup路径和空间；
- plan fingerprint。

增加：

```text
preview-replace
```

或让：

```text
restore-replace ARCHIVE
```

默认输出plan，并要求：

```text
--apply --plan-fingerprint <hash>
```

---

# CG18-F17：Archive v4行schema仍主要依赖SQLite试插入

当前semantic check覆盖了一部分：

- timestamps；
- JSON fields；
- current progress；
- current slots；
- profile name。

历史attempt/slot/progress的：

- identifier字符集；
- player/mode/status长度；
- ruleset长度；
- score/revision范围；
- text总量；
- preserve-only条件；

仍不够显式。

要求每表独立schema validator：

```text
ProfilesRow
AttemptRow
SettingRow
ProgressRow
SaveSlotRow
InvalidAttemptRow
InvalidLocalStateRow
```

历史不安全row应进入evidence-only，不进入active tables。

---

# CG18-F18：Archive v4仍是每游戏单一ruleset字符串

`ruleset_catalog`仍为：

```text
game_id → one ruleset
```

一个游戏存在多套历史ruleset时，无法表达：

- committed observed set；
- active pending observed set；
- evidence-only observed set。

因为v4已经进入main，不应原地改义。记录为冻结限制，未来只有真实需求时设计v5。

---

# CG18-F19：旧policy helper仍可绕过classify-first

`_restore_pending()`会直接parse并激活所有pending，`_restore_recovery_evidence()`直接写文件；当前主planner已有更严格的classify-first和transaction policy。

这些helper在当前主路径中看起来未被使用，但保留会增加未来误调用风险。

处理：

- 删除；
-或改为私有测试helper并加`raise Deprecated`；
-任何恢复必须经过planner + transaction。

---

# CG18-F20：Sokoban history起点未绑定关卡初始棋盘

Validator取得了：

```text
initial boxes
initial player
```

但没有实际比较sequence第一个状态。

因此一个：

```text
moves=0
history=[]
任意合法boxes/player
```

可通过验证。

即使history非空，只要后续每一步相互可达，也可以从任意合法中间局面开始。

要求：

```text
sequence[0] == parse_level(initial)
```

并验证：

- playing checkpoint的score应为预期值；
-history长度与moves一致；
-初始pushes=0；
-最终state非终局；
-current box/player与最后sequence一致。

---

# CG18-F21：Sokoban异步durability ACK过宽

`_poll_campaign_session_save`把以下结果视为成功：

```text
任何非dict结果
任何不是明确 {ok:false} 的dict
```

Future返回`None`也会确认active checkpoint。

另外：

```text
_capture_campaign_session
→ 发起async active save
→ 返回False并清掉内存snapshot
```

如果Future后来实际写入active checkpoint，玩家仍留在campaign；之后崩溃会恢复一个“从未真正进入practice”的旧checkpoint。

要求：

- 明确receipt schema；
- 只有`ok=true`且`committed`或`durable_pending`才确认；
- 保存期间进入“准备练习”状态；
- ACK后自动执行目标practice切换；
-失败/取消时明确写tombstone或保留状态；
-同slot active/tombstone使用single-flight newest queue。

---

# CG18-F22：Sokoban恢复点会随继续游玩变陈旧

恢复practice-return后，checkpoint保持active直到：

- 显式返回；
- 或正常close。

玩家继续移动、完成关卡、写入progress后，如果进程崩溃，下次仍恢复到旧checkpoint：

- 丢失恢复后的移动；
- old level ledger可能与新的progress不同步；
-同attempt identity可能再次提交旧run状态。

选择一种明确策略：

1. 恢复成功后，下一次settled campaign checkpoint替换旧return point；
2. 将其升级为普通debounced campaign autosave；
3. 玩家恢复并完成一次安全保存后tombstone旧return point。

---

# CG18-F23：Sokoban checkpoint体积与主线程验证

当前保存完整undo history，每一步复制所有box坐标，最多允许10,000步。

但state journal单文件上限64 KiB，实际远在10,000步前就会保存失败。

加载后的逐步可达性验证在游戏线程poll阶段执行，也可能造成明显卡顿。

优化：

- command/delta history；
-定期checkpoint；
-明确encoded size preflight；
-合理undo cap；
-后台validate；
-主线程只应用已验证结果。

---

# CG18-F24：2048未知RNG格式会被当作corrupt

Slot v6只记录Python `Random.getstate()`的JSON化结果，没有：

- rng algorithm；
- rng schema/version；
- producer Python implementation。

未来算法、不同实现或自定义RNG state无法恢复时，current validator会将整个slot当成损坏并quarantine。

应区分：

```text
CORRUPT
UNSUPPORTED_RNG
UNSUPPORTED_SLOT_VERSION
HISTORICAL_RULESET
```

不兼容数据默认保留，不能自动删除。

---

# CG18-F25：Merge component receipts会无限增长

为防止component replay，receipt现在与authoritative progress生命周期绑定，不再按365天任意删除。

正确性提高了，但长期每次merge都会新增一行，可能无限增长。

需要设计：

- per-key reset frontier；
- compacted component summary；
- bloom/hash-set不可用于强一致删除，需可证明结构；
-set replacement安全截断旧component；
- maintenance有界且不破坏幂等。

---

# CG18-F26：Archive v4内存峰值仍高

一次export同时持有：

- 多表Python rows；
- payload；
- archive；
- encoded bytes；
- `_decode_archive`产生的第二份对象；
- base64 recovery内容。

128 MiB文件可能需要数倍内存。

短期：

- 捕获并结构化`MemoryError`；
-机器可读memory estimate；
-降低默认上限或按表预算；
-进度报告。

长期Archive v5：

- sectioned/streaming；
-逐section hash；
-索引；
-中断恢复；
-随机访问；
-不把全部evidence base64进单JSON。

---

# CG18-F27：HttpBackendClient缺少close生命周期

HttpBackendClient已经：

- 使用ThreadPoolExecutor；
-支持token；
-异步请求。

但没有实现`GameDataService.close()`：

- executor无法主动shutdown；
-反复创建adapter会累积线程；
-BaseGame/launcher的通用close路径无法释放它；
-Protocol与实现不一致。

增加：

```text
close(cancel_pending, timeout) -> BackendCloseResult
```

并复用LocalBackend的生命周期语义。

---

# CG18-F28：Reverse proxy可改变remote_addr安全判断

Server按 `request.remote_addr` 判断loopback。

如果非可信reverse proxy把远端连接转发到本机，服务看到127.0.0.1，可能不要求token。

由于该API只是调试适配器，最简单策略：

- remote exposure启用时所有请求都要求token；
- reverse proxy默认不支持；
-仅在显式trusted proxy配置后解析forwarded headers；
-README明确plaintext LAN token边界。

---

# CG18-F29：Protocol typing仍不一致

例如：

- `GameDataService.close() -> None`；
- LocalBackend返回`BackendCloseResult`；
- HttpBackend没有close；
- quarantine API无expected identity；
- state/slot/archive receipt仍以裸dict为主。

优先引入：

```text
TypedDict / dataclass / Enum
```

覆盖：

- StateOperation；
- StateReceipt；
- SlotQuarantineReceipt；
- BackendCloseResult；
- ArchiveManifestV4；
- TransactionJournal；
- RecoveryReport；
- ImportPlan。

---

# CG18-F30：正式发行门禁仍未完成

当前事实：

- main branch `protected=false`；
- repository rulesets为空；
- coverage gate仍为60%；
- Ruff只启用少量E/F；
- 无pyright/mypy；
- 无三平台`--require-hashes` lock；
- 根目录无正式LICENSE；
- 字体/图形/名称/未来音频权利仍未结论。

这些不是运行时Bug，但在“稳定分发”前必须完成。

---

# 6. P0执行任务

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG18-P0-01 | Component map统一表示 | resolver module | set/merge都转map | M |
| CG18-P0-02 | Component overlap conflict | resolver | 同ID异hash拒绝 | S |
| CG18-P0-03 | Component subset duplicate | resolver | 原始merge重放不冲突 | M |
| CG18-P0-04 | Component superset dominance | resolver | aggregate扩展可证明 | M |
| CG18-P0-05 | Partial overlap union | resolver | 交换顺序结果一致 | M |
| CG18-P0-06 | Live/import/migration共享resolver | integration | 无第二套逻辑 | M |
| CG18-P0-07 | Raw merge replay regression | tests | pending aggregate+原merge为duplicate | S |
| CG18-P0-08 | Aggregate permutation model test | property tests | 幂等/交换/结合/冲突稳定 | L |
| CG18-P0-09 | Store method matrix | Store | prior set+older merge superseded | M |
| CG18-P0-10 | Store component-first处理 | Store | receipt后再order | M |
| CG18-P0-11 | Set replacement frontier | Store | old merge不复活 | M |
| CG18-P0-12 | Store/outbox differential test | model | 任意顺序winner一致 | L |
| CG18-P0-13 | Persisted DB restart test | integration | set reset跨重启保持 | M |
| CG18-P0-14 | Final reject filename digest lock | outbox | parse前持锁 | M |
| CG18-P0-15 | Final marker stable reread | outbox | 不使用锁外快照 | M |
| CG18-P0-16 | Marker identity post-check | outbox | writer更新后scanner不恢复旧值 | S |
| CG18-P0-17 | Corrupt marker lock-first quarantine | outbox | 未持锁不隔离 | S |
| CG18-P0-18 | Prepared→rejected race test | multiprocess | stale scanner无副作用 | M |
| CG18-P0-19 | Slot quarantine expected hash | API/Store | changed slot不删除 | M |
| CG18-P0-20 | Slot quarantine expected ruleset/version | API/Store | identity完整 | M |
| CG18-P0-21 | Quarantine result enum | service | QUARANTINED/CHANGED/ABSENT/BUSY | S |
| CG18-P0-22 | Durable slot tombstone设计 | ADR | stale pending不能复活 | L |
| CG18-P0-23 | Tombstone journal实现 | state protocol next version | order/receipt完整 | XL |
| CG18-P0-24 | Multiwindow quarantine race | tests | 新slot永不被旧请求删 | M |
| CG18-P0-25 | 2048 outer ruleset classify-first | game | historical不进current parser | S |
| CG18-P0-26 | Historical slot CAS archive | game/backend | 二次确认且changed时reload | M |
| CG18-P0-27 | Historical active-owner test | gameplay | 不先显示current owner冲突 | S |
| CG18-P0-28 | Export deep self-verify | archive writer | replace_eligible前空schema验证 | L |
| CG18-P0-29 | active_data_complete真实计算 | manifest | 不再硬编码 | S |
| CG18-P0-30 | Upgrade共享validate/publish | archive | v2/v3 output同版本可读 | M |
| CG18-P0-31 | v3→v4 upgrade | CLI | 完整v3保持replace资格 | L |
| CG18-P0-32 | v3 incomplete兼容映射 | CLI | 不伪造完整性 | M |
| CG18-P0-33 | AtomicOutputPublisher | common | 无双链接不可读窗口 | L |
| CG18-P0-34 | Archive crash publication test | subprocess | link/rename任一点kill可恢复 | M |
| CG18-P0-35 | Transaction evidence复用publisher | CLI | FAT/SMB fallback一致 | M |
| CG18-P0-36 | Import committed finalization | transaction | cleanup失败仍返回COMMITTED | M |
| CG18-P0-37 | Rolled-back finalization | transaction | phase保持ROLLED_BACK | S |
| CG18-P0-38 | Cleanup result结构化 | transaction | CLEAN/PENDING/FAILED | S |
| CG18-P0-39 | Terminal cleanup failure test | fault injection | 不诱发重复apply | M |
| CG18-P0-40 | P0 release gate | CI | 任一上述回归阻止release | S |

---

# 7. P1执行任务

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG18-P1-01 | Preparing root先rename再删 | transaction | partial delete不阻断启动 | M |
| CG18-P1-02 | Preparing cleanup namespace | classifier/status | 可见但non-active | S |
| CG18-P1-03 | SafeTreeWalker | common FS module | symlink/junction/reparse统一拒绝 | L |
| CG18-P1-04 | Windows junction实机test | CI | root/child junction均拒绝 | M |
| CG18-P1-05 | Recovery lock deadline传播 | state outbox | 总预算真实≤目标 | M |
| CG18-P1-06 | Nonblocking recovery try-lock | outbox | busy快速保留 | M |
| CG18-P1-07 | Winner identity in Store result | Store | revision/id/hash齐全 | M |
| CG18-P1-08 | Semantic status与operation event拆分 | backend | absorbed component不占winner status | L |
| CG18-P1-09 | Lower authoritative identity test | status | DB receipt可修正cache | M |
| CG18-P1-10 | Dual legacy+stripe lock过渡 | score outbox | 0.8/0.9同request互斥 | L |
| CG18-P1-11 | Cross-version process fixture | tests | 两版本协议模型无竞态 | L |
| CG18-P1-12 | Stripe transition移除条件 | ADR | 至少一个兼容周期 | S |
| CG18-P1-13 | Transaction evidence shape validator | CLI | depth/nodes/string有界 | M |
| CG18-P1-14 | Evidence file entries hash验证 | CLI | 内容和manifest一致 | M |
| CG18-P1-15 | Evidence parser异常包装 | CLI | 无裸Memory/Recursion | S |
| CG18-P1-16 | Transaction evidence root reparse拒绝 | CLI | Windows安全边界一致 | S |
| CG18-P1-17 | Exact import space estimator | planner | 统计staged+before+DB+backup | L |
| CG18-P1-18 | Space report UI字段 | CLI | 每项bytes可见 | S |
| CG18-P1-19 | `preview-replace` | CLI | 精确列出删除/写入 | L |
| CG18-P1-20 | Apply绑定plan fingerprint | CLI | preview后目标变化拒绝 | M |
| CG18-P1-21 | Archive per-table schemas | validator | 字段类型/长度/范围显式 | XL |
| CG18-P1-22 | Historical unsafe row evidence-only | planner | 不进入active table | L |
| CG18-P1-23 | Archive v4 catalog限制ADR | docs | 不原地改v4 | S |
| CG18-P1-24 | Archive v5 observed ruleset设计 | ADR-only | 按来源分类sets | M |
| CG18-P1-25 | 删除`_restore_pending`旧helper | cleanup | 无policy bypass | S |
| CG18-P1-26 | 删除直接evidence restore helper | cleanup | 全部经过transaction | S |
| CG18-P1-27 | Sokoban initial board binding | validator | sequence[0]等于level initial | M |
| CG18-P1-28 | Sokoban playing score invariant | validator | checkpoint score合法 | S |
| CG18-P1-29 | Async slot receipt schema | service | None不能代表成功 | M |
| CG18-P1-30 | Pending practice transition state | controller | ACK后才进入practice | M |
| CG18-P1-31 | Active/tombstone newest queue | controller | 同slot single-flight | M |
| CG18-P1-32 | Stale restored checkpoint策略 | controller |继续游玩不会回退 | L |
| CG18-P1-33 | Compact Sokoban undo history | format next version | 64KiB内可预测 | L |
| CG18-P1-34 | Sokoban encoded-size preflight | game | 保存前明确提示 | S |
| CG18-P1-35 | Sokoban background validation | worker | 主线程无长卡顿 | M |
| CG18-P1-36 | 2048 RNG algorithm metadata | slot next version | unsupported≠corrupt | M |
| CG18-P1-37 | Custom RNG capability contract | game | 无兼容state时记录不可重放 | S |
| CG18-P1-38 | Merge receipt compaction ADR | Store | correctness-preserving frontier | L |
| CG18-P1-39 | Merge receipt cardinality status | status | 可观察增长 | S |
| CG18-P1-40 | Archive MemoryError contract | writer/reader | 结构化失败 | S |
| CG18-P1-41 | Archive memory estimate | CLI | 发布前提示峰值 | M |
| CG18-P1-42 | Archive v5 streaming ADR | docs | 不修改v4 | L |
| CG18-P1-43 | HttpBackend `close()` | adapter | executor可drain/shutdown | M |
| CG18-P1-44 | HttpBackend lifecycle tests | tests | 反复创建线程不增长 | M |
| CG18-P1-45 | Remote exposure all-token mode | server | bind remote即所有请求要token | S |
| CG18-P1-46 | Trusted proxy显式配置 | server/docs | 默认不信任forwarded headers | M |
| CG18-P1-47 | Typed state operation | types | static checker通过 | M |
| CG18-P1-48 | Typed slot receipts | types | 无裸dict关键判断 | M |
| CG18-P1-49 | Typed Archive v4 manifest | types | reader/writer一致 | L |
| CG18-P1-50 | Typed transaction journal | types | schema分version | L |
| CG18-P1-51 | Protocol close返回类型统一 | service | local/http一致 | S |
| CG18-P1-52 | Main branch protection | GitHub settings | required checks启用 | S |
| CG18-P1-53 | Repository ruleset | GitHub settings | 禁止绕过main | S |
| CG18-P1-54 | 三平台hash lock | dependencies | `--require-hashes`安装 | L |
| CG18-P1-55 | Core coverage 90% | CI | storage/archive/transaction | M |
| CG18-P1-56 | 全仓coverage渐进80% | CI | 有期限与排除说明 | M |
| CG18-P1-57 | Pyright/mypy gate | CI | service/archive核心通过 | L |
| CG18-P1-58 | Ruff规则渐进扩展 | CI | 不一次引入海量style噪声 | M |
| CG18-P1-59 | LICENSE权利决定 | owner | 正式license文件 | S |
| CG18-P1-60 | AI生成代码来源记录 | docs | 可追踪贡献过程 | S |
| CG18-P1-61 | 名称/字体/图形/音效清单 | docs/build | 每项license/status | M |
| CG18-P1-62 | 持久化协议封板ADR | docs | R0/R1后冻结 | M |

---

# 8. P2：工程与桌面体验任务

## 8.1 模块拆分

| ID | 任务 | 目标 |
|---|---|---|
| CG18-P2-01 | `local_backend.py`拆分score outbox | 降低跨协议耦合 |
| CG18-P2-02 | 拆分state outbox/resolver/recovery | 单一resolver可独测 |
| CG18-P2-03 | 拆分backend status/workers/lifecycle | close/lease语义清晰 |
| CG18-P2-04 | `store.py`拆分repositories | schema/migration/query分开 |
| CG18-P2-05 | `data_cli.py`拆分archive package | reader/writer/verifier/planner/executor |
| CG18-P2-06 | transaction package化 | journal/recovery/cleanup |
| CG18-P2-07 | shared bounded FS/JSON utilities | 消除复制实现 |
| CG18-P2-08 | fault injection registry | 每个fsync/replace/lock点可注入 |
| CG18-P2-09 | structured logger | operation/request/transaction关联 |
| CG18-P2-10 | machine-readable diagnostics | UI和CLI共用 |

## 8.2 本机数据管理

| ID | 任务 | 目标 |
|---|---|---|
| CG18-P2-11 | DataManagementService | GUI不直接调用SQLite内部 |
| CG18-P2-12 | 数据状态页 | DB/pending/quarantine/recovery |
| CG18-P2-13 | Archive inspect/verify页 | 结构与深度验证区分 |
| CG18-P2-14 | Export页 | active/forensic选项清楚 |
| CG18-P2-15 | Import merge preview页 | 冲突、duplicate、历史evidence |
| CG18-P2-16 | Replace preview页 | 删除/写入/backup/空间 |
| CG18-P2-17 | Transaction recovery页 | evidence export与恢复 |
| CG18-P2-18 | Recovery cleanup页 | hash proof后apply |
| CG18-P2-19 | 打开数据目录 | 方便本机维护 |
| CG18-P2-20 | Export-before-delete | 删除档案前可恢复 |

## 8.3 档案体验

| ID | 任务 | 目标 |
|---|---|---|
| CG18-P2-21 | 档案列表页 | 新建/切换/重命名 |
| CG18-P2-22 | 每游戏进度摘要 | best/recent/progress/slot |
| CG18-P2-23 | 最后游玩和继续游戏 | local dashboard |
| CG18-P2-24 | 单档案导出 | 家庭成员独立备份 |
| CG18-P2-25 | 删除档案二次确认 | 明确影响范围 |
| CG18-P2-26 | 本地档案merge preview | 不静默覆盖 |
| CG18-P2-27 | guest转正式档案 | 本机迁移 |
| CG18-P2-28 | 隐私说明 | 数据只在本机 |

## 8.4 输入、音频、显示与无障碍

| ID | 任务 | 目标 |
|---|---|---|
| CG18-P2-29 | InputManager/action map | 五款统一输入 |
| CG18-P2-30 | 键位重映射 | 冲突检测/恢复默认 |
| CG18-P2-31 | 全键盘launcher focus | 无鼠标完整使用 |
| CG18-P2-32 | 手柄支持 | launcher+五款基础操作 |
| CG18-P2-33 | 输入设备提示切换 | 键盘/手柄图标 |
| CG18-P2-34 | IME文本控件 | composition/光标/选择 |
| CG18-P2-35 | AudioManager | master/music/effects |
| CG18-P2-36 | 无音频设备降级 | 不崩溃 |
| CG18-P2-37 | 统一设置页 | 输入/音频/显示/辅助 |
| CG18-P2-38 | 逻辑分辨率和viewport | resize不裁切 |
| CG18-P2-39 | fullscreen与高DPI | 三平台 |
| CG18-P2-40 | CJK fallback | 缺字体仍可读 |
| CG18-P2-41 | 高对比模式 | 信息不只靠颜色 |
| CG18-P2-42 | 色弱图案/符号 | Snake/Zuma/2048 |
| CG18-P2-43 | 降低动画 | 全游戏可用 |
| CG18-P2-44 | 大字号 | UI重排 |
| CG18-P2-45 | 明确focus ring | 键盘导航可见 |
| CG18-P2-46 | 闪烁/震动限制 | 无障碍安全 |

## 8.5 确定性、测试与replay

| ID | 任务 | 目标 |
|---|---|---|
| CG18-P2-47 | 统一Clock注入 | 不依赖全局time |
| CG18-P2-48 | 统一RNG接口 | 五款一致 |
| CG18-P2-49 | RNG state capability | save/replay明确 |
| CG18-P2-50 | InputCommand stream | 可复盘 |
| CG18-P2-51 | 纯2048 engine | 无SDL规则测试 |
| CG18-P2-52 | 纯Tetris engine | 旋转/lock/score |
| CG18-P2-53 | 纯Snake engine | grid/tick/collision |
| CG18-P2-54 | Sokoban board primitives | move/undo/validate |
| CG18-P2-55 | Zuma reaction FSM | chain/explosion/projectile |
| CG18-P2-56 | Replay格式 | ruleset+seed+commands |
| CG18-P2-57 | Replay viewer | pause/step/fast-forward |
| CG18-P2-58 | Model-based gameplay tests | 不变量 |
| CG18-P2-59 | Benchmark CLI | seed/OS/version完整 |
| CG18-P2-60 | 30–60分钟soak | FD/thread/memory稳定 |

---

# 9. P3：符合本地单机定位的玩法任务

## 9.1 Tetris

- lock delay；
- strict/assist旋转preset；
- 40 lines sprint；
- marathon；
- hold/next图形预览；
- 本地seed挑战；
- mode/ruleset独立最佳；
- replay；
- 可选ghost与降低动画。

## 9.2 Snake

- classic；
- 穿墙；
- 障碍；
- 可调速度曲线；
- 双人同屏；
- 本地seed挑战；
- 食物形状线索；
- mode独立最佳；
- replay。

## 9.3 2048

- undo；
- 多个本地slot；
- slot预览、复制、删除；
- 4×4/5×5模式；
- 规则独立排行；
- move history；
- replay；
- 旧ruleset slot浏览器；
- 不兼容RNG保留模式。

## 9.4 Sokoban

- 正式选关页；
- campaign/practice明显区分；
- 最佳步数/推动数；
- 星级；
- 可关闭死锁提示；
- XSB导入导出；
- 本地关卡编辑器；
- 自定义关卡与官方campaign隔离；
- 解法/replay；
- compact undo。

## 9.5 Zuma

- 明确reaction FSM；
- training；
- level select；
- 色弱符号；
- 瞄准辅助；
- 本地轨道编辑器；
- 原创道具；
- 关卡规则版本化；
- deterministic chain tests；
- replay。

## 9.6 全局离线内容

- 本机成就；
- 离线日期seed挑战；
- 本地统计；
- 本地复盘；
- 中英文；
- 主题/皮肤；
- 完全离线可用；
- 无默认遥测；
- replay文件导入导出。

---

# 10. 明确不建设

项目不应投入到：

- 注册登录；
- 云账号；
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
- 强制联网；
- 服务端重写玩法逻辑。

Optional Flask继续作为：

```text
本机或可信开发网络中的调试适配器
```

不是产品核心。

---

# 11. 必须新增的定向测试

## 11.1 Progress

```text
test_absorbed_raw_merge_replay_is_duplicate
test_component_subset_is_duplicate
test_component_superset_dominates
test_component_partial_overlap_unions
test_component_same_id_different_hash_conflicts
test_store_older_merge_cannot_cross_newer_set
test_store_later_merge_after_set_is_applied
test_set_replacement_survives_restart_and_old_merge_replay
test_outbox_store_import_method_matrix_differential
```

## 11.2 Reject recovery

```text
test_final_marker_is_read_only_under_digest_lock
test_prepared_snapshot_cannot_survive_writer_update
test_corrupt_final_marker_is_not_quarantined_while_busy
test_marker_inode_change_after_lock_is_preserved
test_rejected_marker_race_never_restores_stale_previous
```

## 11.3 Slot quarantine

```text
test_quarantine_changed_slot_returns_changed
test_quarantine_never_deletes_newer_value
test_quarantine_expected_ruleset_mismatch
test_quarantine_expected_hash_mismatch
test_quarantine_tombstone_blocks_old_pending_replay
test_multiwindow_2048_historical_quarantine_race
test_multiwindow_sokoban_invalid_session_quarantine_race
```

## 11.4 2048 historical/settled

```text
test_old_ruleset_is_classified_before_current_validator
test_old_ruleset_active_owner_does_not_enter_current_takeover
test_unsupported_rng_is_preserved_not_corrupt
test_mid_animation_close_all_directions
test_mid_merge_close_score_board_rng_consistent
test_settled_move_digest_roundtrip
```

## 11.5 Archive

```text
test_export_replace_eligibility_requires_deep_verify
test_upgrade_v2_self_reads_output
test_upgrade_complete_v3_to_v4
test_upgrade_incomplete_v3_remains_non_replaceable
test_upgrade_near_node_limit_fails_before_publish
test_output_kill_between_link_and_unlink_recovers
test_transaction_evidence_on_no_hardlink_filesystem
test_successful_export_is_inspectable_verifiable_and_replaceable
```

## 11.6 Transaction

```text
test_commit_cleanup_failure_returns_committed_cleanup_pending
test_rollback_phase_is_not_rewritten_completed
test_terminal_cleanup_failure_does_not_block_startup
test_preparing_partial_delete_moves_to_cleanup_namespace
test_recover_terminal_root_cleanup_is_best_effort
```

## 11.7 Cross-version locks

```text
test_08_request_lock_and_09_stripe_lock_share_transition_protocol
test_old_and_new_process_same_request_no_race
test_legacy_lock_cleanup_requires_all_apps_inactive
```

## 11.8 Sokoban

```text
test_history_first_state_equals_level_initial
test_empty_history_arbitrary_board_is_rejected
test_async_none_receipt_is_not_success
test_active_save_ack_enters_requested_practice_level
test_cancelled_practice_publishes_or_queues_tombstone
test_restored_checkpoint_advances_with_campaign
test_compact_history_respects_64k_limit
```

## 11.9 Filesystem与adapter

```text
test_recovery_root_windows_junction_rejected
test_recovery_child_windows_junction_rejected
test_recovery_budget_bounds_lock_wait
test_http_backend_close_drains_executor
test_remote_mode_requires_token_even_behind_untrusted_proxy
```

---

# 12. 稳定版门禁

稳定版至少满足：

1. progress component map协议闭环；
2. old merge不能越过new set；
3. final reject marker全程lock-first；
4. slot quarantine有CAS与durable tombstone；
5. 2048 historical slot classify-first；
6. export/upgrade成功输出必可同版本inspect+verify；
7. 完整v3 backup有v4迁移路径；
8. replace eligibility有deep semantic proof；
9. transaction业务结果与cleanup结果分离；
10. preparing/terminal cleanup失败不阻断启动；
11. output publication崩溃可恢复；
12. 0.8/0.9锁协议兼容；
13. Windows junction/reparse实机通过；
14. Sokoban initial/state/lifecycle闭环；
15. backend lifecycle统一；
16. main branch protected；
17. required checks启用；
18. core coverage达到目标；
19. static type gate通过；
20. hash-locked依赖安装通过；
21. LICENSE和素材权利完成；
22. wheel/sdist/三平台安装包smoke通过；
23. 默认不联网；
24. 默认不遥测。

---

# 13. 性能与稳定性门槛

- pygame主线程不执行SQLite、archive IO、目录扫描和网络等待；
- score/state enqueue p99 ≤2 ms；
- status getter p99 ≤0.2 ms；
- score lock文件有界；
- state marker scan有总时间预算；
- Archive 10k+ attempts round-trip；
- 100次游戏切换后thread/FD回到基线；
- 30–60分钟soak内pending收敛；
- recovery evidence多轮export/import不递归增长；
- 128 MiB边界附近出现结构化拒绝，不出现OOM traceback；
- 磁盘满、只读、坏DB、坏archive、坏journal：
  - 不删除最后有效副本；
  - 返回稳定错误码；
  - 有用户可见恢复入口。

---

# 14. 推荐实施顺序

## R0：六个阻断族

1. component map + Store method matrix；
2. reject final marker lock-first；
3. slot quarantine CAS/tombstone；
4. 2048 historical classify-first；
5. Archive self-certification/v3 upgrade/publisher；
6. transaction finalization/cleanup separation；
7. 对应multiprocess、fault、model tests。

## R1：协议收口

- preparing cleanup；
- SafeTreeWalker；
- recovery deadline；
- semantic winner status；
- dual lock compatibility；
- evidence parser；
- exact space preflight；
- replace preview；
- row schemas；
- Sokoban lifecycle；
- RNG compatibility；
- receipt compaction；
- HttpBackend close；
- typing/CI/license。

完成后冻结：

```text
score spool schema 2
state journal schema 3（除非slot tombstone必须升版）
SQLite schema 7（按migration需要升版）
Archive v4 reader contract
ImportTransaction v2/v3 reader
2048 slot v6 reader
Sokoban practice-return v1 reader
```

冻结后只允许：

- 数据正确性修复；
- 安全修复；
- 向后兼容；
- 明确的新版本迁移。

## R2：本机桌面体验

- 数据管理页；
- 档案页；
- input/remap/controller；
- audio；
- DPI/font/accessibility；
- settings；
- progress dashboard。

## R3：单机内容

- 五款游戏本地模式；
- 编辑器；
- replay；
- 本机成就；
- 离线日期挑战；
- 本地化。

## R4：发行

- LICENSE；
- branch protection；
- hash locks；
- typing/coverage；
- Windows/macOS/Linux安装包；
- 签名、校验和和自动release。

---

# 15. 最终判断

当前项目已经从“AI生成的小游戏集合”发展成了具有：

- 本地档案；
- SQLite；
- durable pending；
- crash recovery；
- archive/import；
- 多进程协调；
- ruleset版本；
- 自动存档；
- CI/release验证；

的完整桌面项目。

本轮应肯定：

> **第十七次审查的直接修复大多真实有效，尤其是2048 settled save、Archive v4、transaction cleanup namespace、state BUSY、lease和Sokoban validator。**

但剩余风险已经不再是简单漏catch，而是：

```text
component hash定义之间
outbox resolver与Store method matrix之间
读取快照与锁之间
load与quarantine之间
结构验证与replace资格之间
业务终态与目录清理之间
新旧程序锁协议之间
```

因此下一轮不要继续添加更多协议功能。先关闭这些组合不变量，再冻结数据层。

项目后续最有价值的完善方向是：

- 数据管理GUI；
- 档案管理；
- 输入、手柄、音频、DPI和无障碍；
- 纯逻辑engine与replay；
- 五款游戏的本地内容；
- 三平台桌面发行。

这比重构成联网竞技平台更符合项目定位、代码规模和用户需求。
