# Classic Games Hub 第十六次代码审查与本地优先优化任务书

> 审查日期：2026-08-25
> 当前基线：`main` 分支 commit `d8808d3ae59e7e3ddd0b0ac90b797c9ca450614e`（`d8808d3`）
> 对比基线：第十五次审查 commit `4ada144e165b324a24bd2a474b89aa0e28a43394`
> 增量提交：4 个；核心修复提交为 `8338ea3ce4ba77174a16cb815432c3acb4432cb7`
> 当前包版本：`0.8.0`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 明确非目标：账号、公网排行、匹配、赛季、实时联机、反作弊、云同步、强制联网和默认遥测

---

## 0. 执行摘要

本轮修复总体有效。第十五次审查的四项发布阻断已经有真实代码、协议文档和回归测试支持：

1. POSIX application handoff 增加独立 transition gate，并在 shared lease 下重新检查 transaction root；
2. ImportTransaction 使用 `.preparing-*` 和 `.import-*` 两种根目录，发布后的 transaction 缺 journal 时会停止启动；
3. State journal 的永久语义错误不再绕过 journal 直接写 SQLite；
4. Live outbox 与 Import planner 共用 `resolve_operations()`，相同 order、不同 hash 会拒绝整次导入。

本轮还完成了：

- Score/state orphan temp 扫描；
- Legacy pending no-follow；
- 数据库路径 canonicalization；
- fresh-DB `restore-replace`；
- Archive v3 的历史 catalog 和 preserve-only 规则；
- 2048 claim 的 owner/epoch/revision/value-hash 核对；
- Sokoban campaign/practice session 与 progress future 拆分；
- Snake、Zuma RNG 注入；
- CI 安装约束进一步固定；
- 包版本升为 `0.8.0`；
- 当前 head 的远端 CI 成功。

现有架构方向正确，无需重构为联网平台：

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
    ├── application transition gate
    ├── application lifetime lease
    ├── maintenance lock
    ├── Archive v3
    └── ImportTransaction

Optional Flask
    └── 仅作为本机成绩 API 调试适配器
```

### 当前发布阻断

本轮发现两个新的确定性发布阻断：

1. **Score spool 的发布协议与读取协议互相矛盾。**发布使用临时文件硬链接到 canonical target；读取器却把 `st_nlink > 1` 当作损坏。另一个进程可在 `link()` 与 temp 删除之间隔离 canonical target，使“已持久化”成绩只剩 quarantine evidence。
2. **`set_progress` 被错误纳入 component merge。**两个尚未提交的 `set_progress` 会被转成 components 为空的 `merge_progress`，随后被 parser/store 判为无效，导致第二次 replacement 写入确定性失败。

此外仍有一批高优先级边界：

- orphan temp recovery 会在另一个活跃 writer 锁超时后隔离其 temp；
- 有效 orphan temp 遇损坏 canonical 时会被隔离而不是修复 canonical；
- state journal 的损坏当前值在 quarantine 失败后仍可能被覆盖；
- state clock 使用无界、可跟随 symlink 的 `read_text()`；
- LocalStateEvent 缺少 operation identity，不能完整表达 `(revision, operation_id)` 顺序；
- terminal ImportTransaction root 删除失败会阻塞后续启动；
- raw replace rollback 没有把 SQLite sidecar 纳入可认证回滚；
- fresh replace staging 目录不是正式 recovery artifact；
- Export/preview 仍有少量副作用和 no-follow 缺口；
- Flask 与预构造 Store 的 canonical database identity 尚未完全统一；
- Archive 对历史 committed rows已有策略，但历史 active pending 仍缺 preserve-only 路径。

### 结论

> **第十五次的四项 P0 已修复到位；项目没有出现玩法层全面反弹。**
>
> **下一轮只应关闭本轮两个 P0和关键恢复硬化问题，随后正式冻结 score spool、state journal、Archive v3、ImportTransaction 和 2048 slot ownership。**

---

## 1. 审查范围、方法、证据与限制

### 1.1 已检查范围

- 当前 `main` 最新提交、提交历史和对比差异；
- `game_service/maintenance.py`
- `game_service/import_transaction.py`
- `game_service/data_cli.py`
- `game_service/local_backend.py`
- `game_service/store.py`
- `game_service/progress.py`
- `game_service/mutation.py`
- `game_service/save_slot_validation.py`
- `game_service/service.py`
- `game_service/catalog.py`
- `server/app.py`
- `client/common/ui.py`
- `client/launcher.py`
- Tetris、Snake、2048、Sokoban、Zuma；
- storage v2–v13 tests；
- gameplay regression、stress、release runner；
- CI workflow、README、CHANGELOG、协议文档、NOTICE 和 constraints；
- 当前 GitHub Actions 与 branch protection 状态。

### 1.2 重点状态模型

```text
score temp fsync
→ hard-link canonical
→ canonical nlink=2
→ 另一个scanner读取
→ scanner隔离canonical
→ writer删除temp

pending set_progress A
→ database不可写
→ pending set_progress B
→ resolve_operations(kind=progress)
→ component merge
→ empty components

orphan temp age>2s
→ 原writer仍持request/key lock
→ recovery lock timeout
→ broad except
→ temp进入quarantine

valid orphan temp
→ canonical存在但损坏
→ canonical parse失败
→ temp被当作invalid orphan隔离

fresh replace staging
→ fresh DB写完
→ ImportTransaction尚未prepare
→ 进程崩溃

raw rollback
→ original DB主文件+WAL
→ replace失败
→ 只恢复主文件
→ sidecars被删除/留在backup名
```

### 1.3 私有等价文件系统复现

对 score hard-link窗口进行了等价本地模型验证：

```text
temp 与 canonical link count均为2
→ scanner移动canonical到quarantine
→ writer删除temp
→ active canonical不存在
→ quarantine保留唯一bytes
```

这不是纯理论判断。

### 1.4 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题主路径和关键组合已闭环 |
| **基本到位** | 主体关闭，但仍有相邻边界 |
| **部分到位** | 已有接口或结构，但端到端不变量仍不成立 |
| **代码路径确定** | 可从当前控制流直接推出 |
| **状态模型复现** | 可用等价调度或文件系统状态复现 |
| **跨平台待验证** | 需要 Windows/macOS/Linux 实机验证 |
| **产品任务** | 非当前 Bug，但符合本地单机方向 |

### 1.5 限制

本次没有在当前执行容器中独立运行 pygame、Flask 和全部 storage/stress/gameplay 测试。

可确认的外部自动化证据：

- 当前 head 的 GitHub Actions CI 已完成并成功；
- 仓库记录 235 项 storage、107 项 gameplay，以及完整 stress/release profile；
- 这些数字属于仓库自测和远端 CI 证据，不表述为本次容器独立测量。

---

## 2. 第十五次审查修复验收矩阵

| 上轮 Finding | 当前状态 | 本轮结论 |
|---|---|---|
| POSIX handoff非原子 | **修复到位** | transition gate + shared lease后二次transaction检查 |
| 缺journal的import root被删除 | **修复到位** | `.preparing-*`与`.import-*`分型；published root缺journal即停止 |
| State语义错误绕过journal | **修复到位** | nonretryable StoreError不调用SQLite |
| Import silent-drop state pending | **修复到位** | live/planner共用resolver，相同identity不同hash拒绝 |
| Prepared reject target缺失 | **基本到位** | previous优先恢复；DB receipt证明仍可加强 |
| Reject失败无稳定状态 | **修复到位** | SUPERSEDED或RECOVERY_REQUIRED，停止本轮replay |
| Orphan temp无协议 | **部分到位** | 已扫描和提升；活跃writer与损坏canonical边界有新问题 |
| Legacy pending跟随symlink | **修复到位** | lstat、单链接、no-follow读取 |
| DB symlink identity | **LocalBackend到位** | Flask和预构造Store仍可保留lexical path |
| Score StoreError全当冲突 | **明显改善** | conflict/retry/corrupt分型；hard-link发布引入新竞态 |
| 构造失败依赖GC释放lease | **基本到位** | outbox初始化失败显式释放；更后续异常仍需统一清理 |
| Future reader拒绝v3 | **主体到位** | v3按自身format验证，不再比较未来全局常量 |
| Ruleset catalog全等 | **修复到位** | 允许历史子集和未知历史ID |
| 历史行使用当前validator | **按安全边界处理** | current严格；historical bounded preserve-only |
| Replace依赖健康目标DB | **修复到位** | 旁路构建fresh DB并原子替换 |
| v1恢复未绑定evidence | **修复到位** | CLI要求evidence与hash |
| 2048 claim未绑定revision/hash | **修复到位** | owner/epoch/revision/value_hash完整核对 |
| 2048单autosave | **产品策略明确** | 一个档案一个autosave、一个active owner |
| Sokoban放弃campaign | **主体到位** | 有session snapshot和返回；进程退出时仍不持久化棋盘 |
| Progress Future共用 | **修复到位** | campaign/practice分开 |
| Practice schema允许unlock | **修复到位** | practice字段集收紧 |
| Practice overlay显示ranked total | **修复到位** | 显示练习关成绩 |
| Main required checks | **未完成** | main仍未保护 |
| Per-platform hash lock | **未完成** | 精确constraints已有，hash lock缺失 |
| Coverage 60% | **未完成** | 测试数量提升，门槛仍为60% |
| Static typing | **未完成** | 无pyright/mypy门禁 |
| LICENSE | **未完成** | 需权利人决定 |
| 根目录重复taskbook | **修复到位** | 审查材料归档到docs/audits |
| 大模块拆分 | **合理延期** | 不与协议修复混做机械重构 |

---

# 3. 当前发布阻断问题

## 3.1 CG16-F01：Score hard-link发布与single-link读取策略冲突

- **优先级**：P0
- **证据**：代码路径确定 + 状态模型复现
- **位置**：
  - `PersistentSaveOutbox.add_mutation`
  - `_read_regular_nofollow`
  - `list_envelopes`
  - `snapshot_envelopes`

### 当前发布协议

```text
写入并fsync temp
→ 取得request lock
→ os.link(temp, canonical)
→ fsync目录
→ 释放request lock
→ finally删除temp
```

在：

```text
os.link()
```

和：

```text
temp.unlink()
```

之间，canonical与temp指向同一inode，`st_nlink == 2`。

### 当前读取协议

所有正式读取都要求：

```text
普通文件
st_nlink <= 1
```

`list_envelopes()`读取失败后会把canonical移入quarantine，而且这个隔离动作没有取得request lock。

### 可复现顺序

```text
Writer A完成link，canonical nlink=2
→ A释放request lock
→ Scanner B读取canonical
→ B将其判为corrupt/unsafe并移入quarantine
→ A删除temp
→ active pending不存在
→ 只有quarantine evidence
```

若SQLite写入随后失败：

- Backend可能已经把spool视为durable；
- 实际active journal不存在；
- 自动重放链断裂；
- 用户数据只剩隔离证据。

### 修复要求

推荐直接取消score canonical的hard-link发布：

```text
request lock
→ 检查canonical
→ temp fsync
→ os.replace(temp, canonical)或平台原子no-replace
→ fsync目录
→ 释放lock
```

同时：

- `list_envelopes`
- `remove`
- `quarantine_request`
- `increment_attempt`
- `set_attempt_count_max`
- orphan recovery

对同一request canonical进行修改时必须取得同一request lock。

---

## 3.2 CG16-F02：`set_progress`错误使用component merge

- **优先级**：P0
- **证据**：代码路径确定
- **涉及**：
  - `PersistentStateOutbox._kind`
  - `resolve_operations`
  - `_merge_progress_operations`
  - `set_progress_async`
  - `LocalGameStore.set_progress`

### 当前语义冲突

`_kind()`把：

```text
set_progress
merge_progress
```

都映射为：

```text
kind = progress
```

`resolve_operations()`只要看到：

```text
incoming.kind == progress
```

就执行 component merge，而没有检查 method。

但 `set_progress`：

- 是replacement/LWW语义；
- 没有components；
- 不应与旧值做monotonic merge。

### 确定性场景

```text
set_progress A进入journal，数据库暂时不可写
→ set_progress B到达同一个key
→ resolve_operations调用_merge_progress_operations
→ components_by_id为空
→ 生成method=merge_progress、components=[]
→ journal被写入
→ parser/store拒绝“progress components are missing”
→ B永久失败
```

混合场景：

```text
set_progress + merge_progress
merge_progress + set_progress
```

同样没有明确定义。

### 修复选择

推荐把运行时持久协议收窄为：

```text
进度写入只使用merge_progress
set_progress仅作为migration/test内部primitive
```

若保留公开 `set_progress_async`，必须定义完整method矩阵：

| existing | incoming | 语义 |
|---|---|---|
| set | set | LWW/order |
| merge | merge | component merge |
| set | merge | 显式从set基线合入 |
| merge | set | replacement是否覆盖components必须明确 |
| 任意同order不同hash | conflict |

不能继续只按`kind`决定。

---

# 4. 高优先级恢复与一致性问题

## 4.1 CG16-F03：Orphan recovery会隔离活跃writer的temp

Score/state orphan scanner以：

```text
mtime超过2秒
```

认定temp可能遗留。

随后尝试取得request/key lock。若另一个活跃writer因调度暂停或慢盘持锁超过超时，lock timeout被宽泛异常处理捕获，scanner会把temp移入quarantine。

结果：

- 活跃writer恢复后找不到temp；
- journal发布失败；
- 若SQLite也失败，只能依靠进程内memory fallback；
- 进程再崩溃时，数据不在自动恢复链中。

### 修复

- lock timeout表示“可能仍有writer”，必须原样保留temp；
- 只有成功取得lock后才可判断orphan；
- 采用两次稳定扫描、writer marker或打开文件元数据，而不是仅用wall-clock grace。

---

## 4.2 CG16-F04：有效orphan temp遇损坏canonical时会被隔离

当前恢复流程：

```text
temp可解析
→ target存在
→ 解析target
→ target损坏抛异常
→ broad except把temp隔离
→ 损坏target仍留在active path
```

这与修复目标相反。

### 正确顺序

```text
验证temp
→ 在request/key lock内验证canonical
→ canonical损坏时先成功保存/隔离canonical
→ 再提升temp
```

若canonical证据无法保存，temp和canonical都必须原样保留并报告manual recovery。

---

## 4.3 CG16-F05：State current损坏且quarantine失败时仍会被覆盖

`PersistentStateOutbox.put()`解析current失败后调用：

```text
_quarantine_locked(target, "invalid-current")
```

但没有检查返回值，随后仍把：

```text
existed = False
```

并允许incoming原子替换target。

若quarantine目录不可写或满盘：

- current损坏证据未保留；
- incoming覆盖current；
- 唯一原始字节消失。

### 修复

只有在：

```text
current成功隔离
```

或：

```text
用户显式确认放弃
```

后，才允许替换不可解析的current。

---

## 4.4 CG16-F06：State clock读取无界且可跟随symlink

`next_revision()`读取 `.state-clock` 使用普通：

```python
read_text()
```

没有：

- `lstat`
- no-follow
- regular-file验证
- 字节上限
- inode前后复核。

损坏或symlink clock可以：

- 读取数据目录外文件；
- 对超大文件分配不必要内存；
- 把控制路径误判为坏clock后移动/重建。

应与score/state journal共享一个严格的小文件读取器。

---

## 4.5 CG16-F07：State timestamp上限过宽，可能污染ordering

State operation允许：

```text
updated_at <= 1e20
```

这是远超合理Unix时间的值。

未来时间operation一旦进入业务行或baseline：

- 可能长期压制正常新值；
- receipt rebuild按业务时间重建时形成异常高baseline；
- Archive跨设备恢复会放大时钟错误。

成绩路径已经会调整明显未来的 occurred_at，state应采用相近策略：

```text
允许合理历史时间
明显未来时间标记clock_adjusted
logical revision仍由本机clock生成
```

---

## 4.6 CG16-F08：LocalStateEvent无法表达完整winner order

Store和journal的顺序是：

```text
(logical_revision, operation_id)
```

但 `LocalStateEvent`只有：

```text
logical_revision
```

状态缓存更新规则又是：

```text
new_revision >= current_revision
```

相同revision、不同operation ID时：

- loser事件可能覆盖winner状态；
- UI无法验证event对应哪个payload；
- Import/replay边界难以诊断。

建议增加：

```text
operation_id
payload_hash
```

并用完整order更新状态缓存。

---

## 4.7 CG16-F09：Terminal ImportTransaction root删除失败会阻塞启动

Transaction完成后使用递归删除；若因权限、杀毒软件或短暂占用失败：

```text
phase = COMPLETED/ROLLED_BACK
root仍存在
```

启动后的：

```text
has_import_transaction_roots()
```

只看目录存在，不看phase，可能持续重试并最终报告maintenance busy。

### 修复

- 已验证terminal root不应阻止应用打开；
- cleanup失败单独进入recovery notice；
- 后台/CLI重试删除；
- active transaction判断应基于phase，而不是只基于glob。

---

## 4.8 CG16-F10：`.import-*`普通文件或Windows junction会造成恢复循环

Published transaction扫描：

- symlink会拒绝；
- regular file可能被略过；
- post-handoff root existence又会把它视为active；
- Windows reparse/junction不一定被`is_symlink()`完整识别。

应为transaction root使用与journal root相同的安全目录验证：

```text
真实目录
非symlink/reparse
owner/mode合理
journal存在
```

任何其他类型直接进入明确的recovery-required记录，不反复超时。

---

## 4.9 CG16-F11：Raw replace rollback未认证恢复SQLite sidecar

严重损坏目标DB时，replace可使用raw主文件rollback。

但原状态可能包括：

```text
games.db
games.db-wal
games.db-shm
games.db-journal
```

当前transaction主要认证主文件；sidecar被移动到额外backup名，而自动rollback只恢复主文件并清理WAL/SHM。

如果有效最新页只在WAL中：

- “rollback成功”不等于原字节/原逻辑状态恢复；
- sidecar需人工重新组合。

### 修复

二选一：

1. Transaction v3把sidecar作为认证的database image一部分；
2. Raw模式失败后只报告manual recovery-required，不宣称自动rollback完全成功。

---

## 4.10 CG16-F12：Fresh replace staging不是正式recovery artifact

`restore-replace`会先在：

```text
.<db>.fresh-replace-*
```

目录构建完整新数据库。

若在ImportTransaction prepare前崩溃：

- staging包含完整个人数据；
- 不属于transaction root；
- status/export/cleanup不统一盘点；
- privacy replace后可能长期残留。

应：

- 在正式transaction root内部materialize；
- 或把fresh staging纳入reserved/recovery inventory和安全清理。

---

## 4.11 CG16-F13：Import/replace会重复保存多个数据库副本

Merge import：

```text
store backup
+ ImportTransaction rollback DB
```

Replace：

```text
fresh DB
+ transaction rollback image
+ durable backup
```

可能需要当前数据库大小的2–3倍以上空间。

目前transaction字节预算主要限制staged/before files，不包含完整rollback DB。

### 改进

- preflight可用空间；
- 复用经过hash认证的rollback image；
- 报告预计字节；
- 满盘前拒绝，而不是中途进入复杂rollback。

---

## 4.12 CG16-F14：Export保留路径策略未覆盖不存在的reserved prefix

输出路径保护能识别现有recovery目录和 `.import-*`，但未来/准备中的命名空间需要按词法规则保留，而不是“存在时才保护”。

例如输出到：

```text
.games.db.preparing-user-backup
```

可能成功；下次启动会把它识别为不安全preparing root并阻止恢复。

统一保留：

```text
.<db>.preparing-*
.<db>.import-*
.<db>.fresh-replace-*
.<db>.replace-plan-*
application/maintenance/transition locks
```

---

# 5. Archive、Import和长期兼容问题

## 5.1 CG16-F15：Export仍会执行repair/maintenance副作用

Export在exclusive gate内构造：

```text
PersistentSaveOutbox(maintain=True)
PersistentStateOutbox(recover=True)
```

因此导出前可能：

- 迁移legacy pending；
- 提升orphan temp；
- 完成reject transaction；
- quarantine损坏文件。

这不一定错误，但“export”不再是纯只读操作。

### 建议

拆成明确阶段：

```text
repair-before-export
snapshot-only
```

结果中列出所有导出前修改。

---

## 5.2 CG16-F16：Preview import会自动回滚unfinished transaction

CLI帮助文案称preview不会修改数据，但其入口会先调用：

```text
recover_import_transactions()
```

这可能恢复数据库和pending文件。

应：

- 文案明确“preview会先恢复上次中断事务”；
- 或提供真正只读 `inspect-archive`；
- GUI把“恢复旧事务”和“预览新archive”分成两步。

---

## 5.3 CG16-F17：Pending score预检查仍使用普通`read_text`

Import planner前置检查target pending score时仍有一条：

```text
target.is_file()
target.read_text()
```

后续正式规划虽使用bounded no-follow reader，但前置路径仍可能：

- 跟随symlink；
- 读取超大文件；
- 对特殊文件产生阻塞。

应删除重复不安全检查，统一调用正式bounded reader。

---

## 5.4 CG16-F18：Archive loader不是descriptor-level bounded read

Archive加载目前：

```text
stat size
→ read_bytes
```

文件可在两步之间：

- 被替换；
- 增长；
- 变为symlink/special file。

应使用：

```text
lstat
O_NOFOLLOW
fstat
bounded read
post-read fstat
```

与transaction/recovery reader一致。

---

## 5.5 CG16-F19：历史active pending没有preserve-only策略

历史committed attempts/progress/slots已经支持：

```text
current ruleset严格验证
historical/unknown ruleset有界保留
```

但active pending仍通过当前：

- `normalize_score_mutation`
- `PersistentStateOutbox._parse`
- 当前progress/slot validator。

当未来移除游戏或修改pending schema时，旧archive可能因一条历史pending整体无法导入。

### 策略

- 已知历史adapter：恢复为active；
- 无adapter：恢复为evidence-only，不执行；
- 不把未知pending伪装成当前协议。

---

## 5.6 CG16-F20：Archive ruleset catalog每个游戏只能记录一个版本

同一archive的attempts可能同时包含：

```text
tetris-assist-2
tetris-assist-3
```

但manifest目前是：

```text
game_id → 单个ruleset字符串
```

当前validator主要把它作为game声明，功能尚可，但语义名称不准确。

Archive v3应冻结，不再改字段；未来v4应记录：

```text
game_id → observed ruleset set/list
```

---

## 5.7 CG16-F21：预构造Store和Flask仍可保留lexical数据库路径

LocalBackend默认路径已经canonicalize；但：

- 调用者传入一个已构造的`LocalGameStore`时，Store对象仍保留原path；
- Flask `init_db/create_app`取得resolved lease后，又用原配置路径构造Store。

数据库文件symlink或目录alias时，仍可能出现：

```text
lease identity
≠ store path identity
≠ pending identity
```

应由一个统一bootstrap返回：

```text
canonical_database_path
application_session
store
outbox roots
```

---

## 5.8 CG16-F22：构造函数后半段异常仍缺统一资源回滚

Outbox初始化异常已经显式关闭lease，但后续仍会创建：

- write worker
- read worker
- maintenance/retry tasks

其中任一步异常时，资源清理依赖部分对象销毁。

建议使用：

```text
ExitStack / explicit initialization transaction
```

保证lease、workers、store按逆序关闭。

---

# 6. 2048专项判断

### 已确认修复

- claim必须核对owner token；
- claim必须核对owner epoch；
- claim必须核对slot revision；
- claim必须核对value hash；
- superseded不会进入ready；
- pending/recovery/failure状态有明确门禁；
- close时发布更高revision release intent；
- terminal score补交；
- takeover CAS。

## 6.1 CG16-F23：`publish_slot_intent`未返回winner resolution

同步发布final release intent后，API总是主要返回：

```text
ok=true
durable_pending=true
```

没有显式返回：

```text
published
duplicate
superseded
winning_operation_id
winning_revision
```

关闭流程无法确认release是否真的成为journal winner。

建议复用state resolver结果，并把event状态设为：

```text
DURABLE_PENDING / SUPERSEDED
```

而不是一律pending成功。

## 6.2 CG16-F24：2048仍未使用可注入RNG

Tetris、Snake、Zuma已支持RNG注入，2048仍依赖module-level random。

这不是玩法Bug，但会阻碍：

- seed+输入完整重放；
- autosave恢复确定性测试；
- 本地replay；
- model-based测试。

## 6.3 产品策略

保持当前：

```text
每档案一个2048 autosave
同一时刻一个authoritative owner
```

是合理的本地产品选择。

多存档槽可作为后续可选功能，不需要联网。

---

# 7. Sokoban专项判断

### 已确认修复

- campaign/practice ledger分开；
- practice不提高campaign unlock；
- practice progress schema收紧；
- campaign/practice load generation分开；
- campaign/practice write Future分开；
- 进入练习可保存并返回campaign棋盘；
- overlay显示练习成绩；
- selector只在playing状态生效。

## 7.1 CG16-F25：同一progress key的快速连续写仍只有一个Future槽

Campaign和practice已分为两个Future槽，但每个key内部仍可能：

```text
上一写入未完成
→ 又通关/改善同一关
→ 新Future覆盖旧Future引用
```

底层merge journal会保留业务值，但UI可能遗漏旧请求的失败/恢复结果。

建议每个key采用：

```text
single in-flight
+ dirty/coalesced progress buffer
```

## 7.2 CG16-F26：Practice session只保存在内存

进入练习时campaign棋盘快照仅存在当前进程。

如果练习期间：

- 程序崩溃；
- 操作系统结束进程；
- 用户直接退出应用；

campaign ledger仍在，但具体棋盘/history无法继续。

这属于产品决策：

- 保持“只保留累计campaign进度”；或
- 增加一个本地campaign slot。

不需要联网。

---

# 8. 其他游戏专项判断

## 8.1 Tetris

当前主体：

- 7-bag；
- hold；
- ghost；
- DAS/ARR；
- soft drop；
- 大dt generation guard；
- top-out；
- RNG注入；
- ruleset隔离。

后续适合：

- lock delay；
- 可选strict rotation；
- hold/next图形；
- seed replay；
- 游戏内规则页；
- 静态网格缓存。

## 8.2 Snake

当前主体：

- 双转向queue；
- 180°门禁；
- 长停顿保护；
- 食物后速度更新；
- 尾格碰撞；
- RNG注入。

后续：

- 速度模式；
- 穿墙；
- 障碍；
- 双人同屏；
- 色弱纹理；
- 模式独立最佳。

## 8.3 Zuma

当前主体：

- swept collision；
- 多pending reaction；
- path bisect；
- 长帧余量；
- 临界救场；
- RNG注入；
- 多关进度。

后续：

- reaction FSM；
- `incoming`改deque；
- 属性测试；
- 训练/选关；
- 色弱符号；
- 原创道具；
- 轨道编辑器。

---

# 9. 工程、仓库和发行问题

## 9.1 Main未启用required checks

当前CI成功不能代替仓库保护。

至少要求：

```text
release-gate
core-only
Ubuntu
macOS
Windows
```

## 9.2 依赖构建仍未完全可复现

当前已固定pip并在CI使用constraints，这是进步。

仍缺：

- per-platform lock；
- `--require-hashes`；
- build-system精确版本；
- wheel availability长期验证；
- installed manifest与SBOM强一致门禁。

## 9.3 Python支持范围大于测试范围

项目声明：

```text
requires-python >=3.11
```

但自动化只验证3.11–3.13。

应：

- 明确上界；或
- 每个新Python版本发布后加入测试再宣称支持。

## 9.4 Coverage仍为60%

建议分阶段：

```text
storage/archive/import ≥90%
全项目 ≥80%
```

但不能用coverage替代：

- multiprocess barrier；
- crash injection；
- filesystem race；
- migration fixtures；
- gameplay state model。

## 9.5 无静态类型门禁

优先标注：

- GameDataService；
- SaveEvent/LocalStateEvent；
- StoreError result；
- Archive manifest；
- ImportTransaction；
- Future controllers。

## 9.6 Lock file hard-link未明确拒绝

Control file已经检查：

- regular file；
- owner；
- group/world write；
- symlink/reparse。

但POSIX没有明确拒绝：

```text
st_nlink > 1
```

同用户hard-link可让锁初始化的truncate/write影响另一文件。

## 9.7 Optional Flask可被显式暴露到非loopback

默认是 `127.0.0.1`，符合调试用途。

但环境变量可改为公网地址，接口没有认证。建议：

- 非loopback需显式 `--unsafe-expose`；
- 启动时高亮警告；
- 文档继续强调它不是联网平台。

## 9.8 LICENSE仍缺失

正式分发前由权利人完成：

- 代码权利确认；
- AI辅助代码政策；
- 项目名称/商标；
- 未来字体、图片、音频；
- LICENSE选择。

---

# 10. 符合本地单机定位的完善方向

## 10.1 数据与档案

- 本机数据管理页；
- 档案列表、新建、切换、重命名；
- export-before-delete；
- 家庭成员分档；
- transaction/quarantine/backup浏览；
- merge import与replace restore预览；
- legacy history浏览；
- 按游戏清理本机数据。

## 10.2 桌面体验

- 全键盘菜单导航；
- 手柄；
- 键位重映射；
- BGM/SFX；
- 分组音量；
- 可调窗口；
- 高DPI；
- CJK字体fallback；
- 色弱图案；
- 高对比度；
- 降低动态效果；
- 本机日志和崩溃恢复页。

## 10.3 确定性和可维护性

- Clock/RNG统一注入；
- 纯规则engine；
- seed+输入本地replay；
- pygame只负责输入和渲染；
- 模型测试；
- 可重复benchmark。

## 10.4 离线内容

- 本机成就；
- 日期seed挑战；
- 本地复盘；
- 中英文；
- 三平台桌面包；
- 签名和校验和。

---

# 11. 明确不建议建设

- 注册登录；
- 云端账号；
- 公网排行榜；
- 匹配；
- 赛季；
- 实时联机；
- 服务端权威判定；
- 反作弊；
- 在线商城；
- 云同步；
- 默认遥测；
- 强制联网。

也不建议：

- 再增加新的journal/receipt类型；
- 为形式统一强制改写unittest、pygame subprocess regression和stress；
- 在当前协议封板前新增多设备数据合并；
- 因极端存储边界重写五款游戏核心玩法。

---

# 12. 完整优化任务清单

## 优先级

- **P0**：可使active pending离开自动恢复链，或使公开状态API确定性失败。
- **P1**：恢复、兼容、数据安全和正式发行基础。
- **P2**：维护性、桌面体验、性能和可访问性。
- **P3**：玩法内容和三平台发行。
- **S/M/L/XL**：相对工作量。

---

## 12.1 P0：Score发布与progress语义

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG16-P0-01 | 移除score canonical hard-link窗口 | score publisher | canonical发布期间永远nlink=1 | M |
| CG16-P0-02 | 所有score canonical操作统一request lock | spool API | read/remove/quarantine/update互斥 | L |
| CG16-P0-03 | link→unlink barrier回归 | multiprocess test | scanner不能隔离新canonical | M |
| CG16-P0-04 | writer/scanner/writer三方矩阵 | stress fixture | 不丢active pending、不误报durable | L |
| CG16-P0-05 | durable结果二次确认canonical存在 | backend | DB失败时durable_pending必须真实 | S |
| CG16-P0-06 | 区分set与merge progress | resolver matrix | 不再按kind直接merge | M |
| CG16-P0-07 | set/set LWW测试 | unit/model test | pending replacement正确 | S |
| CG16-P0-08 | set/merge与merge/set策略 | ADR+实现 | 语义明确、无empty components | M |
| CG16-P0-09 | 收窄公开set_progress API | service boundary | runtime优先只用merge | M |
| CG16-P0-10 | Legacy set_progress兼容 | migration fixture | 旧journal可安全迁移/隔离 | M |
| CG16-P0-11 | P0 fault matrix | release suite | 所有调度点确定性覆盖 | L |
| CG16-P0-12 | P0 release gate | CI | 任一失败禁止release | S |

---

## 12.2 P1：恢复、兼容和发行基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG16-P1-01 | Orphan lock timeout保留temp | recovery policy | 活跃writer文件不被隔离 | S |
| CG16-P1-02 | Orphan稳定两次扫描 | scanner | 不只依赖mtime | M |
| CG16-P1-03 | Score corrupt canonical优先隔离 | recovery | valid temp可提升 | M |
| CG16-P1-04 | State corrupt canonical优先隔离 | recovery | valid temp可提升/merge | M |
| CG16-P1-05 | Quarantine失败禁止覆盖current | outbox invariant | 原始字节不丢 | S |
| CG16-P1-06 | State clock no-follow bounded reader | clock journal | symlink/大文件安全失败 | M |
| CG16-P1-07 | State future-time policy | clock adjustment | 极端未来值不压制正常写入 | M |
| CG16-P1-08 | LocalStateEvent加入operation_id/hash | contract v2 | UI可识别winner | M |
| CG16-P1-09 | Event缓存使用完整order | backend status | equal revision不误覆盖 | S |
| CG16-P1-10 | Terminal transaction root不阻塞启动 | root classifier | cleanup失败只提示 | M |
| CG16-P1-11 | Transaction root安全目录检查 | recovery | regular/reparse/junction明确拒绝 | M |
| CG16-P1-12 | Raw rollback纳入WAL/SHM | transaction v4或policy | 不虚报完整rollback | L |
| CG16-P1-13 | Raw模式manual-recovery fallback | recovery UI | sidecar不全时停止自动恢复 | M |
| CG16-P1-14 | Fresh staging纳入transaction root | restore engine | 崩溃后可盘点 | L |
| CG16-P1-15 | Replace staging cleanup/inventory | status/export | 不残留隐私副本 | M |
| CG16-P1-16 | 数据操作磁盘空间preflight | maintenance | 预计空间不足时不开始 | M |
| CG16-P1-17 | 复用authenticated rollback image | import/replace | 减少重复DB副本 | L |
| CG16-P1-18 | Reserved prefix统一策略 | path policy | preparing/fresh/plan永不可覆盖 | M |
| CG16-P1-19 | Export snapshot-only模式 | data CLI | 不隐式repair | L |
| CG16-P1-20 | Export repair报告 | result schema | 所有导出前修改可见 | S |
| CG16-P1-21 | 真正只读archive inspect | CLI | 不回滚目标transaction | M |
| CG16-P1-22 | Preview文案与副作用一致 | docs/UI | 不宣称完全只读 | S |
| CG16-P1-23 | Pending score统一bounded reader | planner | 无read_text旁路 | S |
| CG16-P1-24 | Archive descriptor-level reader | parser | nofollow、bounded、post-fstat | M |
| CG16-P1-25 | Historical score pending策略 | archive adapter | active或evidence-only明确 | L |
| CG16-P1-26 | Historical state pending策略 | archive adapter | 不用当前schema误解释 | L |
| CG16-P1-27 | Removed-game pending evidence | archive/import | 不阻断其他游戏恢复 | M |
| CG16-P1-28 | Archive v3规则目录语义文档 | ADR | 单值限制明确 | S |
| CG16-P1-29 | Archive v4 observed ruleset set设计 | ADR only | 不修改已发布v3 | S |
| CG16-P1-30 | Canonical database bootstrap | shared factory | lease/store/outbox同一identity | L |
| CG16-P1-31 | Flask使用canonical DB path | optional adapter | symlink alias不分裂 | S |
| CG16-P1-32 | 预构造Store path一致性检查 | LocalBackend API | mismatch直接拒绝 | M |
| CG16-P1-33 | Constructor ExitStack清理 | lifecycle | 任一阶段失败无锁/线程泄漏 | M |
| CG16-P1-34 | `publish_slot_intent`返回resolution | state API | release是否winner可见 | M |
| CG16-P1-35 | 2048 release状态事件 | controller | superseded不误报pending | S |
| CG16-P1-36 | Sokoban per-key coalescing | progress controller | 同key单in-flight | M |
| CG16-P1-37 | Sokoban crash期间campaign策略 | ADR | 是否保存棋盘明确 | S |
| CG16-P1-38 | Lock file hard-link检查 | control file | nlink>1安全拒绝 | S |
| CG16-P1-39 | Flask非loopback显式确认 | CLI/config | 默认不意外暴露 | S |
| CG16-P1-40 | Main required checks | repo settings | main不可绕过CI | S |
| CG16-P1-41 | Per-platform dependency lock | lock files | 三平台可复现 | L |
| CG16-P1-42 | Hash-locked安装 | release | 支持require-hashes | M |
| CG16-P1-43 | Pin build-system | pyproject/constraints | setuptools/build可审计 | S |
| CG16-P1-44 | Python支持矩阵策略 | packaging | 声明范围等于测试范围 | S |
| CG16-P1-45 | Storage/archive coverage提升 | CI | 核心逐步达到90% | M |
| CG16-P1-46 | Static type checking | pyright/mypy | service/archive边界通过 | L |
| CG16-P1-47 | Model-based journal tests | test suite | merge/LWW/conflict不变量 | L |
| CG16-P1-48 | Filesystem race registry | fault injection | link/replace/fsync/lock统一注入 | L |
| CG16-P1-49 | 完整恢复演练 | release drill | export→replace→replay闭环 | L |
| CG16-P1-50 | Structured recovery logs | logging | operation/transaction ID可追踪 | M |
| CG16-P1-51 | LICENSE权利核对 | owner checklist | 可安全选择许可证 | M |
| CG16-P1-52 | 加入LICENSE | owner decision | 正式分发前完成 | S |
| CG16-P1-53 | 存储协议封板ADR | architecture | P0/P1关键项后冻结 | S |

---

## 12.3 P2：维护性、桌面体验和可访问性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG16-P2-01 | 拆分local_backend.py | spool/state/workers/status | 行为不变 | XL |
| CG16-P2-02 | 拆分store.py | repositories/migrations | 行为不变 | XL |
| CG16-P2-03 | 拆分data_cli.py | archive/planner/executor | 可独测 | L |
| CG16-P2-04 | DataManagementService | service API | GUI不直接访问SQLite | L |
| CG16-P2-05 | 数据管理页 | desktop UI | backup/import/recovery/cleanup | XL |
| CG16-P2-06 | Transaction恢复页 | UI | list/export/recover | L |
| CG16-P2-07 | 档案列表页 | profile UI | 新建/切换/重命名/进度 | L |
| CG16-P2-08 | Export-before-delete | profile flow | 删除可恢复 | L |
| CG16-P2-09 | Profile merge工具 | local UI | 冲突预览 | XL |
| CG16-P2-10 | GameState Enum | state model | 减少magic string | L |
| CG16-P2-11 | AttemptSaveController | common layer | 五款保存控制统一 | XL |
| CG16-P2-12 | LocalStateController | common layer | progress/slot/settings统一 | L |
| CG16-P2-13 | InputManager | action map | 五款输入统一 | L |
| CG16-P2-14 | 完整IME控件 | widget | 光标/选择/组合输入 | M |
| CG16-P2-15 | 键位重映射 | settings UI | 冲突检测 | L |
| CG16-P2-16 | 全键盘菜单 | focus model | 无鼠标完整操作 | L |
| CG16-P2-17 | 手柄支持 | controller | launcher/五款可用 | L |
| CG16-P2-18 | 音频系统 | BGM/SFX | 无设备安全降级 | L |
| CG16-P2-19 | 设置页 | UI | 音量/窗口/按键/辅助项 | L |
| CG16-P2-20 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG16-P2-21 | 高DPI | DPI handling | 图文清晰 | M |
| CG16-P2-22 | CJK字体fallback | licensed chain | 缺系统字体仍可读 | M |
| CG16-P2-23 | 色弱图案 | shapes/patterns | 颜色非唯一信息 | L |
| CG16-P2-24 | 高对比/降低动态 | accessibility | 动画强度可调 | L |
| CG16-P2-25 | Clock/RNG统一注入 | deterministic services | 五款均可seed | L |
| CG16-P2-26 | 纯规则Engine抽取 | rules modules | 核心测试无需SDL | XL |
| CG16-P2-27 | Launcher拆分 | app/state/render | main职责清晰 | L |
| CG16-P2-28 | 首页改为进度中心 | dashboard | best/recent/progress/continue | L |
| CG16-P2-29 | 静态Surface缓存 | profiler-driven | 有失效规则 | L |
| CG16-P2-30 | Benchmark CLI | benchmark | OS/版本/seed完整 | M |
| CG16-P2-31 | 30–60分钟soak | stability suite | 线程/FD/内存稳定 | M |
| CG16-P2-32 | Recovery inventory统一 | data model | active/temp/evidence清楚分类 | L |

---

## 12.4 P3：玩法内容和三平台发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG16-P3-01 | Tetris lock delay | optional ruleset | 测试完整 | M |
| CG16-P3-02 | Tetris strict rotation | optional mode | 与assist分榜 | M |
| CG16-P3-03 | Tetris规则页/hold图形 | UI | 规则可查看 | S |
| CG16-P3-04 | Snake速度/穿墙/障碍 | local modes | 最佳分开 | L |
| CG16-P3-05 | Snake双人同屏 | local multiplayer | 无网络 | L |
| CG16-P3-06 | 2048撤销 | undo model | 与attempt/slot一致 | L |
| CG16-P3-07 | 2048多存档槽 | save UI | 查看/继续/删除 | L |
| CG16-P3-08 | 2048棋盘尺寸 | modes | ruleset分离 | M |
| CG16-P3-09 | Sokoban正式选关UI | progress UI | campaign/practice清楚 | L |
| CG16-P3-10 | Sokoban星级/最佳推动 | metrics | 规则明确 | M |
| CG16-P3-11 | Sokoban死锁检测/提示 | analysis | 可关闭 | XL |
| CG16-P3-12 | Sokoban编辑器 | XSB import/export | 地图验证 | L |
| CG16-P3-13 | Zuma reaction FSM | game model | 连锁可属性测试 | L |
| CG16-P3-14 | Zuma训练/选关 | practice | 不混入通关 | L |
| CG16-P3-15 | Zuma色弱辅助 | symbols | 球色可辨认 | M |
| CG16-P3-16 | Zuma原创道具/轨道 | content | 确定性测试 | XL |
| CG16-P3-17 | Zuma轨道编辑器 | path tool | 版本化/预览 | L |
| CG16-P3-18 | 本机成就 | local achievements | 无账号/遥测 | L |
| CG16-P3-19 | 离线每日挑战 | date seed | 完全离线 | L |
| CG16-P3-20 | 本地replay | command log | 复盘/调试 | L |
| CG16-P3-21 | 中英文 | localization | 长文本布局测试 | L |
| CG16-P3-22 | Windows桌面包 | installer/portable | 无Python运行 | XL |
| CG16-P3-23 | macOS/Linux桌面包 | app bundle/AppImage | 数据目录正确 | XL |
| CG16-P3-24 | 自动发布/签名/校验和 | release pipeline | smoke通过才发布 | L |

---

# 13. 必须新增的关键测试

## 13.1 Score hard-link/race

```text
test_score_canonical_is_never_visible_with_two_links
test_scanner_cannot_quarantine_a_writer_publication
test_second_writer_cannot_misread_link_in_progress
test_db_failure_after_spool_publication_has_real_active_journal
test_score_remove_and_retry_count_share_request_lock
```

## 13.2 Progress method matrix

```text
test_two_pending_set_progress_operations_use_lww
test_set_then_merge_progress_has_defined_result
test_merge_then_set_progress_has_defined_result
test_set_progress_never_generates_empty_components
test_public_progress_contract_matches_store_and_outbox
```

## 13.3 Orphan recovery

```text
test_lock_timeout_keeps_score_temp_in_place
test_lock_timeout_keeps_state_temp_in_place
test_valid_score_temp_replaces_quarantined_corrupt_target
test_valid_state_temp_replaces_quarantined_corrupt_target
test_quarantine_failure_never_overwrites_corrupt_current
```

## 13.4 State clock/event

```text
test_state_clock_symlink_is_rejected
test_state_clock_size_is_bounded
test_future_state_timestamp_is_adjusted_or_rejected
test_equal_revision_events_are_ordered_by_operation_id
test_event_payload_hash_matches_authoritative_winner
```

## 13.5 Transaction and replace

```text
test_completed_transaction_root_cleanup_failure_does_not_block_startup
test_regular_file_import_root_is_recovery_required
test_windows_junction_import_root_is_rejected
test_raw_rollback_preserves_or_reports_sidecars
test_fresh_replace_staging_is_inventory_visible
test_replace_preflights_required_disk_space
```

## 13.6 Archive/import

```text
test_export_snapshot_only_has_no_repair_side_effect
test_preview_help_matches_transaction_recovery_behavior
test_pending_score_target_is_read_nofollow_and_bounded
test_archive_loader_rejects_symlink_and_growth_race
test_removed_game_pending_restores_as_evidence_only
test_historical_state_pending_requires_adapter_or_evidence
```

## 13.7 Canonical path/lifecycle

```text
test_flask_symlink_database_uses_one_canonical_identity
test_prebuilt_store_path_mismatch_is_rejected
test_worker_creation_failure_releases_application_lease
test_control_file_hardlink_is_rejected
```

## 13.8 Sokoban/2048

```text
test_same_progress_key_coalesces_while_future_inflight
test_practice_exit_policy_preserves_campaign_ledger
test_publish_slot_intent_reports_superseded_resolution
test_2048_seed_and_input_reproduce_board
```

---

# 14. 性能与稳定性门槛

1. pygame主线程不得执行：
   - SQLite；
   - journal扫描；
   - archive I/O；
   - lock等待；
   - transaction恢复；
2. score/state enqueue p99 ≤2 ms；
3. status getter p99 ≤0.2 ms；
4. Score canonical在任何可观察时点都符合正式reader约束；
5. 语义冲突永远不绕过journal；
6. set/merge progress每个组合都有确定性语义；
7. orphan recovery不得抢占活跃writer；
8. 完整archive不得遗漏temp/transaction artifact；
9. raw rollback若不能恢复sidecar必须明确停止；
10. 100次游戏切换：
    - 线程回到基线；
    - FD不增长；
    - Surface/内存稳定；
11. 30–60分钟：
    - pending最终收敛；
    - temp/transaction不无限增长；
12. 满盘、只读、坏DB、坏archive、坏journal：
    - 安全拒绝或恢复；
    - 最后一份有效副本不静默删除；
    - 有用户可见恢复入口。

---

# 15. 稳定版本质量门禁

正式稳定版本至少满足：

- 全部P0关闭；
- score发布协议与reader约束一致；
- pending `set_progress`不会生成无效merge journal；
- orphan recovery不隔离活跃writer；
- corrupt current在证据保存失败时不被覆盖；
- terminal transaction root不阻塞启动；
- raw replace rollback语义真实；
- Archive loader和pending planner无普通read旁路；
- current/historical active pending策略明确；
- 当前head三平台CI通过；
- main required checks开启；
- wheel/sdist smoke；
- type/lint/coverage/依赖审计；
- LICENSE/NOTICE完成；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代：

- hard-link/replace barrier；
- multiprocess lock；
- crash injection；
- corrupt canonical fixture；
- Archive演进fixture；
- 真实恢复演练。

---

# 16. 推荐实施顺序

## M0：关闭两个发布阻断

1. Score发布协议统一；
2. 所有score canonical操作统一request lock；
3. Progress method matrix；
4. 收窄`set_progress`运行时入口；
5. 对应fault/multiprocess测试。

## M1：恢复硬化

- orphan lock timeout；
- corrupt canonical处理；
- state clock；
- event identity；
- terminal transaction root；
- raw rollback sidecar；
- staging inventory；
- disk preflight。

## M2：兼容和协议封板

- snapshot-only export；
- bounded archive/pending reader；
- historical active pending；
- canonical DB bootstrap；
- slot intent resolution；
- storage protocol freeze ADR。

冻结：

```text
score spool schema
state journal schema
state receipt schema
Archive v3
ImportTransaction
2048 slot schema
```

以后只允许：

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

## M4：玩法和发行

- 五款游戏单机舒适功能；
- Sokoban/Zuma编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 自动发布。

---

# 17. 最终判断

本轮依然是明确进步：

- 第十五次的四项P0均已实质关闭；
- CI和测试规模继续增长；
- fresh replace、Archive历史规则、2048 claim和Sokoban practice均更成熟；
- 五款游戏核心玩法没有发现大面积回退；
- 项目版本和协议文档已经开始体现正式发行意识。

新的严重问题数量已经收敛到两个：

```text
score hard-link publication与single-link reader冲突
set_progress replacement被误当作component merge
```

两者都是修复层新增复杂性暴露出的确定性问题，但都可以在现有架构内局部修复，不需要再新增持久化机制。

> **建议下一轮只完成M0和关键M1，然后正式封板数据层。**
>
> **后续主要开发资源应转向本机档案体验、可访问性、五款游戏内容和三平台桌面发行。**
