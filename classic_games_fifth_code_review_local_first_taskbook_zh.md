# Classic Games Hub 第五次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 当前基线：`main` 分支 commit `00d9dc11c949d9ae1eb1e16405c52b74ee6d5586`（`00d9dc1`）
> 对比基线：上次审查 commit `34cc5c94be4cd03ef171f64bb0ec2dab893d6077`（`34cc5c9`）
> 产品定位：**本地运行、单机为主、默认无网络依赖的经典小游戏合集**
> 审查范围：`client/`、`game_service/`、`server/`、`tests/`、脚本、依赖与仓库治理

---

## 0. 执行摘要

本轮修改是一次成功的可靠性修复。上一轮指出的多数本地存储核心问题已经得到实质解决：

- 本地保存现在由真实后台单线程执行，不再用“已完成 Future”包装同步 SQLite；
- SQLite 正常运行时采用较短锁等待，锁冲突可进入磁盘待保存；
- 共享 `pending_saves.json` 已改为“一请求一文件”的版本化 spool；
- spool 在发布前会校验 canonical payload、hash、attempt UUID 和 revision；
- 坏 spool 会隔离，不再阻断正常启动；
- 稳定 `attempt_uuid + revision` 已贯穿 BaseGame、2048、SQLite 与重放；
- 同一 2048 运行的里程碑和最终成绩不会再自然拆成两条 attempt；
- stale/mismatched `submission_id` 已返回明确错误；
- personal-best rank 和并列时间查询已经改为选择真实最佳 attempt；
- mode、ruleset version 和 status 已进入 schema 与查询层；
- 旧库缺列和坏行不再直接拖垮新库；
- `GameDataService`、`StorageStatus`、`DataResult` 和 `pyproject.toml` 已建立；
- 核心安装不再强制依赖 Flask 和 requests；
- 退出、重置、窗口关闭和 overlay 按钮已基本统一到保存感知的破坏性操作保护。

因此，上一轮最关键的三条基础要求：

1. 保存不阻塞 pygame；
2. 一次运行有稳定身份；
3. pending 不使用跨进程不安全的共享 JSON；

已经基本成立。

但当前仍有三个应在稳定发行前先解决的数据完整性问题：

1. **spool 检出 request ID 负载冲突后，集成保存路径仍可能继续写入新负载并删除原 pending 文件；**
2. **早期版本使用 `str(dict)` 保存 `extra`，当前迁移器只接受 JSON，可能把真实旧成绩整行跳过；**
3. **所有游戏的规则版本仍为 `"1"`，旧漏洞时代的成绩会被导入为当前规则成绩。**

此外，还存在若干高优先级边界：

- 当前 schema 快路径只检查列，不检查唯一约束和索引；
- 同版本但结构损坏的数据库修复前不会自动备份；
- higher revision 可以把高分与低分 extra 混合，也能在 `practice/completed` 间任意切换；
- request/attempt 空字符串会被静默替换，revision/submission ID 没有 SQLite 整数上界；
- 180 天回执过期后，未显式提供 attempt UUID 的旧式请求仍有唯一约束异常风险；
- optional Flask 的 leaderboard 接受 `profile_id` 查询参数后会把它传给不支持该参数的仓储方法；
- 当前实例不会主动发现由另一个仍在运行的进程后来写入的 pending 文件；
- spool 的无硬链接 fallback、目录 fsync、文件名绑定、大小上限和存量隔离通知仍不完整；
- profile 仍以显示名充当身份，昵称没有持久化，IME 输入尚未完成；
- pytest、CI、覆盖率、LICENSE、桌面打包和数据管理 UI 仍缺失。

本轮结论：

> **项目的本地优先主架构已经正确成型，不需要再次大改方向。下一阶段应做“小范围数据一致性修补 + 工程化 + 单机体验”，而不是继续扩大存储抽象，也不应转向联网竞技平台。**

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的工作

- 锁定当前 `main` 最新提交；
- 阅读当前仓库树、提交差异、README、修复记录和新增测试；
- 逐项对照上次审查的 F01–F19；
- 审查：
  - 规范化 mutation；
  - spool envelope 和跨进程发布；
  - 本地 worker；
  - SQLite schema/migration；
  - attempt revision；
  - personal best/recent/stats；
  - BaseGame 保存状态与破坏性操作；
  - 2048 特殊保存状态；
  - 五款游戏的主要状态边界；
  - optional Flask；
  - packaging、测试和脚本；
- 对几个关键问题做最小可执行验证：
  - Python `str(dict)` 不能由 `json.loads()` 解析；
  - 超大 Python int 绑定 SQLite INTEGER 会抛 `OverflowError`；
  - 按当前 `_save_mutation()` 控制流，spool conflict 后若数据库写入成功，会进入删除原 spool 并返回成功的分支；
  - Flask leaderboard 的 `profile_id` 会形成不匹配的关键字参数。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题的正常路径和关键边界均已闭环 |
| **基本到位** | 原缺陷已关闭，但仍有相邻极端边界 |
| **部分到位** | 数据层或接口层完成，产品/UI 尚未完整接入 |
| **代码路径确定** | 从当前控制流可以确定 |
| **最小模型复现** | 使用等价控制流或底层 SQLite/JSON 行为复现 |
| **待跨平台验证** | Linux/本机逻辑合理，但仍需 Windows/macOS/特殊文件系统测试 |
| **产品任务** | 不是当前 Bug，而是符合单机定位的完善方向 |

### 1.3 限制

当前审查运行环境没有安装 `pygame` 和 `Flask`，也无法从外网安装依赖，因此未独立完整运行仓库的：

- pygame headless 回归；
- optional Flask 集成测试；
- 20,000 步压力；
- 渲染 benchmark；
- wheel 运行 smoke。

仓库的 `task.md` 记录了这些测试通过，这是项目自测证据；本报告不会把它们冒充为本次环境的独立复现结果。

本次判断主要基于：

- 当前提交的完整代码路径；
- 新增测试内容；
- GitHub 提交差异；
- JSON、SQLite 和关键状态机的可执行最小验证。

---

## 2. 上一轮问题修复验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| 本地 async 实际同步等待 SQLite | **修复到位** | `LocalWriteWorker` 返回真实 Future；锁等待发生在 worker |
| 5 秒写锁冻结 pygame | **修复到位** | 主线程只做 mutation 规范化；仓储 busy timeout 降至 250 ms |
| 坏 pending JSON 阻断启动 | **修复到位** | 严格 envelope、逐条 parse、quarantine |
| 共享 JSON outbox 跨进程丢写 | **基本到位** | 一 request 一文件、唯一 temp、硬链接排他发布；fallback 和实时发现仍需完善 |
| outbox 在验证前序列化 | **修复到位** | `ScoreMutation` 成为 SQLite/spool/HTTP 的共同规范化入口 |
| 同一运行 pending + final 形成两条 attempt | **修复到位** | BaseGame/2048 使用稳定 attempt UUID 和递增 revision |
| stale `submission_id` 静默插入 | **修复到位** | 不存在返回 404，身份不匹配返回 409 |
| 保存响应 rank 与个人最佳不一致 | **修复到位** | 返回 personal-best rank |
| tie 时间来自无关旧记录 | **修复到位** | 窗口函数选出实际最佳 attempt，并使用 `score_achieved_at` |
| 旧数据库缺列拖垮新库 | **基本到位** | 缺列/坏行可跳过；早期 `str(dict)` extra 尚未兼容 |
| mode/ruleset/status 没接入 | **数据层到位** | schema、写入和查询已支持；游戏 session/UI 尚未显式传递模式 |
| progress/save/settings 空壳 | **按决策处理** | schema v2 删除未使用表，没有虚构已完成功能 |
| request ID payload 冲突 | **直接 outbox 到位，集成路径未到位** | `add_mutation()` 会冲突；`_save_mutation()` 检查顺序仍有漏洞 |
| outbox 错误静默丢弃 | **基本到位** | quarantine 和启动通知已实现；旧隔离记录、错名文件等仍需治理 |
| QUIT/Esc/按钮/R 行为不一致 | **基本到位** | 已统一 `request_destructive_action()`；确认状态超时和文案仍可改善 |
| 本地读取错误伪装为空数据 | **基本到位** | `DataResult/StorageStatus` 已有；启动器仍主要消费 bool/list |
| 核心安装强制 Flask/requests | **修复到位** | `pyproject` core 只依赖 pygame，API 为 extra |
| “游玩次数”定义不准确 | **修复到位** | README 明确当前记录的是结算，不是启动或放弃 |
| 回执无限增长 | **基本到位** | 180 天保留期已建立；过期后的旧式请求边界仍有问题 |
| LAN API 风险 | **修复到位** | Flask 明确为可选调试，README 提示无鉴权风险 |

---

## 3. 当前发布阻断问题

## 3.1 CG5-F01：spool conflict 检查顺序仍可能删除原 pending

- **优先级**：P0
- **证据**：代码路径确定 + 最小模型复现
- **位置**：`game_service/local_backend.py::_save_mutation`

当前流程：

```text
尝试写 spool
  → 发现同 request ID、不同 payload
  → 保存为 spool_error
仍继续调用 SQLite
  → 如果数据库里尚无该 request ID，新的 payload 可以写入成功
  → 删除 request_id 对应 spool 文件
  → 返回成功
最后的 spool_error 分支没有机会执行
```

典型场景：

1. request `R`、payload A 已进入 pending，但尚未写入数据库；
2. 某个 Bug、旧客户端或手工调用使用同一个 `R` 提交 payload B；
3. outbox 正确检测 A/B 冲突；
4. 数据库尚无 `R`，因此 B 被写入；
5. 代码删除保存 A 的 spool 文件；
6. A 被丢弃，request ID 最终绑定到 B。

这违背了“request ID 一经出现，其语义不可改变”的不变量。

### 修复要求

- `StoreError(request_id_conflict)` 必须在访问数据库前立即返回；
- 冲突时不得删除、覆盖或移动原 spool；
- 返回结果包含：
  - `request_id_conflict`
  - 原 payload hash
  - 新 payload hash
  - `retryable=False`
- 增加集成测试，不能只测试 `PersistentSaveOutbox.add_mutation()`；
- 同时覆盖“已有 spool、数据库无 receipt”的关键路径。

---

## 3.2 CG5-F02：真实早期旧成绩可能因 `str(dict)` extra 被整行跳过

- **优先级**：P0（升级路径）
- **证据**：代码路径确定 + JSON 行为复现
- **位置**：
  - 当前：`game_service/store.py::_legacy_rows`
  - 早期版本：`server/app.py` 曾使用 `str(extra)`

早期服务端保存扩展信息时使用：

```python
str(extra)
```

例如：

```text
{'lines': 38, 'level': 4}
```

这不是 JSON。当前迁移器使用：

```python
json.loads(row["extra"])
```

因此这类记录会被计入“无效记录已跳过”，整个成绩也不导入。

早期五款游戏大多数结算都带 `extra`，所以这不是罕见脏行，而可能是旧数据库的主流格式。

### 修复要求

旧库迁移采用分级恢复：

1. 先尝试严格 JSON；
2. 对明确来自旧版本的字段，再尝试受限 `ast.literal_eval()`；
3. 只接受 dict、list、str、int、float、bool、None 的组合；
4. 若 extra 仍无法解析：
   - **保留 score、player、game、timestamp；**
   - 将 `extra_json` 设为 null；
   - 记录“元数据丢失但成绩已恢复”；
5. 不得仅因附加元数据损坏而丢掉基础成绩；
6. 加入由真实旧版本 `str(dict)` 生成的 migration fixture。

---

## 3.3 CG5-F03：旧规则成绩与当前规则成绩仍混为版本 `"1"`

- **优先级**：P0（数据真实性）
- **证据**：代码路径确定
- **位置**：
  - `game_service/catalog.py`
  - legacy migration
  - personal-best/recent 查询

当前五款游戏的 `ruleset_version` 全部是 `"1"`。但是仓库此前已经发生过：

- 推箱子重复累计和完整通关判定修复；
- Tetris 旋转、计时和输入修复；
- 2048 提交与输入队列修复；
- Zuma 连锁与碰撞时序修复；
- 多款游戏的计分或结算语义变化。

旧数据库导入时又使用当前 catalog 的默认版本。因此：

- 旧规则产生的成绩；
- 甚至利用旧漏洞产生的推箱子高分；
- 当前规则产生的成绩；

会出现在同一个“本机最佳”中。

这不是在线反作弊问题，而是本地历史数据解释错误。

### 修复要求

- 为五款游戏建立独立、可读的规则版本，例如：
  - `tetris-assist-2`
  - `snake-classic-1`
  - `2048-classic-2`
  - `sokoban-campaign-2`
  - `zuma-classic-2`
- 旧 `scores` 数据导入为明确的 `legacy-v1`；
- legacy 默认不参与当前个人最佳，但可在“历史记录”中查看；
- 每次计分、胜负或关键规则变化必须提升对应版本；
- README 和游戏内规则页说明版本；
- 增加“旧成绩不会进入当前 best”的迁移测试。

---

## 4. 仍需修复的高优先级问题

## 4.1 CG5-F04：spool 的 fallback 仍不是完整的跨平台原子发布

- **优先级**：P1
- **位置**：`PersistentSaveOutbox.add_mutation`

硬链接路径的设计合理：

```text
完整写唯一 temp
→ fsync temp
→ 排他创建目标硬链接
```

但文件系统不支持 hard link 时，fallback 直接用 `O_EXCL` 写最终目标。另一个进程此时可能读到部分文件并将其隔离。

此外：

- 文件已 fsync，但目录项没有显式 fsync；
- “durable”更准确地说是进程崩溃恢复能力，不能无条件承诺断电级持久化；
- 当前测试没有强制走无 hard-link fallback。

### 修复要求

- 为 Windows、macOS、Linux 和不支持 hard link 的文件系统定义发布协议；
- 推荐：
  - 带跨进程锁的 `temp → replace`；
  - 或单独的 spool SQLite；
  - 或最终文件 + `.ready` 提交标记；
- POSIX 下在发布/删除后 fsync 目录；
- 文案区分“已写入待保存文件”和“数据库已提交”；
- 增加 fallback 故障注入测试。

## 4.2 CG5-F05：spool 文件名未绑定 envelope request ID

- **优先级**：P1
- **位置**：`list_envelopes()` 与 `remove()`

当前会读取 `pending/*.json` 中所有文件，但没有要求：

```text
文件名 == <envelope.request_id>.json
```

若一个合法 envelope 被重命名：

1. 它会被正常加载；
2. 保存成功后代码删除 canonical `<request_id>.json`；
3. 原错名文件仍存在；
4. 下次启动再次重放；
5. 循环永久发生。

重复拷贝同一 envelope 也有类似问题。

### 修复要求

- 加载时严格检查文件名；
- 可安全识别时原子改名到 canonical 名；
- 冲突时隔离；
- `remove()` 应删除本次实际加载的 path，而不是重新推导；
- 对 spool 文件设置大小上限和文件数量告警；
- 增加 misnamed/duplicate spool 测试。

## 4.3 CG5-F06：当前进程不会主动发现另一个进程后来写入的 pending

- **优先级**：P1
- **位置**：`failed_save_count()`、`retry_failed_saves()`

构造时会扫描 spool，`_retry_all()` 也会扫描。但：

- `failed_save_count()` 只看内存；
- `retry_failed_saves()` 若内存 candidate 为空，会直接返回；
- 另一个独立游戏在 launcher 启动之后写入 pending，当前 launcher 不会看到；
- 只能等下次启动，或恰好本进程本身已有失败项触发扫描。

记录没有丢，但恢复状态不实时。

### 修复要求

- 方案 A：本地应用单实例；
- 方案 B：定时或文件系统事件驱动 rescan；
- 方案 C：每次按 S 都强制执行一次轻量扫描；
- UI 显示“发现其他实例留下的待保存记录”；
- 增加“两个长期存活进程”的恢复测试。

## 4.4 CG5-F07：短暂初始化锁会让 store 整个会话保持不可用

- **优先级**：P1
- **位置**：`LocalBackendClient.__init__`、`LocalGameStore.initialize`

如果初始化遇到非损坏型 SQLite 错误，客户端会设置：

```text
store = None
```

之后：

- health 继续失败；
- 所有成绩只进 spool；
- 即使锁或权限问题已解除，也不会重新打开 store；
- 必须重启程序。

另外，fresh schema 或 migration 使用 5 秒初始化预算，首次并发启动仍可能让菜单迟迟不出现。

### 修复要求

- 区分：
  - transient lock；
  - corruption；
  - unsupported schema；
  - permission；
- transient 错误应后台重试初始化；
- UI 先进入“记录服务恢复中”，游戏仍可启动；
- migration 放到可观察的 startup state，不要阻塞首帧；
- 增加“锁释放后同一实例自动恢复并提交 pending”的测试。

## 4.5 CG5-F08：schema 快路径只检查列，不检查约束和索引

- **优先级**：P1
- **位置**：`_schema_is_current()`

一个数据库可以：

- version=2；
- 列全部存在；
- 但缺少 attempt UUID 唯一索引、request ID 唯一约束或 best/recent 索引。

当前会直接判定为 current，不修复。

同样，如果 version 已是 2 但结构不完整，进入修复前不会自动备份，因为备份条件只看：

```text
existing_version < SCHEMA_VERSION
```

### 修复要求

- 检查：
  - `PRAGMA index_list`
  - `PRAGMA index_info`
  - `sqlite_master.sql`
  - 必需唯一约束
- 同版本结构修复前也要备份；
- schema 使用明确 migration step，而不是只靠“补列”；
- 加入 missing-index、wrong-constraint、same-version-backup 测试。

## 4.6 CG5-F09：显式空 request/attempt ID 被静默替换

- **优先级**：P1
- **位置**：`mutation.py::_transport_id`

当前逻辑：

```python
value = value or uuid.uuid4().hex
```

因此调用方显式传入：

```text
request_id=""
attempt_uuid=""
```

不会收到参数错误，而会得到随机 ID。

这会隐藏调用方 Bug，并破坏“显式传入 ID 就必须稳定”的契约。

### 修复要求

- 只有 `None` 才自动生成；
- 空字符串、空白和错误格式一律拒绝；
- `attempt_uuid_provided` 只在真实合法 ID 被提供时为真；
- HTTP、本地和 spool 测试使用同一契约。

## 4.7 CG5-F10：revision 与 submission ID 缺少 SQLite 整数上界

- **优先级**：P1
- **证据**：底层 SQLite 行为已复现
- **位置**：`normalize_score_mutation`

当前只要求正整数。超出 64 位整数范围时，sqlite3 绑定会抛：

```text
OverflowError: Python int too large to convert to SQLite INTEGER
```

该异常不是 `sqlite3.Error`，当前 worker 不会转换成稳定 StoreError；已写入的 spool 还可能成为每次启动都失败的“毒记录”。

### 修复要求

- 限制：
  - `revision <= 2^63 - 1`
  - `submission_id <= 2^63 - 1`
- worker 最外层兜底返回 typed permanent failure；
- 意外异常必须写结构化日志；
- poison spool 可隔离并导出；
- 增加 huge-int 测试。

## 4.8 CG5-F11：高 revision 可以制造 score/extra 不一致

- **优先级**：P1
- **位置**：`record_mutation()`

higher revision 更新时：

```text
stored_score = max(old_score, incoming_score)
extra_json = incoming_extra
```

如果新 revision 的分数更低：

```text
数据库 score = 200
extra = {"max_tile": 8}   # 来自分数 150 的状态
```

分数与元数据不再代表同一状态。

`status` 也可在 `practice` 与 `completed` 之间任意变化；如果分数不变，`score_achieved_at` 仍可能来自之前的 practice 状态。

### 修复要求

为每个游戏声明 merge policy：

```text
final_only
monotonic_score
metadata_only_same_score
```

建议：

- 2048、Sokoban：revision 分数不得下降；
- Tetris/Snake/Zuma：通常只允许一次终态结算；
- status 对一次结算 attempt 应不可变；
- 若未来引入生命周期，使用明确转换表；
- extra 必须与最终被接受的 score/revision 成对保存。

## 4.9 CG5-F12：180 天 receipt 过期后的旧式 idempotency 仍不完整

- **优先级**：P1
- **位置**：`record_mutation()`

未显式传 `attempt_uuid` 时，normalizer 会从 request ID 派生一个稳定 UUID，但仓储只有在：

```text
attempt_uuid_provided == True
```

时才按 UUID 查已有 attempt。

因此 receipt 被清理后，旧式请求重放：

- 派生 UUID 与原记录相同；
- 但不会查询；
- 尝试 INSERT；
- 命中 attempt UUID/request ID 唯一约束；
- 可能被误判为 retryable database error。

### 修复要求

- 无论 UUID 是显式还是由 request ID 派生，都先按 UUID 查询；
- receipt 只是响应快照缓存，不应是唯一幂等基础；
- `sqlite3.IntegrityError` 必须区分：
  - idempotent replay；
  - payload conflict；
  - schema/data corruption；
- 不得统一标记为“数据库暂时不可用”。

## 4.10 CG5-F13：坏 response snapshot 与 IntegrityError 缺少稳定分类

- **优先级**：P1
- **位置**：`record_mutation()`、`_save_mutation()`

风险：

- `save_requests.response_json` 损坏时，`json.loads()` 抛 `ValueError`；
- 所有 `sqlite3.Error` 在 LocalBackend 中都成为 retryable；
- 永久唯一约束/完整性错误可能进入 spool 并永远重试。

### 修复要求

建立错误映射：

| 异常 | 结果 |
|---|---|
| locked/busy | retryable |
| disk full / I/O transient | 视平台分类 |
| IntegrityError | permanent conflict 或 repair-needed |
| malformed response snapshot | receipt_corrupt，可重建/隔离 |
| malformed schema | database_corrupt |
| programming error | internal_error + traceback |

## 4.11 CG5-F14：optional Flask leaderboard 的 `profile_id` 参数会触发 TypeError

- **优先级**：P1
- **位置**：
  - `server/app.py::query_dimensions`
  - `LocalGameStore.leaderboard`

Flask 从 query string 收集：

```text
profile_id
mode
ruleset_version
status
```

然后将全部传给 leaderboard；仓储 leaderboard 不接受 `profile_id`。

请求：

```text
GET /api/leaderboard/tetris?profile_id=alice
```

会形成意外关键字参数。当前没有通用 JSON TypeError handler，可能返回 HTML 500。

### 修复选择

- leaderboard 是“所有本机档案最佳”：拒绝 `profile_id`，返回 400；
- 或支持“指定档案最佳”，正式增加参数；
- 不要让 query_dimensions 对不同端点盲目复用；
- 所有 mode/ruleset/status 查询也应使用共享校验；
- 增加 optional API contract tests。

## 4.12 CG5-F15：游戏层尚未真正使用 mode/profile/ruleset/status session

- **优先级**：P1
- **性质**：接入不完整

数据层已经支持这些维度，但 BaseGame 提交时主要依赖默认值：

```text
profile_id = player
mode = classic
ruleset = catalog default
status = completed
```

当前五款游戏只有经典结算，暂时可用；但一旦加入：

- Tetris 舒适模式；
- Snake 穿墙；
- Sokoban 单关练习；
- Zuma 训练；

若 UI 忘记传参数，记录仍会混入 classic。

### 修复要求

建立 `AttemptContext`：

```text
profile_id
game_id
mode
ruleset_version
status
attempt_uuid
revision
```

由启动器/游戏注册表一次创建，不让各游戏手拼默认值。

## 4.13 CG5-F16：本机 profile 仍只是显示名字符串

- **优先级**：P1/P2
- **性质**：本地产品任务

当前：

- nickname 不持久化；
- 默认每次启动为 guest；
- `profile_id` 默认等于 player；
- 改名相当于新档案；
- 同名家庭成员合并；
- 输入仍用 `KEYDOWN.unicode`，无完整 IME。

不需要账号系统。只需本机档案：

```text
profile_uuid
display_name
last_used
created_at
```

默认一个档案，可选家庭成员档案。

## 4.14 CG5-F17：worker 单队列缺少优先级与有界恢复

- **优先级**：P1/P2
- **位置**：`LocalWriteWorker`

同一个单线程队列处理：

- score write；
- leaderboard/recent；
- health；
- maintenance；
- 大量 pending replay。

优点是 SQLite 写入简单；缺点是数百 pending 恢复时：

- health 和首页数据可能长时间排队；
- 新成绩也可能排在旧的非关键读任务之后；
- close 会等待所有 queued read。

### 修复要求

- 保持单 SQLite writer；
- 非关键读使用独立只读 executor；
- score/spool 优先于 maintenance；
- pending replay 分批；
- 自动退避重试，不依赖玩家反复按 S；
- close 取消非必要读，只 drain write/spool；
- 为退出时间设置可测预算。

## 4.15 CG5-F18：确认状态没有超时，UI 信息仍可能过期

- **优先级**：P2
- **位置**：BaseGame 与 launcher

第一次尝试退出后，确认状态没有时间期限。玩家继续游戏一段时间，再次触发相同操作仍可能直接执行。

另外：

- 后续新增 quarantine 不一定刷新启动器 recovery notice；
- `StorageStatus/DataResult` 尚未完整显示；
- “待保存记录已安全落盘”应更精确地说“已写入待保存文件”；
- 本机页面仍大量使用“排行/Top”语言，而项目实质是个人最佳和家庭本机记录。

## 4.16 CG5-F19：spool 没有大小、数量和错名治理

- **优先级**：P1

需要补充：

- 单文件最大大小；
- pending 总数量/总大小告警；
- 单次启动最大扫描量；
- 错名 envelope；
- duplicate envelope；
- 旧 quarantine 的持续通知；
- 手动导出/清理；
- `attempt_count` 当前未更新，应实现或删除。

## 4.17 CG5-F20：迁移 marker 不是按源文件身份管理

- **优先级**：P1

当前外部 legacy 使用单一 `legacy_scores_v2` marker：

- 更换另一个旧库后不会自动导入；
- 修复此前缺字段的旧库后也不会重试；
- 同名不同路径的旧库 attempt UUID/marker 语义不够清晰；
- embedded legacy table 仍长期保留；
- schema v2 修复会直接 DROP 之前的 progress/save/settings。

### 修复要求

marker 至少包含：

```text
source path
size
mtime
optional content hash
imported/skipped counts
migration version
```

对于曾存在的空壳表，先 rename/archive，再决定删除，不直接丢弃潜在数据。

## 4.18 CG5-F21：本地 sync API 静默忽略未知关键字

- **优先级**：P2
- **位置**：`LocalBackendClient.submit_score(..., **_ignored)`

本地同步接口会忽略额外字段，而 async/HTTP 会拒绝。这会造成同一 payload 在不同适配器中表现不同。

应删除 `**_ignored`，所有适配器共享一个严格契约。

---

## 5. 分模块审查

## 5.1 `game_service/mutation.py`

### 优点

- canonical JSON；
- `allow_nan=False`；
- extra 大小限制；
- 统一 player/profile/mode/ruleset/status；
- transport ID 和 attempt ID；
- semantic payload hash；
- SQLite、spool、HTTP 共用。

### 仍需处理

- 显式空 transport ID；
- revision/submission ID 上界；
- mode/ruleset 应来自注册表/模式定义，而不是任意字符串；
- status transition 不在 mutation 层表达；
- `_identifier` 建议先 Unicode normalize 再检查长度；
- 错误类型应区分 extra JSON 与 envelope/response JSON。

## 5.2 `game_service/local_backend.py`

### 优点

- 真异步；
- 单 writer；
- durable spool；
- 一 request 一文件；
- hash 冲突；
- quarantine；
- legacy pending 迁移；
- typed read result；
- close/drain；
- 核心不依赖 requests。

### 仍需处理

- conflict 检查顺序；
- fallback 发布；
- filename binding；
- live rescan；
- self-healing store reopen；
- worker priority；
- unexpected exception typed result；
- old quarantine 持续通知；
- size/count limit；
- directory fsync；
- retry bookkeeping 回调收尾；
- durable 语义精确文案。

## 5.3 `game_service/store.py`

### 优点

- schema version；
- migration transaction；
- backup；
- attempt UUID/revision；
- stale no-op；
- strict submission ID；
- response snapshot；
- receipt retention；
- personal best；
- best-attempt tie；
- mode/ruleset/status 查询；
- legacy row isolation；
- WAL 和短 transaction。

### 仍需处理

- actual old extra format；
- current-schema constraint verification；
- same-version backup；
- status/score-extra merge policy；
- receipt expiry；
- error classification；
- legacy marker；
- source-specific ruleset；
- settings/progress/save slot repository；
- data export/reset；
- DB-level CHECK constraints；
- persistent profile identity。

## 5.4 `client/common/ui.py`

### 优点

- 保存状态清晰；
- stable attempt/revision；
- retry 重用相同 ID；
- ACK 后刷新；
- 统一 destructive guard；
- overlay 防穿透；
- 失焦暂停。

### 仍需处理

- 状态仍是字符串；
- BaseGame 和 2048 仍有两套保存状态机；
- 确认状态无超时；
- broad exception；
- 恢复/数据库错误细节不够；
- 每帧创建大量 Surface/Button；
- 固定窗口；
- 字体 fallback；
- 设置、音频、按键、可访问性尚无统一服务。

## 5.5 启动器

### 已改善

- 默认 local；
- HTTP 懒加载；
- core 无 requests；
- 个人最佳和最近记录；
- hover 使用 dt；
- 游戏注册表基本统一。

### 仍需处理

- `main()` 仍承担数据、输入、布局、游戏生命周期；
- 五卡固定布局；
- 主要依赖鼠标；
- nickname 不持久；
- IME 不完整；
- health/records 状态信息较粗；
- 本机进度/继续游戏尚未展示；
- 确认状态无超时；
- 游戏启动错误只打印终端；
- 图标和部分 Surface 每帧重建；
- profile 不是稳定身份。

## 5.6 Tetris

核心输入和长帧问题未见回退。

适合后续的单机完善：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- 规则帮助页；
- 可注入 RNG；
- 静态网格缓存；
- 同义物理键应按“逻辑动作边沿”决定是否重复立即移动；
- 保持当前“辅助旋转”命名，不要重新声称标准 SRS。

## 5.7 Snake

已有修复保持：

- 双转向队列；
- 长停顿只走一步；
- 吃食物后重新计算 interval。

后续：

- 可注入 RNG；
- 个人最佳按模式；
- 速度/穿墙/障碍；
- 双人同屏；
- 静态棋盘缓存；
- 色弱形状/纹理。

## 5.8 2048

已有修复保持：

- 无效果方向不会阻塞队列；
- win/pause/reset 边界清队列；
- attempt UUID/revision；
- pending milestone/final 合并。

剩余：

- 与 BaseGame 平行的保存状态机应统一；
- queued final 不应只按 score 判断是否有更新，same-score extra/final state 也应提交；
- 自动存档；
- 撤销；
- 本地最高分页；
- R 重开确认；
- 可注入 RNG；
- 棋盘尺寸；
- 静态背景缓存。

## 5.9 Sokoban

核心计分修复保持：

- 同关不重复累计；
- 跳关进入练习；
- 完成全部关卡才提交；
- pending/confirmed 分离；
- stable attempt。

后续：

- progress repository；
- 选关和解锁；
- 最少移动/推动；
- 是否使用撤销；
- 星级；
- 死锁检测；
- 提示；
- 地图闭合/可达/静态死角；
- XSB 导入；
- 编辑器；
- 固定逻辑窗口。

## 5.10 Zuma

核心修复保持：

- 多 pending reactions；
- 球对象边界；
- swept projectile；
- bisect path；
- 长帧计时余量；
- 临界救场顺序。

后续：

- reaction FSM；
- 多重反应属性测试；
- RNG；
- `incoming` deque；
- 训练和选关；
- 色弱符号；
- 缓存 atmosphere/aim/particle surfaces；
- 原创道具与轨道编辑器应在 FSM 稳定后再做。

## 5.11 optional Flask

### 优点

- app factory；
- 默认 loopback；
- 可选 extra；
- 复用同一 store；
- body limit；
- JSON errors；
- LAN 风险文档。

### 剩余

- leaderboard `profile_id`；
- query schema；
- unexpected exception JSON 化；
- HTTP client 的 failed-save 仍是内存语义；
- HTTP replace cleanup 仍按 game/player，而不是 attempt UUID；
- API 只保留调试价值，不应反过来决定桌面架构。

## 5.12 测试与工程

### 优点

- 新增 storage v2 边界测试；
- 32 进程 spool；
- forced process exit；
- locked async latency；
- migration rollback；
- legacy bad row；
- mode/ruleset/status；
- UI retry identity；
- core import without requests；
- 固定 seed stress。

### 缺口

- 集成 spool conflict 顺序；
- 真实旧 `str(dict)` extra；
- ruleset legacy isolation；
- no-hardlink fallback；
- misnamed/oversized spool；
- live cross-process discovery；
- self-healing store；
- missing index/current-version repair；
- huge revision；
- empty request ID；
- score/extra regression；
- practice/completed transition；
- receipt expiry；
- corrupt response snapshot；
- Flask leaderboard profile query；
- immediate crash before worker begins spool；
- core-only test command；
- pytest fixture/marker；
- CI；
- coverage/JUnit；
- Windows/macOS spool 与数据目录；
- LICENSE。

---

## 6. 明确非目标

本项目不需要建设：

- 用户注册；
- 云端账号；
- 公网排行榜；
- 匹配系统；
- 赛季；
- 实时联机；
- 反作弊；
- 服务端权威重放；
- replay 审核；
- 在线商城；
- 强制联网；
- 默认遥测；
- 复杂权限系统。

可选 Flask 继续定位为：

- 教学；
- 调试；
- 本机 API 示例；
- 可选数据查看接口。

---

## 7. 推荐目标架构

当前架构无需推倒重来。建议在现有边界上增量完善：

```text
Launcher / Game
    │
    ├── GameDescriptor
    ├── AttemptContext
    │       ├── profile_uuid
    │       ├── game_id / mode / ruleset
    │       ├── attempt_uuid / revision
    │       ├── status
    │       └── score merge policy
    │
    ├── GameDataService Protocol
    │       ├── score result
    │       ├── progress
    │       ├── save slots
    │       ├── settings
    │       └── data management
    │
    ├── LocalReadService
    └── LocalWriteWorker
            ├── LocalGameStore (SQLite)
            └── PersistentSaveOutbox
                    └── versioned per-request records

Optional Flask Adapter
    └── same service, no separate business rules
```

### 7.1 Game rule descriptor

```python
@dataclass(frozen=True)
class GameModeDescriptor:
    game_id: str
    mode: str
    ruleset_version: str
    score_policy: Literal["final_only", "monotonic_revision"]
    ranked_locally: bool
```

这里的 `ranked_locally` 只表示是否进入本机最佳，不涉及在线竞技。

### 7.2 Save result

```text
COMMITTED
DURABLE_PENDING
PERMANENT_FAILURE
RECOVERY_REQUIRED
```

UI 不再从散落的 bool 推断含义。

### 7.3 本地数据产品

建议最终增加：

```text
profiles
attempts
progress
save_slots
settings
request_receipts
```

个人最佳由 attempts 查询或缓存，不应与 attempt 本身混为一表一行。

---

## 8. 完整优化任务清单

### 8.1 优先级定义

- **P0**：旧数据错误导入、request 语义被覆盖或当前规则最佳失真；稳定发行阻断。
- **P1**：数据边界、迁移、自恢复、API 契约与工程门禁；稳定版前完成。
- **P2**：设置、输入、性能、可访问性和代码解耦。
- **P3**：内容、打包、展示和长期产品能力。
- **S/M/L/XL**：相对工作量。

---

## 8.2 P0：关闭数据完整性缺口

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG5-P0-01 | 修复 spool conflict 顺序 | fail-fast 保存路径 | outbox conflict 时不访问 DB、不删除原文件，返回 409 | S |
| CG5-P0-02 | 增加集成 conflict 测试 | local backend test | 已有 pending A、DB 无 receipt、提交 B 后 A 仍完整且 DB 无 B | M |
| CG5-P0-03 | 兼容旧 `str(dict)` extra | legacy decoder | 真实旧格式可恢复；非法 extra 只丢元数据，不丢 score | M |
| CG5-P0-04 | 建立 legacy ruleset | per-game legacy version | 旧成绩不进入当前最佳，可单独查看 | M |
| CG5-P0-05 | 为当前五款游戏定规则版本 | catalog + docs | 每款规则版本与当前行为对应；未来规则变更有升级约定 | M |
| CG5-P0-06 | 增加真实旧库 fixture | migration fixtures | 覆盖 f12/f88 格式、extra、updated_at、坏行和重复行 | L |
| CG5-P0-07 | 数据迁移预览与报告 | import summary | 导入、跳过、仅恢复分数、legacy 隔离数量清晰 | M |
| CG5-P0-08 | P0 发布门禁 | CI gate | 任一 P0 数据场景失败时禁止发布 | S |

---

## 8.3 P1：仓储、迁移与工程基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG5-P1-01 | spool filename 与 envelope 绑定 | canonical path validation | 错名文件被改名或隔离，不会永久重放 | M |
| CG5-P1-02 | spool 大小/数量限制 | quotas + warning | 超大文件不 OOM；数量过多可分批恢复 | M |
| CG5-P1-03 | hard-link fallback 原子化 | cross-platform publish | 无 hard link 文件系统下并发无 partial quarantine | L |
| CG5-P1-04 | 完善断电级持久化语义 | directory fsync + docs | POSIX 目录项持久；文案不夸大保证 | M |
| CG5-P1-05 | live spool discovery | rescan/event | 另一个运行中实例新增 pending 后，当前 launcher 可发现 | M |
| CG5-P1-06 | store 自恢复 | reopen state machine | transient lock/permission 恢复后无需重启即可提交 | L |
| CG5-P1-07 | 异步初始化/迁移状态 | startup controller | 首帧不因 5 秒 migration budget 阻塞 | L |
| CG5-P1-08 | schema 约束检查 | index/DDL verifier | 缺索引、缺 UNIQUE 和错误 table SQL 可被识别 | M |
| CG5-P1-09 | 同版本修复前备份 | repair backup | version=2 但结构异常时仍先备份 | S |
| CG5-P1-10 | 正式 migration steps | migration registry | 每一步可测试、可回滚、可重复 | L |
| CG5-P1-11 | transport ID 严格化 | validator | 显式空 ID 拒绝；None 才自动生成 | S |
| CG5-P1-12 | SQLite integer 边界 | validator | huge revision/submission ID 返回 typed 400 | S |
| CG5-P1-13 | attempt merge policy | game score policies | 低分 revision 不污染 high-score extra；status 不非法变化 | L |
| CG5-P1-14 | receipt 过期后幂等 | derived UUID lookup | 删除 receipt 后重放仍安全 no-op | M |
| CG5-P1-15 | SQLite 错误分类 | error mapper | lock 可重试；IntegrityError 不进入永久重试 | M |
| CG5-P1-16 | receipt corruption 恢复 | repair/quarantine | 坏 response JSON 不让 worker 抛裸异常 | M |
| CG5-P1-17 | source-specific migration marker | fingerprint marker | 更换/修复旧库可重新评估，不重复导入 | M |
| CG5-P1-18 | 旧空壳表安全处理 | archive migration | 不直接 DROP 潜在用户数据 | S |
| CG5-P1-19 | worker 优先级 | writer/read separation | 成绩写与 spool 优先于首页读取和 maintenance | L |
| CG5-P1-20 | 自动 pending 重试 | bounded backoff | 锁解除后自动恢复，仍允许手动重试 | M |
| CG5-P1-21 | 关闭语义和预算 | write-only drain | 退出只等待必要写入，非关键读可取消 | M |
| CG5-P1-22 | 完整 StorageStatus UI | status panel | 可区分 DB、spool、迁移、quarantine 和只读状态 | M |
| CG5-P1-23 | 修复 Flask query dimensions | endpoint schemas | profile_id 不再触发 500；非法维度返回 JSON 400 | M |
| CG5-P1-24 | optional HTTP attempt 语义 | attempt-keyed retry | 不按 game/player 清除另一局失败记录 | M |
| CG5-P1-25 | 严格同步接口 | remove `**_ignored` | Local/HTTP 对未知字段行为一致 | S |
| CG5-P1-26 | AttemptContext 接入 BaseGame | session object | profile/mode/ruleset/status 不靠隐式默认散落 | L |
| CG5-P1-27 | 本机 profile UUID | profiles repository | 默认单档案；同名不合并；无需账号 | L |
| CG5-P1-28 | nickname 持久化 | last-used profile | 重启后保留名称；可切换家庭成员 | M |
| CG5-P1-29 | 数据导出/导入/清理 service | backup API | 用户无需操作 SQLite 即可备份和恢复 | L |
| CG5-P1-30 | receipt/history 保留策略 | data retention | 可查看、导出、按游戏清理，不误删 pending | M |
| CG5-P1-31 | 结构化日志 | rotating logs | worker、migration、quarantine、游戏异常有 traceback | M |
| CG5-P1-32 | 错误恢复页面 | recovery UI | 可打开日志、导出隔离文件、重建 DB | L |
| CG5-P1-33 | 迁移到 pytest | fixtures/markers | 测试可单独运行；不再 import 即执行 | XL |
| CG5-P1-34 | core-only 测试命令 | no-api suite | 不安装 Flask/requests 也能测试桌面核心 | M |
| CG5-P1-35 | GitHub Actions | OS matrix | Linux/Windows/macOS smoke，上传 JUnit/日志 | L |
| CG5-P1-36 | 质量门禁 | Ruff/type/coverage | PR 自动执行；核心规则覆盖率可见 | M |
| CG5-P1-37 | 整理根目录文档 | `docs/audits`/`docs/adr` | 历史审查和工作日志不占据项目入口 | S |
| CG5-P1-38 | LICENSE 与 NOTICE | legal files | 代码和第三方素材许可明确 | M |

---

## 8.4 P2：单机体验、维护性与性能

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG5-P2-01 | 统一 GameState enum | state transitions | 无散落字符串，进入/退出 hook 可测试 | L |
| CG5-P2-02 | 合并 BaseGame/2048 保存控制器 | SaveController | 五款游戏只使用一套保存状态机 | L |
| CG5-P2-03 | InputManager | action map | 按键状态、队列、失焦和暂停统一 | L |
| CG5-P2-04 | IME 输入 | TEXTINPUT/TEXTEDITING | 中日韩组合输入可用 | M |
| CG5-P2-05 | 键位重映射 | binding UI | 冲突检测、恢复默认、持久化 | L |
| CG5-P2-06 | 键盘/手柄菜单 | focus navigation | 不用鼠标可完成全部主要操作 | L |
| CG5-P2-07 | settings repository/UI | settings screen | 窗口、音量、键位、辅助项持久化 | L |
| CG5-P2-08 | 音频系统 | BGM/SFX | 无设备时不崩；分组音量和静音 | L |
| CG5-P2-09 | 可调窗口和 DPI | logical canvas | 常见分辨率不裁切，高 DPI 清晰 | XL |
| CG5-P2-10 | 字体与资源管理 | font fallback | 缺系统 CJK 字体仍能显示 | M |
| CG5-P2-11 | 可访问性 | colorblind/high contrast | 颜色不是唯一信息通道，动态效果可降低 | L |
| CG5-P2-12 | Clock/RNG 注入 | deterministic services | 相同 seed + commands 可重现 | L |
| CG5-P2-13 | 渐进抽取纯规则 Engine | rules modules | 核心逻辑测试无需 SDL | XL |
| CG5-P2-14 | 静态 Surface 缓存 | profiler-driven cache | 只优化实测热点，有失效规则 | L |
| CG5-P2-15 | launcher 拆分 | App/Renderer/Data/GameRunner | `main()` 不再承担全部职责 | L |
| CG5-P2-16 | Zuma reaction FSM | explicit state | 重叠连锁确定、可属性测试 | L |
| CG5-P2-17 | 可重复 benchmark | benchmark CLI | 带机器、OS、窗口、seed 和锁竞争场景 | M |
| CG5-P2-18 | soak 测试 | 30–60 min run | 线程、FD、Surface、内存达到稳定平台 | M |
| CG5-P2-19 | progress repository | progress API | Sokoban/Zuma 进度原子保存 | L |
| CG5-P2-20 | save slots | versioned saves | 2048 可保存/继续，坏存档安全隔离 | L |
| CG5-P2-21 | 数据管理 UI | history/backup/reset | 用户可查看历史、备份和清理 | M |
| CG5-P2-22 | 游戏内规则页 | controls/rules/version | 计分、模式和 ruleset 可查看 | M |

---

## 8.5 P3：游戏内容与桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG5-P3-01 | Tetris 舒适模式 | 7-bag/ghost/hold/lock delay | 与当前辅助旋转分模式记录 | XL |
| CG5-P3-02 | Snake 多模式 | speed/wrap/obstacle/local-2P | 每模式独立最佳 | L |
| CG5-P3-03 | 2048 完整体验 | undo/autosave/board size | 存档和 attempt 不重复 | L |
| CG5-P3-04 | Sokoban 关卡进度 | unlock/stars/best moves/pushes | 练习与闯关分开 | L |
| CG5-P3-05 | Sokoban 死锁与提示 | deadlock/hint | 可关闭，规则明确 | XL |
| CG5-P3-06 | Sokoban 编辑器 | XSB import/export | 地图验证、预览、版本化 | L |
| CG5-P3-07 | Zuma 训练和选关 | training/accessibility | 训练记录不混入完整通关 | L |
| CG5-P3-08 | Zuma 原创机制 | power balls/tracks/goals | 每项有确定性测试 | XL |
| CG5-P3-09 | Zuma 轨道编辑器 | path tool | 路径合法、可预览和版本化 | L |
| CG5-P3-10 | 本机成就 | local achievements | 无账号、无遥测、导入不重复触发 | L |
| CG5-P3-11 | 离线每日挑战 | date seed | 完全离线，同版本可重现 | L |
| CG5-P3-12 | 本地 replay | command log/viewer | 用于复盘和调试，不用于反作弊 | L |
| CG5-P3-13 | 本地化 | zh/en resources | 主要 UI 无硬编码，长文本测试 | L |
| CG5-P3-14 | Windows 打包 | installer/portable | 无 Python 可运行，升级不丢数据 | XL |
| CG5-P3-15 | macOS 打包 | app bundle | 数据目录、字体和关闭流程 smoke | XL |
| CG5-P3-16 | Linux 打包 | AppImage/等效 | XDG 路径正确，常见发行版 smoke | L |
| CG5-P3-17 | 自动发布 | tagged builds | smoke 通过才生成 release | L |
| CG5-P3-18 | 截图/GIF/主页 | showcase | README 首屏展示玩法和本地优先 | M |
| CG5-P3-19 | 社区文件 | CONTRIBUTING/SECURITY | Bug 模板包含版本、日志和复现 | S |
| CG5-P3-20 | 谨慎新增游戏 | template/contract tests | 新游戏同时交付规则、记录、输入和测试 | XL |

### 适合新增的本地游戏

- 扫雷；
- 打砖块；
- 四子棋；
- 五子棋；
- 数独；
- 华容道；
- 记忆翻牌；
- 连连看；
- 双人同屏球拍；
- 原创纵向射击；
- 弹球；
- 迷宫。

不建议以“游戏数量”作为主要质量指标。每款新游戏应同时具备：

- 确定性规则；
- attempt/progress 语义；
- 暂停、失焦和返回；
- 键盘操作；
- 可访问性；
- 资源许可；
- 边界和长帧测试。

---

## 9. 必须新增的测试

### 9.1 P0 数据测试

```text
test_integrated_spool_conflict_never_touches_database
test_integrated_spool_conflict_keeps_original_file
test_legacy_python_repr_extra_is_recovered
test_invalid_legacy_extra_keeps_base_score
test_legacy_ruleset_does_not_enter_current_personal_best
test_each_game_has_explicit_ruleset_version
```

### 9.2 spool 与 worker

```text
test_fallback_publish_never_exposes_partial_file
test_misnamed_valid_spool_is_renamed_or_quarantined
test_duplicate_spool_files_do_not_replay_forever
test_oversized_spool_is_rejected_without_oom
test_running_process_discovers_external_pending
test_transient_store_init_recovers_without_restart
test_close_cancels_reads_but_drains_writes
test_directory_entry_is_synced_or_durability_is_documented
test_legacy_pending_nan_does_not_crash_quarantine
```

### 9.3 mutation 与 attempt

```text
test_explicit_empty_request_id_is_rejected
test_explicit_empty_attempt_uuid_is_rejected
test_revision_over_sqlite_int64_is_rejected
test_submission_id_over_sqlite_int64_is_rejected
test_lower_score_revision_cannot_replace_high_score_extra
test_attempt_status_is_immutable
test_receipt_expiry_replay_is_idempotent
test_integrity_error_is_not_retried_as_busy
test_corrupt_response_snapshot_returns_typed_error
```

### 9.4 migration

```text
test_same_version_missing_index_is_repaired_after_backup
test_current_version_wrong_unique_constraint_is_detected
test_source_fingerprint_allows_second_legacy_database
test_repaired_legacy_database_can_be_reimported
test_progress_tables_are_archived_not_blindly_dropped
test_real_f12_database_fixture
test_real_f88_database_fixture
```

### 9.5 API

```text
test_leaderboard_profile_query_is_supported_or_rejected_json
test_query_dimensions_are_validated_per_endpoint
test_all_unexpected_api_errors_remain_json
test_http_attempt_retry_is_keyed_by_attempt_uuid
```

### 9.6 游戏属性测试

#### Tetris

- generation 变化后 accumulator 不跨块；
- 同义键逻辑动作边沿；
- 所有锁定格在边界内；
- 若加入 7-bag，每袋包含七种块；
- 自定义旋转按独立规格验证。

#### Snake

- 不接受 180°；
- 两个合法快速转向按序；
- 长停顿策略；
- 身体长度不变量；
- 食物不在身体内。

#### 2048

- 每 tile 每步最多合并一次；
- 总值守恒；
- 新 tile 只增加 2/4；
- 无效果队列不阻塞；
- win/pause/reset 清空输入；
- same-score final metadata 不丢失；
- save/undo/recovery 一致。

#### Sokoban

- 同关不重复累计；
- 练习不进入 campaign best；
- 全部完成才完整通关；
- 闭合/可达/箱目标；
- 死角；
- 最佳移动与推动。

#### Zuma

- 多 pending 不丢；
- 重叠反应顺序确定；
- chain 单调；
- 长帧余量；
- 临界救场；
- reaction replay。

---

## 10. 性能与稳定性门槛

必须记录机器、OS、Python/打包版本、窗口尺寸和 seed。

1. pygame 主线程不执行磁盘 I/O；
2. `submit_score_async()` p99 ≤2 ms；
3. 锁冲突在预算内返回 committed 或 durable pending；
4. 正常游戏目标 60 FPS，基准机 p95 ≤16.7 ms；
5. 保存、spool 扫描、leaderboard 不产生 >50 ms 主线程长帧；
6. 32 进程 spool：
   - 不丢记录；
   - 不读 partial；
   - 同 ID 同 payload 幂等；
   - 同 ID 不同 payload 冲突；
7. 强制进程终止后 pending 可恢复；
8. 同一次 2048 只有一个 attempt；
9. 100 次切换游戏：
   - 线程回基线；
   - FD 不增长；
   - Surface/内存稳定；
10. 30–60 分钟：
    - pending 最终清空；
    - 无未处理 lock；
    - 无持续内存增长；
11. 坏 DB、只读目录、磁盘满、坏 spool、坏旧库：
    - 游戏仍可启动；
    - 原数据不被覆盖；
    - 有恢复入口。

---

## 11. 稳定版本质量门禁

第一个正式稳定桌面版本建议满足：

- 所有 P0 关闭；
- 真实旧数据库迁移通过；
- legacy ruleset 与 current 分离；
- request conflict 不会改变已存在 pending；
- 默认无 Flask/HTTP；
- 本地提交不阻塞 pygame；
- pending 跨重启和多进程可靠；
- attempt、personal best、recent、profile、ruleset 语义准确；
- 数据位于用户目录；
- schema migration、backup、export 完整；
- core-only 测试可运行；
- pytest + CI；
- Windows/macOS/Linux smoke；
- 纯规则模块建议：
  - 行覆盖率 ≥90%；
  - 分支覆盖率 ≥85%；
- 全项目建议行覆盖率 ≥80%；
- formatter、lint、typing、依赖审计通过；
- LICENSE、CHANGELOG、数据恢复文档完整；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代迁移 fixture、并发测试、属性测试和真实玩家测试。

---

## 12. 推荐实施顺序

### M0：修正当前数据完整性

1. spool conflict fail-fast；
2. 真实旧 extra 兼容；
3. legacy/current ruleset 隔离；
4. 对应 P0 测试。

### M1：仓储硬化

- filename/size/fallback；
- live rescan；
- store self-heal；
- schema constraints；
- numeric bounds；
- merge policy；
- receipt expiry；
- error classification；
- migration marker。

### M2：完成本地数据产品

- AttemptContext；
- 本机 profile；
- progress；
- save slots；
- settings；
- backup/import/history；
- 首页进度和继续游戏。

### M3：工程化与体验

- pytest/CI/type/coverage；
- launcher 拆分；
- InputManager；
- IME；
- 手柄；
- 音效；
- DPI；
- 字体；
- 可访问性；
- Clock/RNG；
- 纯规则引擎；
- benchmark/soak。

### M4：内容与发行

- 五款游戏的单机舒适功能；
- Sokoban/Zuma 编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 许可证与展示；
- 稳定后再增加新游戏。

---

## 13. 最终判断

本轮修改是成功的。

上一轮提出的关键本地可靠性要求，大多已经从设计落实为代码和测试：

- 真异步；
- 短锁预算；
- per-request spool；
- stable attempt；
- revision；
- strict submission；
- personal-best query；
- app factory；
- optional dependencies；
- unified destructive guard。

现在不需要再次重构成更复杂的平台。最合理的下一步是：

> **先修好 request conflict、真实旧库迁移和 ruleset 隔离，然后把精力转向本机档案、进度、存档、设置、可访问性和桌面发行。**

这些工作都服务于“可靠、舒服、可长期保存的单机小游戏合集”，不需要账号、公网竞技、反作弊或云平台。
