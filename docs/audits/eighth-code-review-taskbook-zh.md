# Classic Games Hub 第八次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 当前基线：`main` 分支 commit `f51de90b0d17d4be0725ba439b3873621aa6c32a`（`f51de90`）
> 对比基线：上一轮审查 commit `e75c20656e851a6bd101691a1e1733e2b73ad4bb`
> 产品定位：**本地运行、单机为主、默认不联网的经典小游戏合集**
> 审查范围：五款游戏、启动器、共享 UI、本地服务、SQLite 迁移、成绩与本机状态日志、可选 Flask、测试、CI 和仓库治理

---

## 0. 执行摘要

本轮修复总体是成功的。上一轮三个发布阻断问题已经得到有针对性的实现：

- Windows/POSIX request lock 不再使用 PID 探测；
- 旧 score spool schema 1 可以转换为 schema 2，并把旧显示名档案映射为 UUID；
- SQLite schema 已升至 v5，旧 settings/progress/save_slots 会显式重建和迁移。

同时，以下能力也从规划进入了实际代码：

- `ProfileIdentity` 成为 profile 规则的单一来源；
- profile 启动门禁、档案轮换、新建和改名；
- 本机状态 keyed journal；
- progress 单调合并与 ruleset 分区；
- 2048 载入门禁、attempt 身份恢复和 autosave schema 2；
- 坏 settings/progress/save slot 隔离；
- schema repair 退避；
- GitHub Actions 的三平台及 Python 兼容任务；
- coverage 最低门槛；
- 文档与审查材料移入 `docs/`。

因此，项目目前不需要推倒重构。现有主架构是合理的：

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

但是，本轮新增的 keyed local-state journal、profile 异步选择和 2048 autosave 之间仍存在几项数据一致性缺口。

### 当前最严重的四项问题

1. **state journal 的“检查 hash 后删除”不是跨进程原子操作。**旧 worker 可以在另一个进程刚写入新 autosave/progress 后，把新文件删除。
2. **2048 在 profile ensure 或 slot 读取临时失败时，会把失败解释为“没有存档”。**玩家开始新棋盘后，可能覆盖原本有效的 autosave。
3. **attempt 身份仍把显示名作为组成部分。**档案改名后恢复同一个 2048 attempt，后续 revision 会发生 identity conflict。
4. **state journal 本身无法写入时，没有 score 路径那样的内存待保存与退出保护。**进度、昵称和 autosave 可能在退出时静默丢失。

此外，还有一个明确的 profile 启动竞态：

- `last_profile` 尚未返回时点击游戏，会同时启动一个随机 guest profile 的 ensure；
- 两个 Future 的完成顺序可以使旧结果覆盖新选择，或把本局错误归到新 guest 档案。

本轮结论是：

> **成绩主链路已经较成熟；下一阶段应集中修复 keyed state journal 的跨进程 CAS、profile Future 代际、以及 2048 存档失败语义。**

这些都属于本地可靠性，不需要账号、云端排行、联网竞技、赛季或反作弊。

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的检查

- 锁定当前 `main` 最新 commit；
- 对比上一轮基线与当前 commit 的全部修改；
- 阅读并核对：
  - `game_service/profile.py`
  - `game_service/catalog.py`
  - `game_service/mutation.py`
  - `game_service/service.py`
  - `game_service/local_backend.py`
  - `game_service/store.py`
  - `client/common/ui.py`
  - `client/common/network.py`
  - `client/launcher.py`
  - 五款游戏当前实现
  - `server/app.py`
  - `tests/test_storage_v2.py`
  - `tests/test_storage_v4.py`
  - `tests/test_storage_v5.py`
  - `tests/regression.py`
  - `tests/stress.py`
  - `.github/workflows/ci.yml`
  - `README.md`、`spec.md`、`task.md`、ADR 和工程文件；
- 逐项核对第七轮任务书的修复结果；
- 使用等价文件操作模型实际复现 state journal 的 check-then-unlink 竞争：
  - A 读取旧 hash；
  - B 原子替换为新文件；
  - A 随后 unlink；
  - B 的最新文件被删除；
- 检查 GitHub 当前分支保护与 status 状态。

### 1.2 证据等级

| 标记 | 含义 |
|---|---|
| **修复到位** | 原问题主要路径和关键边界均已闭环 |
| **基本到位** | 原缺陷已关闭，但仍有跨功能或跨平台边界 |
| **部分到位** | 表/API 已有，但失败语义或 UI 尚未闭环 |
| **已复现** | 通过等价可执行模型复现 |
| **代码路径确定** | 从当前控制流可以确定 |
| **高概率竞态** | 调度顺序满足时会发生，需要并发测试固定重现 |
| **待真机验证** | 需要 Windows/macOS/Linux 实机证明 |
| **产品任务** | 不是当前 Bug，而是符合单机定位的完善方向 |

### 1.3 环境限制

当前执行环境没有安装 `pygame` 和 `Flask`，且没有取得仓库源码归档用于本地完整运行，因此本次未独立执行：

- pygame headless 回归；
- Flask 测试；
- storage unittest 全套；
- stress；
- wheel smoke；
- compileall。

仓库 `task.md` 报告：

- 107 项功能检查通过；
- 76 项存储、升级和生命周期用例通过；
- 固定 seed 20,000 步；
- 240 次并发 SQLite 写入；
- coverage 62%；
- Ruff、编译、构建和数据库指纹检查通过。

这些属于项目自测证据，本报告不把它们表述为本次环境的独立复现结果。

截至本次审查：

- 仓库已经包含 CI workflow；
- 当前 `main` 没有启用 required status checks；
- 连接器没有返回当前 commit 的完成状态记录。

因此正式发布前仍应查看 GitHub Actions 实际运行结果。

---

## 2. 第七轮问题修复验收矩阵

| 第七轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| Windows 使用 `os.kill(pid, 0)` | **修复到位** | Windows 改用 `msvcrt.locking`，POSIX 使用 `flock` |
| request lock metadata 前崩溃 | **修复到位** | 锁由内核持有，不再依赖 PID JSON |
| e99 score spool 被隔离 | **修复到位** | schema 1 旧 hash 校验、profile UUID 转换、schema 2 重写和原件备份 |
| schema v2 不能直升 | **修复到位** | schema v5 显式重建旧状态表，现有测试覆盖 v2 和带 attempt 的 v2 |
| profile 未解析即可启动 | **主要路径到位** | 增加 `profile_ready` 和 queued launch；仍有多个 profile Future 的代际竞态 |
| profile 规则散落 | **修复到位** | `ProfileIdentity` 统一 Unicode、UUID 和旧显示名映射 |
| standalone/import orphan | **基本到位** | score 写入与 legacy 导入会建 profile；本机状态使用 FK |
| 改名后榜单仍显示旧名 | **修复到位** | leaderboard/recent join `profiles.display_name` |
| progress 可回退 | **DB 层到位** | `merge_progress` 单调合并；journal 并发与 schema 校验仍有缺口 |
| 非成绩状态不持久 | **基本到位** | keyed journal 已实现；journal 写失败与跨进程 CAS 未闭环 |
| 2048 载入前接受输入 | **修复到位** | loading 状态屏蔽游戏输入 |
| 2048 不恢复 attempt | **修复到位** | slot v2 保存 attempt UUID/revision/确认状态 |
| 2048 terminal slot | **基本到位** | gameover 不恢复；临时 load failure 仍会被当作“无存档” |
| slot 校验不足 | **明显改善** | 棋盘、分数、状态和 attempt 字段均校验；语义坏 slot 未统一隔离 |
| shared pending 非全成功迁移 | **修复到位** | transient failure 时保留源文件 |
| schema repair 重试错误 | **修复到位** | `RECOVERY_REQUIRED` + 60 秒退避 |
| 时钟回拨删除 pending | **修复到位** | 时间被规范并返回 `clock_adjusted` |
| 状态表无 FK/坏值恢复 | **修复到位** | profile FK、版本字段和 `invalid_local_state` |
| profile UI | **部分到位** | 新建、轮换、改名；尚无明确列表、删除、合并和导出 |
| HTTP 能力不等价 | **按产品边界处理** | README 明确 HTTP 仅为成绩 API 调试 |
| CI 未闭环 | **部分到位** | workflow、coverage 和兼容任务已加入；required checks 仍关闭 |
| 根目录审查材料 | **明显改善** | 多数移入 `docs/`；`spec.md`、`task.md` 仍在根目录 |

---

## 3. 当前发布阻断问题

## 3.1 CG8-F01：state journal 的 compare-and-delete 存在跨进程 TOCTOU

- **优先级**：P0
- **证据**：已用等价文件模型复现
- **位置**：
  - `PersistentStateOutbox.put`
  - `PersistentStateOutbox.remove_if_current`
  - `LocalBackendClient._durable_state_write`
  - `LocalBackendClient._replay_state_entries`

当前 `put()` 使用：

```text
写唯一 temp
→ os.replace(target)
```

这是完整文件的原子替换。

但 `remove_if_current()` 使用：

```text
读取 target
→ 比较 payload_hash
→ unlink target
```

两步只受当前对象的 `threading.RLock` 保护，不受跨进程锁保护。

### 确定性竞争

```text
进程 A：读取 target，确认 hash=A
进程 B：os.replace，新文件 hash=B
进程 A：unlink(target)
结果：B 的最新 autosave/progress 被删除
```

这会影响：

- 2048 最新自动存档；
- Sokoban/Zuma 最新进度；
- 昵称修改；
- 设置。

最危险的情况是：

1. A 的旧状态已经成功写入 DB；
2. B 的新状态因数据库锁只写入 journal；
3. A 删除 B 的 journal；
4. B 返回过 `durable_pending=True`，但实际文件已被 A 删除。

### 修复要求

- 为 state key 使用与 score request 同等级的跨进程文件锁；
- `put`、`remove_if_current` 和 quarantine 都在同一 key lock 内完成；
- compare 与 unlink 之间不得释放锁；
- put 在锁内比较：
  - payload hash；
  - logical revision；
  - updated_at/generation；
- 旧 operation 不得覆盖或删除新 operation；
- 增加两个进程同 key 的确定性 barrier 测试。

---

## 3.2 CG8-F02：2048 临时存档读取失败会被解释为“没有存档”

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `LocalBackendClient.ensure_profile_and_load_slot_async`
  - `Game2048._poll_slot_load`
  - `Game2048._save_autosave_slot`

当前流程：

```python
ensure_profile
if result["ok"] is False:
    return None
load_slot
```

若数据库临时锁定：

- profile ensure 可以返回 `ok=False, durable_pending=True`；
- `ensure_profile_and_load_slot_async()` 返回 `None`；
- 2048 把 `None` 当作“没有存档”；
- `slot_load_state` 变为 ready；
- 玩家开始新棋盘；
- 首次有效移动写入新 autosave；
- 原有合法 autosave 可能被覆盖。

### 正确语义必须区分

```text
NO_SLOT
LOADED
TEMPORARY_LOAD_FAILURE
CORRUPT_SLOT
PROFILE_PENDING
```

### 修复要求

- profile ensure 失败不能等价于 no slot；
- 读取临时失败时保持 load gate；
- UI 提供：
  - 重试；
  - 明确“开始新游戏并覆盖旧存档”；
  - 返回菜单；
- 未经用户确认不得写新 autosave；
- 将“profile ensure”和“load existing slot”拆成可组合但独立的结果；
- 增加真实 SQLite lock 下已有 slot 的测试。

---

## 3.3 CG8-F03：档案改名后，恢复中的 2048 attempt 无法继续更新

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`LocalGameStore._identity_matches`

当前 attempt 身份比较：

```text
game_id
player
profile_id
mode
ruleset_version
```

但项目 ADR 已经规定：

```text
profile_id 是身份
display_name 可修改
attempts.player 是结算时快照
```

### 复现场景

1. 玩家名为“旧名”；
2. 达成 2048，attempt revision 1 写入；
3. autosave 保存同一 attempt UUID；
4. 玩家在 launcher 改名为“新名”；
5. 恢复 autosave；
6. 最终 gameover 提交 revision 2，player=“新名”；
7. store 中原 row.player=“旧名”；
8. `_identity_matches` 返回 false；
9. `attempt_identity_conflict`，最终分不能更新。

### 修复要求

attempt 身份只比较：

```text
attempt_uuid
profile_id
game_id
mode
ruleset_version
```

`player` 不应参与身份。

可选规则：

- attempts.player 保留最初结算时名字；
- 或单独增加 `player_at_start`；
- 排行继续 join 当前 profile display name。

---

## 3.4 CG8-F04：state journal 无法写入时，没有最后一份可恢复数据

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `_durable_state_write`
  - `pending_saves_are_durable`
  - `failed_save_count`
  - 2048/Sokoban/Zuma 调用方

若 `state_outbox.put()` 因：

- ENOSPC；
- 只读目录；
- 权限；
- I/O 错误；

失败，当前代码直接返回错误：

```text
不尝试 DB
不加入内存 non-durable state queue
不增加 pending_state_count
不进入退出保护
```

### 影响

- 2048 显示一次 autosave 错误，但仍可直接退出；
- Sokoban/Zuma 基本忽略 progress Future；
- launcher 的退出保护可能认为所有数据都 durable；
- 昵称、进度和最新棋盘在退出时消失。

### 修复要求

建立：

```text
_non_durable_state[key] = latest operation
```

策略：

1. journal 写失败后仍尝试 DB；
2. DB 也失败则保留内存 latest operation；
3. `pending_saves_are_durable` 同时考虑 score 与 local state；
4. `failed_save_count` 包含 non-durable state；
5. 退出、重置和程序关闭提示；
6. 用户释放空间后可重试；
7. non-durable 采用 last-write-wins/monotonic merge。

---

## 3.5 CG8-F05：profile 启动与切换 Future 缺少 generation

- **优先级**：P0/P1
- **证据**：高概率竞态，控制流明确
- **位置**：`client/launcher.py`

### 初次启动竞态

1. `last_profile_async()` 仍在进行；
2. 用户点击游戏；
3. launcher 设置 `pending_launch_gid`；
4. 同时调用 `ensure_profile("guest", random_profile_id)`；
5. `last_profile` 和 guest ensure 可任意顺序完成；
6. 后完成的 Future 会直接覆盖 `profile_id`；
7. queued launch 可能使用随机 guest，而不是原 last profile。

### 切换/改名竞态

launcher 只有一个 `profile_save_future`：

- 用户快速切换档案或改名；
- 旧 Future 晚到；
- `poll_network()` 直接应用旧 result；
- 当前选择被旧操作覆盖。

`pending_launch_gid` 也没有绑定预期 profile UUID。

### 修复要求

- 增加 `profile_generation`；
- 每个 Future 记录：
  - generation；
  - expected profile ID；
  - operation type；
- 旧 generation 结果只记录日志，不修改当前 UI；
- queued launch 保存 `(game_id, profile_id, generation)`；
- 初次 last_profile 未完成时，只排队，不创建 guest；
- 只有 last_profile 明确返回 None 后才创建默认档案；
- profiles list Future 同样使用 generation。

---

## 4. 高优先级残留与新增问题

## 4.1 CG8-F06：state journal 中没有冻结 ruleset version

- **优先级**：P1
- **位置**：
  - `merge_progress_async`
  - `set_progress_async`
  - `save_slot_async`
  - `PersistentStateOutbox`

当前 progress journal 在未显式传 ruleset 时保存：

```text
key = progress:...:current:...
args.ruleset_version = None
```

重放时 store 再解析“当前规则版本”。

save slot journal甚至没有 ruleset 参数，重放时直接取当前 catalog。

### 影响

规则升级前产生的 pending：

- 可被写入新 ruleset；
- 旧 2048 棋盘可能被标为新规则存档；
- 旧 progress 可能解锁新规则关卡。

### 修复要求

- journal 生成时解析并冻结 ruleset；
- state journal schema 升级；
- operation payload 显式包含：
  - ruleset version；
  - state schema；
  - logical revision；
- journal key 使用实际 ruleset，不使用 `"current"`；
- 规则升级后旧 pending 按旧版本恢复或隔离。

---

## 4.2 CG8-F07：offline progress 的 journal 仍是覆盖式，不是单调 merge

- **优先级**：P1
- **位置**：`PersistentStateOutbox.put`

DB 的 `merge_progress()` 会：

- 数值取最大；
- 布尔取或；
- 列表取并集；
- 分数字典逐项取最大。

但 journal 对同一 key 只是：

```text
last os.replace wins
```

### 多进程场景

1. A 在锁库时写入已解锁第 10 关；
2. B 尚未读取 A 的进度，写入第 2 关；
3. B 的 journal 替换 A；
4. DB 恢复时只看到第 2 关；
5. 单调语义在到达 DB 前已经丢失。

### 修复要求

- `merge_progress` journal 自身也执行 schema-aware monotonic merge；
- 在跨进程 key lock 内：
  - 读取现有 journal；
  - 验证同 profile/game/ruleset/key；
  - 合并；
  - 原子替换；
- 不得把 generic latest-value 用于 progress。

---

## 4.3 CG8-F08：state journal 缺少目录 fsync

- **优先级**：P1
- **位置**：`PersistentStateOutbox.put`

temp 文件本身已经 fsync，但 `os.replace()` 后没有像 score spool 一样 fsync 父目录。

因此“durable pending”更接近：

```text
对进程崩溃较可靠
```

而不是：

```text
对断电后目录项持久有完整保证
```

### 修复要求

- POSIX replace 后 fsync 目录；
- remove/quarantine 后同样 fsync；
- Windows 文档说明 FlushFileBuffers/rename 保证边界；
- 不夸大断电级保证。

---

## 4.4 CG8-F09：state journal quarantine 没有恢复通知

- **优先级**：P1

坏 state journal 文件会移动到：

```text
pending-state-quarantine
```

但没有：

- quarantine count；
- recovery notice；
- UI；
- 导出入口；
- 总大小告警。

用户可能只发现进度或存档消失。

---

## 4.5 CG8-F10：state journal 同一 key 的新旧顺序只依赖写入时机

- **优先级**：P1

operation 有 `updated_at`，但 `put()` 不比较它。

较早生成、较晚到达的操作可以覆盖较新操作。

### 修复要求

- 增加 monotonic `state_revision`；
- 每 profile/key 在 DB 和 journal 中比较 revision；
- updated_at 只用于显示，不用于唯一顺序；
- 跨进程 operation ID/UUID。

---

## 4.6 CG8-F11：schema fast path 没核对子表主键/唯一约束

- **优先级**：P1
- **位置**：`_schema_is_current`

当前检查：

- 列；
- profile FK。

但未核对：

```text
settings PRIMARY KEY(profile_id,key)
progress PRIMARY KEY(profile_id,game_id,ruleset_version,key)
save_slots PRIMARY KEY(profile_id,game_id,slot_id)
```

一个列和 FK 都正确但缺 UNIQUE 的数据库会被判 current，随后：

```sql
ON CONFLICT(...) DO UPDATE
```

在运行时失败。

### 修复要求

- `PRAGMA index_list/index_info` 核对三个主键；
- `foreign_key_check`;
- 同版本修复前备份；
- malformed v5 fixture。

---

## 4.7 CG8-F12：profile 迁移冲突可能静默丢子状态

- **优先级**：P1
- **位置**：`_migrate_profiles`

当前使用：

```sql
UPDATE OR IGNORE child_table SET profile_id=new
DELETE old_profile_rows
```

若两个旧 profile 映射到同一规范 UUID，并拥有相同 setting/progress/slot key：

- 一个 UPDATE 被 IGNORE；
- 随后的 DELETE 删除未迁移数据；
- 没有 merge 或 quarantine。

### 修复要求

- settings：按 updated_at 取新；
- progress：执行单调 merge；
- slots：按 updated_at/revision 取新；
- losing row 进入 `invalid_local_state` 或迁移冲突表；
- migration report 显示冲突数。

---

## 4.8 CG8-F13：generic progress merge 缺少游戏 schema

- **优先级**：P1
- **位置**：`_merge_monotonic`

若 existing 和 incoming 类型不同，generic merge 通常直接接受 incoming。

一个调用方 Bug 可以把：

```text
completed_levels: [0,1,2]
```

变成：

```text
completed_levels: "broken"
```

### 修复要求

为各游戏定义 progress schema：

#### Sokoban

```text
unlocked_level: int 1..len(levels)
completed_levels: list[int]
level_scores: dict[str,int]
best_moves/best_pushes
```

#### Zuma

```text
unlocked_level: int 1..len(levels)
highest_score: int
completed_all: bool
```

先验证，再 merge。

---

## 4.9 CG8-F14：改名后的 attempt identity 冲突

- **优先级**：P1/P0
- **说明**：见 F03。

除 2048 外，未来任何跨启动 continuation 都会受影响。应在本轮优先修复。

---

## 4.10 CG8-F15：2048 不应持久化数据库整数 row ID

- **优先级**：P1

slot 保存 `submission_id`。

但稳定身份已经是 `attempt_uuid`。数据库：

- 恢复；
- 导入；
- 行隔离；
- 重建；

都可能改变或删除 row ID。

### 修复要求

- slot 不再把 submission ID 作为必要身份；
- 恢复后按 attempt UUID 更新；
- 旧 slot 中的 ID 仅作可选 hint；
- 404 时自动移除 hint 并按 UUID 重试。

---

## 4.11 CG8-F16：语义损坏的 2048 slot 不进入 quarantine

- **优先级**：P1

store 只隔离非法 JSON。

如果 JSON 合法，但棋盘：

- tile 值非法；
- won 与最大块矛盾；
- attempt metadata 非法；

游戏仅设置 `slot_load_state="failed"`。

原记录没有：

- 隔离；
- 删除；
- 用户提示；
- 导出。

下一次新移动会覆盖它。

### 修复要求

- game 返回 typed semantic validation failure；
- store 提供 `quarantine_slot`;
- UI 提示“存档损坏，已保留副本”；
- 用户确认后开始新局。

---

## 4.12 CG8-F17：本机状态最终保存结果没有统一事件

- **优先级**：P1

score 有 `SaveEvent`。

profile/progress/save slot 没有等价事件。

因此：

- 2048 `slot_save_error` 在 journal 后台成功后仍可能保留；
- launcher 不知道 durable profile 最终已写入；
- progress HUD 不知道补写是否完成。

### 修复要求

增加：

```text
LocalStateEvent
key
kind
state
payload_hash/revision
result
```

---

## 4.13 CG8-F18：Profile Future 结果可能覆盖较新选择

- **优先级**：P1
- **说明**：见 F05。

需要 generation 和 expected profile ID。

---

## 4.14 CG8-F19：default profile 语义仍有多个来源

- **优先级**：P1/P2

当前可能存在：

- launcher 新建的随机 guest；
- standalone `anonymous` 的确定性 UUID；
- legacy guest 的确定性 UUID。

建议定义一个 canonical default profile，并给出迁移/合并策略。

---

## 4.15 CG8-F20：progress read 可以晚于 progress write，HUD 回退

- **优先级**：P2

Sokoban/Zuma 的初始进度 read 和游戏内 write 使用不同 worker。

旧 read 晚到时，可能把内存 HUD 更新为旧值。DB 仍正确，但：

- unlocked HUD 变低；
- K 最高关入口变低；
- 本次进程体验不一致。

### 修复要求

- progress generation；
- write 成功后刷新；
- read result 只在没有更新过的 generation 生效。

---

## 4.16 CG8-F21：正常 state read 使用写锁

- **优先级**：P2
- **位置**：
  - `get_setting`
  - `get_progress`
  - `load_slot`

它们使用 `BEGIN IMMEDIATE`，即使正常读取也取得写锁，只为了损坏时能隔离。

### 改进

- 首先普通 read transaction；
- 只有 parse 失败时再启动短写事务隔离；
- 减少 profile/progress/autosave 间锁竞争。

---

## 4.17 CG8-F22：state journal 扫描和 count 可随文件数线性增长

- **优先级**：P2

- `count()` 每次遍历目录；
- `_durable_state_write` 成功/失败后反复调用 count；
- `list_entries()` 可以一次解析多达 10,000×64 KiB。

建议：

- 内存计数由 put/remove 更新；
- 后台校准；
- 分批读取；
- 总大小上限；
- maintenance budget。

---

## 4.18 CG8-F23：request lock 文件长期残留

- **优先级**：P2

当前稳定 inode 锁文件不会删除。

在 fallback 路径中可能逐 request 留下大量隐藏文件。

建议安全清理：

- 对应 target 不存在；
- 无进程持锁；
- 超过保留期；
- 分批 cleanup。

---

## 4.19 CG8-F24：SaveEvent 的 committed 状态被内存淘汰后不能重建

- **优先级**：P2

`_save_status` 最多 1024 条。

pending 状态可从内存 pending 重建；committed 被淘汰后 `get_save_status()` 返回 None。

对于当前短局通常不明显，但统一 SaveController 时应从 receipt/attempt 查询或使用 session 事件确认。

---

## 4.20 CG8-F25：CI 文件存在，但仓库保护未启用

- **优先级**：P1/P2

当前 workflow 包含：

- core-only；
- 三平台 Python 3.11；
- Linux 3.12/3.13；
- Ruff；
- storage+stress coverage；
- 60% threshold；
- regression。

但：

- `main` 仍未保护；
- required status checks 关闭；
- 当前 commit 未获得本次审查可见的 completed status；
- regression 不计入 coverage；
- 无 mypy/Hypothesis/依赖审计；
- 无 timeout-minutes；
- 无失败截图和 server log artifact；
- 60% 仍低于正式稳定版本目标。

---

## 4.21 CG8-F26：仓库尚无 LICENSE

- **优先级**：P1（正式分发前）

当前已有：

- CHANGELOG；
- CONTRIBUTING；
- SECURITY。

但没有 LICENSE/NOTICE。

许可证需要仓库所有者确认代码、名称和素材权利，不能由自动修复随意选择。

---

## 5. 分模块审查

## 5.1 `game_service/profile.py`

### 优点

- profile UUID 规则集中；
- NFC；
- 控制字符拒绝；
- legacy 显示名 UUIDv5；
- strict 32 hex UUID。

### 建议

- 增加 canonical default profile；
- 显示名 grapheme/长度策略写入 UI 契约；
- profile migration 和 journal 只调用本模块。

## 5.2 `game_service/local_backend.py`

### 优点

- score 与 state 两类 durable journal；
- 真异步；
- read/write executor；
- cross-platform score lock；
- error code classification；
- SaveEvent；
- deferred initialization；
- retry backoff；
- legacy spool migration；
- quarantine。

### 重点问题

- state journal 没有跨进程 CAS；
- state put 缺目录 fsync；
- state put 失败无 non-durable fallback；
- state ruleset 未冻结；
- progress journal不是 monotonic merge；
- state quarantine 无通知；
- state event 缺失；
- profile Future generation 由 launcher 自己管理；
- state scan/count 规模治理不足。

## 5.3 `game_service/store.py`

### 优点

- schema v5；
- v2 状态表显式迁移；
- profile/FK；
- invalid attempts/local state；
- current display name join；
- ruleset progress；
- clock correction；
- receipt maintenance；
- legacy content dedup；
- attempt score policy。

### 重点问题

- identity 仍包含 player；
- state table PK/UNIQUE 未在 fast path 核对；
- profile normalization collision 会丢 child row；
- generic progress merge；
- normal state reads取得写锁；
- state table JSON schema 仅做通用 JSON 校验；
- submission ID 仍被 2048 持久化。

## 5.4 启动器

### 已改善

- profile gate；
- queued launch；
- IME；
- new/switch/rename；
- local data background startup；
- recent 非竞争展示。

### 重点问题

- last-profile/ensure/switch Future 没有 generation；
- 早点击可能创建 random guest；
- queued launch 不绑定 profile generation；
- profile list Future 可覆盖新 cache；
- 档案选择只是轮换，不是明确列表；
- 键盘/手柄不可完整操作；
- `main()` 仍高度集中；
- 本地首页仍以榜单为主。

## 5.5 2048

### 已改善

- load gate；
- slot schema 2；
- attempt/revision 恢复；
- terminal policy；
- board validation；
- durable slot journal；
- score/save controller 大量复用 BaseGame。

### 重点问题

- transient load failure 被当 no slot；
- identity 与改名冲突；
- submission ID 不应持久；
- semantic bad slot 不隔离；
- slot ruleset 未冻结在 journal；
- slot final event 不回写；
- 同一个 `_slot_save_future` 只观察最后一个 Future；
- 仍缺撤销、多槽、明确“继续/新局”入口。

## 5.6 Sokoban

### 已改善

- 重复计分、跳关全通关、0 分通关均已修复；
- progress load；
- campaign/practice 分键；
- monotonic DB merge；
- 解锁 HUD。

### 仍需

- journal monotonic merge；
- progress generation；
- 真正的关卡选择界面；
- 最少移动/推动；
- 撤销使用标记；
- 星级；
- 死锁检测；
- 提示；
- 地图闭合、可达和死角验证；
- XSB 导入和编辑器；
- 固定逻辑窗口。

## 5.7 Zuma

### 已改善

- 多 pending reaction；
- swept collision；
- 计时余量；
- progress load/merge；
- 解锁和高分 HUD。

### 仍需

- journal monotonic merge；
- progress generation；
- reaction FSM；
- 重叠反应属性测试；
- RNG 注入；
- `incoming` deque；
- 训练与选关；
- 色弱符号；
- 原创机制和轨道编辑器。

## 5.8 Tetris

保持先前修复：

- 同义物理键；
- soft drop repeat；
- generation guard；
- 边界；
- 自定义辅助旋转名称。

适合后续：

- 可选 7-bag；
- ghost；
- hold；
- lock delay；
- RNG 注入；
- 规则页；
- 网格缓存。

## 5.9 Snake

保持先前修复：

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
- 棋盘缓存。

## 5.10 optional Flask/HTTP

产品边界正确：

- 默认不需要；
- 仅成绩 API 调试；
- 不应复制 profile/progress/slot 服务。

仍建议：

- Protocol 用 feature capability 表达；
- HTTP client 不假装完整 `GameDataService`；
- API 错误、日志和测试保持即可。

---

## 6. 明确非目标

本项目不需要建设：

- 注册登录；
- 云端账号；
- 公网排行榜；
- 匹配系统；
- 赛季；
- 实时联机；
- 服务端权威判定；
- 反作弊；
- replay 审核；
- 在线商城；
- 强制联网；
- 默认遥测；
- 复杂权限平台。

Flask 继续仅用于：

- 教学；
- 调试；
- 本机 API 示例；
- 成绩查询接口。

---

## 7. 推荐的增量目标架构

现有架构保留，补足以下部分：

```text
Launcher
  └── ProfileController
        ├── generation
        ├── resolved profile
        ├── pending launch binding
        └── profile operation events

Game
  ├── AttemptContext
  ├── AttemptSaveController
  ├── ProgressController
  └── SaveSlotController

LocalDataService
  ├── score journal
  ├── keyed-state journal
  │     ├── per-key OS lock
  │     ├── logical revision
  │     ├── explicit ruleset
  │     └── CAS remove
  ├── state events
  ├── profiles
  ├── progress schemas
  ├── save-slot schemas
  └── settings
```

### 7.1 State operation envelope v2

```text
schema_version
operation_id
kind
semantic_key
profile_id
game_id
mode
ruleset_version
logical_revision
created_at
payload_hash
payload
```

### 7.2 状态写入结果

```text
COMMITTED
DURABLE_PENDING
NON_DURABLE_PENDING
RECOVERY_REQUIRED
QUARANTINED
PERMANENT_INPUT_ERROR
```

### 7.3 Progress policy

不要使用一个完全通用的 merge 算法作为最终规则。

每个游戏定义：

```python
validate_progress(payload)
merge_progress(existing, incoming)
```

---

## 8. 完整优化任务清单

### 优先级定义

- **P0**：可能静默丢失本机数据、把记录写错档案或覆盖有效存档；发布阻断。
- **P1**：数据模型、自恢复、迁移、测试和正式发行基础。
- **P2**：输入、UI、性能、可访问性和维护性。
- **P3**：玩法内容和桌面发行。
- **S/M/L/XL**：相对工作量。

---

## 8.1 P0：本地状态一致性

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG8-P0-01 | state key 跨进程锁 | OS file lock | 同 key 的 put/remove 串行 | L |
| CG8-P0-02 | 原子 compare-and-delete | CAS remove | 新 journal 不能被旧 worker 删除 | M |
| CG8-P0-03 | state journal generation | logical revision | 旧 operation 不能覆盖新 operation | L |
| CG8-P0-04 | state directory fsync | durable publish | POSIX replace 后同步目录 | S |
| CG8-P0-05 | transient slot load typed result | SlotLoadResult | 临时失败不等于无存档 | M |
| CG8-P0-06 | 2048 load retry/start-new UI | load overlay | 未确认前不覆盖旧 slot | L |
| CG8-P0-07 | 移除 player 的 attempt 身份作用 | identity fix | 改名后可更新同 attempt | S |
| CG8-P0-08 | rename+resume 2048 测试 | integration | revision 2 成功写入原 attempt | M |
| CG8-P0-09 | non-durable state queue | memory fallback | journal+DB 都失败仍保留最新 operation | L |
| CG8-P0-10 | 退出保护包含本机状态 | lifecycle guard | slot/progress/profile 未落盘时不静默退出 | M |
| CG8-P0-11 | profile operation generation | ProfileController | 旧 Future 不覆盖新档案 | L |
| CG8-P0-12 | queued launch 绑定 profile | launch token | 游戏只能使用预期 profile 启动 | M |
| CG8-P0-13 | 初次 last-profile 竞态修复 | startup state | 未得到 None 前不创建 guest | M |
| CG8-P0-14 | 冻结 state journal ruleset | envelope v2 | 升级前 pending 不进入新 ruleset | L |
| CG8-P0-15 | P0 并发/故障测试 | test suite | 所有 P0 场景可确定重现 | L |
| CG8-P0-16 | 发布门禁 | required job | 任一 P0 失败禁止发布 | S |

---

## 8.2 P1：数据、迁移与工程基础

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG8-P1-01 | state journal schema v2 | migrator | 旧 journal 可升级 | L |
| CG8-P1-02 | progress journal monotonic merge | per-game merge | 离线多进程进度不回退 | L |
| CG8-P1-03 | setting/save-slot last-write ordering | revision policy | 较旧操作不覆盖新值 | M |
| CG8-P1-04 | state journal quarantine notice | status/UI | 坏状态文件可见 | M |
| CG8-P1-05 | state journal 大小/数量限额 | quotas | 不因海量文件 OOM | M |
| CG8-P1-06 | LocalStateEvent | event queue | 后台补写结果可观察 | L |
| CG8-P1-07 | 2048 slot 状态回写 | UI integration | pending→saved/error 自动更新 | M |
| CG8-P1-08 | progress 写入状态回写 | controller | 失败和恢复可见 | M |
| CG8-P1-09 | profile durable 状态回写 | controller | durable profile 最终成功可解除门禁 | M |
| CG8-P1-10 | profile list Future generation | cache token | stale list 不覆盖新档案 | S |
| CG8-P1-11 | canonical default profile | ADR/migration | guest/anonymous 身份一致 | M |
| CG8-P1-12 | state table PK/UNIQUE 检查 | schema verifier | malformed v5 不误判 current | M |
| CG8-P1-13 | state table foreign_key_check | maintenance | orphan 可发现 | S |
| CG8-P1-14 | profile migration collision merge | migration policy | setting/progress/slot 不静默丢失 | L |
| CG8-P1-15 | Sokoban progress schema | validator | 类型、范围严格 | M |
| CG8-P1-16 | Zuma progress schema | validator | completed/all/high score 合法 | M |
| CG8-P1-17 | progress read/write generation | UI controller | 晚到 read 不降低 HUD | M |
| CG8-P1-18 | progress 写入显式 ruleset | API | journal 与 DB 使用同版本 | S |
| CG8-P1-19 | save-slot 显式 ruleset | API/schema | pending slot 不漂移版本 | M |
| CG8-P1-20 | 2048 去除 durable submission ID | slot v3 | UUID 是唯一稳定身份 | M |
| CG8-P1-21 | stale row-ID fallback | save controller | 404 后按 UUID 安全重试 | M |
| CG8-P1-22 | semantic bad slot quarantine | store API | 原件保留，用户有提示 | M |
| CG8-P1-23 | 2048 terminal slot 完整策略 | slot state machine | gameover 不重复结算 | M |
| CG8-P1-24 | slot load timeout/retry | controller | 读故障不会无限 loading | M |
| CG8-P1-25 | state read 去除正常写锁 | repository | 正常读不使用 BEGIN IMMEDIATE | M |
| CG8-P1-26 | state journal 分批扫描 | bounded scanner | 大目录有预算 | M |
| CG8-P1-27 | 内存 state count | event counter | 每次写不全目录 count | S |
| CG8-P1-28 | request lock 文件清理 | maintenance | 不无限积累隐藏文件 | M |
| CG8-P1-29 | committed status 重建 | receipt lookup | cache 淘汰后仍可查询 | M |
| CG8-P1-30 | profile rename ADR 落地测试 | integration | 榜单/历史名称符合策略 | S |
| CG8-P1-31 | profile 选择明确列表 | profile UI base | 不只循环切换 | L |
| CG8-P1-32 | profile export before delete | backup flow | 删除前可恢复 | L |
| CG8-P1-33 | profile merge policy | migration/UI | 历史归属可解释 | XL |
| CG8-P1-34 | 数据导出/导入 | service | 原子、校验、可回滚 | L |
| CG8-P1-35 | quarantine 导出 | recovery API | 原始问题文件可保存 | M |
| CG8-P1-36 | invalid_local_state 管理 | retention | 不无限增长 | M |
| CG8-P1-37 | 结构化日志 | rotating logs | worker/migration/game 有 traceback | M |
| CG8-P1-38 | 恢复页面 | UI | DB/spool/state quarantine 可查看 | L |
| CG8-P1-39 | HTTP feature capabilities | protocol | 调试后端不假装完整服务 | M |
| CG8-P1-40 | Flask API contract tests | tests | 降级行为明确 | S |
| CG8-P1-41 | pytest 全量迁移 | fixtures/markers | 任一测试可单独运行 | XL |
| CG8-P1-42 | score/state 并发属性测试 | Hypothesis/model | journal 不变量可验证 | L |
| CG8-P1-43 | 2048 状态机属性测试 | property tests | slot/attempt/input 一致 | L |
| CG8-P1-44 | Tetris/Snake/Sokoban/Zuma 属性测试 | property tests | 关键不变量覆盖 | XL |
| CG8-P1-45 | CI required checks | branch settings | main 合并必须通过 | S |
| CG8-P1-46 | 当前 commit CI 验证 | workflow run | 三平台实际通过 | S |
| CG8-P1-47 | coverage 包含 regression | config | 游戏路径进入覆盖率 | M |
| CG8-P1-48 | coverage 阈值提升 | gate | 核心≥90%，全项目≥80% | M |
| CG8-P1-49 | JUnit/日志/截图 artifact | CI | 失败可诊断 | M |
| CG8-P1-50 | timeout-minutes/concurrency | workflow | 死锁不占满 runner | S |
| CG8-P1-51 | 类型检查 | mypy/pyright | service/store/client 边界通过 | M |
| CG8-P1-52 | 依赖审计 | pip-audit/等效 | 已知风险可见 | S |
| CG8-P1-53 | 依赖锁定 | constraints/lock | 发布可复现 | M |
| CG8-P1-54 | pyproject/requirements 一致 | dependency policy | 不双重漂移 | S |
| CG8-P1-55 | LICENSE/NOTICE | legal files | 权利确认后明确许可 | M |
| CG8-P1-56 | 商标/命名与素材审查 | release checklist | 正式分发风险有记录 | M |
| CG8-P1-57 | CHANGELOG/schema/ruleset 治理 | release policy | 兼容变化可追踪 | S |
| CG8-P1-58 | 文档目录整理 | docs structure | spec/task 归档 | S |

---

## 8.3 P2：单机体验、维护性和性能

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG8-P2-01 | ProfileController 独立类 | controller | launcher 不手管多个 Future | L |
| CG8-P2-02 | 本机档案页 | UI | 新建、切换、重命名、查看进度 | L |
| CG8-P2-03 | GameState Enum | state model | 无散落字符串 | L |
| CG8-P2-04 | 统一 AttemptSaveController | common controller | 2048/BaseGame 控制流统一 | XL |
| CG8-P2-05 | ProgressController | common layer | 读取/合并/状态统一 | L |
| CG8-P2-06 | SaveSlotController | common layer | loading/retry/new game 统一 | L |
| CG8-P2-07 | InputManager | action map | 五款游戏统一输入 | L |
| CG8-P2-08 | 完整 IME 文本控件 | widget | 组合、光标、选择、退格稳定 | M |
| CG8-P2-09 | 键位重映射 | settings UI | 冲突检测、恢复默认 | L |
| CG8-P2-10 | 键盘菜单导航 | focus model | 不用鼠标可操作 | L |
| CG8-P2-11 | 手柄支持 | controller | launcher/五款游戏可用 | L |
| CG8-P2-12 | 音频系统 | BGM/SFX | 无设备不崩，音量持久 | L |
| CG8-P2-13 | 逻辑分辨率 | scalable canvas | 常见窗口不裁切 | XL |
| CG8-P2-14 | 高 DPI | DPI handling | 字体/图形清晰 | M |
| CG8-P2-15 | 字体 fallback | licensed fonts | 缺系统 CJK 字体仍可读 | M |
| CG8-P2-16 | 色弱符号 | patterns/shapes | 颜色不是唯一信息 | L |
| CG8-P2-17 | 高对比/降低动态 | accessibility | 脉冲、抖动可关闭 | L |
| CG8-P2-18 | Clock/RNG 注入 | deterministic services | seed+输入可复现 | L |
| CG8-P2-19 | 渐进抽取纯规则 Engine | rules modules | 核心测试无需 SDL | XL |
| CG8-P2-20 | launcher 拆分 | app/state/render/data | main 职责清晰 | L |
| CG8-P2-21 | 本地首页改为进度中心 | dashboard | best/recent/progress/continue | L |
| CG8-P2-22 | 静态 Surface 缓存 | profiler-driven | 有失效策略 | L |
| CG8-P2-23 | Zuma reaction FSM | explicit model | 连锁可属性测试 | L |
| CG8-P2-24 | 可重复 benchmark | CLI | 带 OS/版本/seed | M |
| CG8-P2-25 | 30–60 分钟 soak | stability suite | 线程/FD/内存稳定 | M |
| CG8-P2-26 | 游戏内规则页 | controls/rules/version | ruleset 可查看 | M |
| CG8-P2-27 | 崩溃恢复页 | crash UI | 返回菜单并显示日志 | M |
| CG8-P2-28 | 设置页面 | settings UI | 窗口/音量/按键/辅助项 | L |

---

## 8.4 P3：游戏内容和桌面发行

| ID | 任务 | 交付物 | 验收标准 | 量级 |
|---|---|---|---|---|
| CG8-P3-01 | Tetris 7-bag | comfort mode | 独立 ruleset | M |
| CG8-P3-02 | Tetris ghost/hold/lock delay | comfort features | 规则测试完整 | L |
| CG8-P3-03 | Snake 速度/穿墙/障碍 | local modes | 模式最佳分开 | L |
| CG8-P3-04 | Snake 双人同屏 | local multiplayer | 不依赖网络 | L |
| CG8-P3-05 | 2048 撤销 | undo model | slot/attempt 规则清晰 | L |
| CG8-P3-06 | 2048 多存档槽 | save UI | 可查看、继续、删除 | L |
| CG8-P3-07 | 2048 棋盘尺寸 | modes | ruleset 分离 | M |
| CG8-P3-08 | Sokoban 正式选关 | progress UI | 练习/闯关分开 | L |
| CG8-P3-09 | Sokoban 星级/最佳推动 | metrics | 规则明确 | M |
| CG8-P3-10 | Sokoban 死锁检测/提示 | analysis | 提示可关闭 | XL |
| CG8-P3-11 | Sokoban 编辑器 | XSB import/export | 地图验证和预览 | L |
| CG8-P3-12 | Zuma 训练/选关 | practice mode | 不混入完整通关 | L |
| CG8-P3-13 | Zuma 色弱辅助 | symbols | 球色可独立辨认 | M |
| CG8-P3-14 | Zuma 原创道具/轨道 | content | 每项有确定性测试 | XL |
| CG8-P3-15 | Zuma 轨道编辑器 | path tool | 合法、版本化、可预览 | L |
| CG8-P3-16 | 本机成就 | local achievements | 无账号、无遥测 | L |
| CG8-P3-17 | 离线每日挑战 | date seed | 完全离线 | L |
| CG8-P3-18 | 本地 replay | command log | 用于复盘/调试 | L |
| CG8-P3-19 | 中文/英文 | localization | 长文本布局测试 | L |
| CG8-P3-20 | Windows 桌面包 | installer/portable | 无 Python 可运行 | XL |
| CG8-P3-21 | macOS app bundle | package | 数据目录和关闭 smoke | XL |
| CG8-P3-22 | Linux package | AppImage/等效 | XDG 路径正确 | L |
| CG8-P3-23 | 自动发布 | tagged builds | smoke 通过才发布 | L |
| CG8-P3-24 | 签名/校验和 | release integrity | 下载可验证 | M |
| CG8-P3-25 | 截图/GIF/项目主页 | showcase | README 首屏展示玩法 | M |
| CG8-P3-26 | Issue/PR 模板完善 | community | Bug 含版本/日志/复现 | S |
| CG8-P3-27 | 谨慎新增游戏 | template/contracts | 同时交付规则、数据、输入、测试 | XL |

---

## 9. 必须新增的测试

### 9.1 state journal 跨进程

```text
test_old_remove_cannot_delete_newer_state_file
test_same_key_two_processes_use_one_os_lock
test_older_updated_at_cannot_replace_newer_revision
test_progress_journal_merges_instead_of_last_writer_wins
test_state_replace_fsyncs_parent_directory
test_state_quarantine_is_reported
```

### 9.2 2048 autosave

```text
test_profile_pending_does_not_mean_no_slot
test_locked_slot_load_does_not_overwrite_existing_save
test_user_must_confirm_start_new_after_load_failure
test_rename_then_resume_updates_same_attempt
test_stale_submission_id_falls_back_to_attempt_uuid
test_semantically_invalid_slot_is_quarantined
test_slot_journal_freezes_ruleset_version
test_slot_pending_success_clears_ui_error
```

### 9.3 profile

```text
test_early_card_click_waits_for_last_profile
test_guest_ensure_cannot_race_last_profile_result
test_stale_profile_future_cannot_change_current_profile
test_queued_launch_is_bound_to_expected_profile
test_stale_profiles_list_cannot_hide_new_profile
test_default_profile_identity_is_canonical
```

### 9.4 non-durable local state

```text
test_state_journal_enospc_keeps_in_memory_latest_value
test_state_journal_failure_still_attempts_database_write
test_exit_guard_detects_non_durable_autosave
test_progress_future_failure_is_visible
test_free_space_then_retry_commits_local_state
```

### 9.5 schema/migration

```text
test_v5_missing_settings_primary_key_is_repaired
test_v5_missing_progress_unique_is_repaired
test_profile_normalization_collision_merges_child_state
test_state_journal_schema1_ruleset_is_migrated
test_legacy_slot_does_not_enter_new_ruleset
```

### 9.6 游戏属性

#### Tetris

- 方块 generation 不跨代；
- 同义键；
- 边界；
- 自定义旋转规格；
- 未来 7-bag 不变量。

#### Snake

- 不接受 180°；
- 两个合法转向；
- 长停顿；
- 长度与食物不变量。

#### 2048

- 每 tile 每步最多合并一次；
- 总值守恒；
- 新 tile 只增加 2/4；
- 队列不迟到；
- slot/attempt/revision 一致；
- load/new-game 状态机。

#### Sokoban

- 同关不重复；
- practice 不进 campaign；
- 0 分通关；
- progress 单调；
- 地图合法性。

#### Zuma

- 多 pending 不丢；
- reaction 顺序；
- 长帧；
- 临界救场；
- progress 单调。

---

## 10. 性能与稳定性门槛

1. pygame 主线程不得执行：
   - SQLite；
   - score/state spool scan；
   - profile migration；
   - save-slot read/write；
2. `submit_score_async()` p99 ≤2 ms；
3. `retry_failed_saves()` 调用本身 p99 ≤2 ms；
4. profile resolved 前首帧可渲染，但不能错误启动；
5. state journal CAS 在 32 进程同 key 下不丢最新值；
6. 2048 transient load 不覆盖旧 slot；
7. 正常游戏目标 60 FPS，基准机 p95 ≤16.7 ms；
8. 保存、进度和存档不得造成 >50 ms 主线程长帧；
9. 旧 DB + score spool + state journal 升级：
   - 自动恢复；
   - 原件备份；
10. 100 次游戏切换：
    - 线程回到基线；
    - FD 不增长；
    - Surface/内存稳定；
11. 30–60 分钟：
    - pending 最终 commit/recovery/quarantine；
    - 不无限重试；
    - 不持续增长内存；
12. 满盘、只读、坏 DB、坏 slot、坏 progress：
    - 游戏仍可玩；
    - 最后一份数据不被静默删除；
    - 有恢复提示。

---

## 11. 稳定版本质量门禁

正式稳定版本至少满足：

- 全部 P0 关闭；
- state journal 同 key 真正跨进程安全；
- 2048 读取失败不覆盖旧存档；
- 改名后 continuation 不冲突；
- score 与 local-state 都有 non-durable 保护；
- profile Future 有 generation；
- ruleset 在所有 journal 中冻结；
- progress 在 DB 和 journal 两层均单调；
- schema fast path 核对子表主键和 FK；
- 默认无 Flask/HTTP；
- 数据位于用户目录；
- migration/backup/export 完整；
- GitHub Actions 当前 head 三平台实际通过；
- required checks 开启；
- core-only 测试；
- pytest/JUnit/coverage；
- formatter/lint/type/dependency audit；
- LICENSE、CHANGELOG、恢复文档；
- 默认不联网；
- 默认不遥测。

覆盖率不能替代升级 fixture、跨进程竞争、故障注入和真实玩家测试。

---

## 12. 推荐实施顺序

### M0：关闭本地状态数据风险

1. state journal 跨进程 CAS；
2. transient slot load；
3. attempt/player identity；
4. non-durable state fallback；
5. profile generation；
6. ruleset freeze；
7. 对应故障测试。

### M1：完成本地数据产品

- progress schema 和 journal merge；
- state events；
- profile list/rename/export；
- 2048 slot v3；
- data recovery；
- schema fast-path 约束；
- migration collision。

### M2：工程可持续

- pytest；
- 全量 coverage；
- branch protection；
- typing；
- property tests；
- dependency lock；
- LICENSE；
- CI artifacts；
- 文档整理。

### M3：桌面体验

- InputManager；
- 键位和手柄；
- 音频；
- DPI；
- 字体；
- 可访问性；
- launcher 拆分；
- RNG/Clock；
- 纯规则引擎；
- settings UI。

### M4：内容与发行

- 五款游戏单机舒适功能；
- Sokoban/Zuma 编辑器；
- 本机成就；
- 离线挑战/replay；
- 本地化；
- 三平台桌面包；
- 自动发布；
- 基础稳定后再新增游戏。

---

## 13. 最终判断

本轮修复仍然是明显进步：

- 上一轮三个 P0 都有实际实现和测试；
- profile、progress、autosave 和 CI 不再只是任务清单；
- 本地数据恢复边界更完整；
- 产品方向保持在本地单机，没有被可选 HTTP 牵引成联网平台。

当前最主要的风险已经不是“有没有本地存储”，而是：

```text
多个进程同时更新同一个本机状态时，最新值是否真的保住；
存档读取暂时失败时，程序是否会误认为没有存档；
档案改名后，同一游戏运行是否仍是同一 attempt；
journal 自身失败时，退出前是否仍有最后一份副本。
```

推荐下一步：

> **保留现有架构，先修 state journal CAS、2048 load failure、attempt identity 和 profile generation；之后再投入可访问性、玩法完善和桌面发行。**

这条路线完全服务于“可靠、舒服、能长期保存进度的本地经典小游戏合集”，不需要联网竞技平台。
