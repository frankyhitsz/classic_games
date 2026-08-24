# 第四次代码审查核对结论

本文逐条核对 `classic_games_fourth_code_review_local_first_taskbook_zh.md`。结论以
当前代码、macOS 本机运行和新增的锁竞争/多进程测试为准。任务书没有 pygame、
Flask 的运行环境，因此其中对游戏回归和窗口行为的判断只能作为静态意见；仓库
现有环境可以执行这些路径，本轮已实际运行。

## F01–F19

| 编号 | 判断 | 处理 |
| --- | --- | --- |
| F01 | 成立 | 新增单线程 `LocalWriteWorker`。本地保存、排行、最近记录和健康检查的异步入口都在 worker 执行，不再先访问 SQLite 再返回 completed Future。数据库持锁时，调用本身的最终固定压力结果 p99 为 0.021 ms，失败记录由 worker 写入 durable spool。当前 SQLite busy budget 为 250 ms，不再等待 5 秒。当前 schema 的正常启动走只读快路径，不争抢写锁；维护和旧记录清理在后台执行。 |
| F02 | 成立 | 待保存项改为版本化 `PendingSaveEnvelope`，严格检查字段集合、类型、request ID、attempt UUID、revision 和 payload hash。每个文件独立解析，坏文件移入 `pending-quarantine/`；一条坏记录不会阻止其他记录恢复或启动。启动器会显示隔离数量。 |
| F03 | 成立 | 不再读改写共享 JSON 数组。每个 request 使用一个不可变 JSON 文件，唯一临时文件写完并 `fsync` 后，通过硬链接或排他创建发布。32 个独立进程并发写入 32 条记录，没有丢失、覆盖或固定 `.tmp` 冲突。项目选择支持多实例，而不是禁止第二实例。 |
| F04 | 成立 | `game_service.mutation.ScoreMutation` 统一完成规范化、JSON 合法性、大小和字段验证；只有规范化成功后才进入 worker/spool/SQLite。`set`、bytes、NaN 等非法 extra 返回稳定 `invalid_extra`，不再向 UI 抛 `TypeError`。HTTP 适配器复用同一个仓储模型。 |
| F05 | 成立 | 每次新游戏由客户端生成稳定 `attempt_uuid`，每次新状态使用递增 revision。2048 里程碑、最终分数、手动重试和 spool 重放使用同一 UUID。低 revision 晚到只返回 `stale_revision/no_op`，不会生成第二条 attempt，也不会覆盖最终分数。 |
| F06 | 成立 | 旧整数 `submission_id` 已严格化：不存在返回 `submission_not_found`/404；game、player、profile、mode、ruleset 或 attempt UUID 不匹配返回 409，不再静默插入。新游戏代码以 attempt UUID 为主，整数 ID 只保留兼容性。 |
| F07 | 成立 | 保存响应的 `rank` 现在是 `personal_best_rank` 的兼容别名，按该 profile 的个人最佳计算。已有高分的玩家提交低分新局时，响应名次与榜单一致。 |
| F08 | 成立 | 榜单用窗口查询挑出每个 profile 的真实最佳 attempt，tie 时间来自该最佳分数的 `score_achieved_at`。同分只更新 extra 不会改变达到分数的时间；分数真正提高时才更新时间。 |
| F09 | 成立 | 外部旧库先检查完整列集合，再逐行验证 game、player、score、extra 和时间。缺列、损坏库和坏行只产生迁移提示，不会让新库不可用，也不会写旧库。迁移 DDL 在显式事务中执行；注入中途失败后，schema 和 version 均回滚。 |
| F10 | 成立，按现有产品边界实现 | schema v2 已把 `profile_id/mode/ruleset_version/status` 接入写入、幂等 hash、榜单、最近记录和统计。当前产品只产生 `classic + 当前规则版本 + completed` 结算；`practice` 可存但不进入默认最佳。没有虚构尚不存在的舒适模式或穿墙模式。 |
| F11 | 成立 | progress、save_slots、settings 没有产品入口，继续建空表会误导维护者。本轮从 schema v2 移除这些空表；等存档、进度或设置有真实 API、迁移和 UI 时再通过新 schema 增加。 |
| F12 | 成立 | spool 文件保存 canonical payload hash。同 request ID、同 hash 是幂等；不同 hash 返回 `request_id_conflict`，保留原文件，不再静默吞掉新负载，也不会把永久冲突留在可重试队列。 |
| F13 | 成立 | 顶层旧 JSON、单项 envelope、corrupt、unsupported、hash mismatch 分别处理。隔离名含纳秒与随机后缀；旧共享文件迁移后保留为唯一 `.migrated-*` 文件。当前 UI 显示恢复提示；“导出隔离数据”按钮属于后续数据管理界面，原文件已经保留，未假装已有 UI。 |
| F14 | 成立 | `BaseGame.request_destructive_action()` 统一保护 QUIT、Esc、返回按钮、R/重置按钮以及推箱子关卡切换。保存中或完全未落盘时第一次操作只进入确认状态，第二次才放弃；durable pending 可以安全返回。2048 和五款游戏的按钮/键盘路径都改用同一入口。 |
| F15 | 成立 | 新增 `StorageStatus` 和 `DataResult`，区分 readable、writable、outbox_writable、错误码、可重试性和恢复提示。本地读错误不再伪装成空榜；Future 以错误完成，启动器和结算页显示“本机记录暂时不可读”。健康检查会做后台读、短写探测和 spool 可写探测。 |
| F16 | 成立 | 新增 `pyproject.toml`：core 只依赖 pygame；`[api]` 才安装 Flask/requests，`[dev]` 提供测试工具。游戏类型依赖中立 `GameDataService` Protocol。启动器只在 `GAMES_USE_HTTP=1` 时导入 HTTP 客户端；阻止 requests 导入的 core 测试已通过。console script 和 wheel 构建也已验证。 |
| F17 | 合理的语义提醒，不是现有规则 Bug | 当前记录的是“每次结算”，不是每次按下开始。中途重开、返回和关闭不计 attempt；README 已明确这一点。完整 in-progress/abandoned 生命周期会改变统计和 UI，本轮没有在缺少产品要求时擅自加入。`completed` 表示成绩已结算，不等同于通关。 |
| F18 | 多项成立，按风险拆分 | 已完成：回执 180 天保留期、唯一备份名、损坏库 WAL/SHM 一并保留、旧行完整规范化、事务迁移和失败回滚测试。未实现：删除历史、导出/导入和数据管理 UI；这些是会删除或覆盖用户数据的产品功能，需要单独设计确认、备份和撤销，不能作为本轮内部修补顺手加入。 |
| F19 | 风险判断成立，复杂鉴权建议不必要 | Flask 默认仍只监听 loopback。README 已明确 `0.0.0.0` 没有身份验证，只能在可信开发网络临时使用并配合防火墙。该适配器不是产品联网服务，因此没有引入账号或 token 系统。 |

## 架构与模块建议

任务书推荐的最小可靠闭环已经落实：

```text
Game / Launcher
  -> GameDataService
  -> LocalWriteWorker
       -> LocalGameStore (schema v2)
       -> pending/<request_id>.json
```

- `ScoreMutation` 是 SQLite、spool 和 HTTP 共用的规范化请求。
- `attempt_uuid + revision` 是游戏会话身份；数据库自增 ID 不再承担这个职责。
- `close()` 等待 worker 完成，返回时每条异步保存已经 commit、durable pending，或明确成为用户确认过的非持久失败。
- 启动时只扫描一次 spool，`failed_save_count()` 读取内存快照，不再每帧解析 JSON。
- 当前 schema 已接入真实使用的 attempts 和 request receipts；没有提前放置未实现的存档/设置表。

没有采纳“立即拆分 launcher、统一全部状态机、缓存所有 Surface”等建议。launcher 重构需要独立验收，现有渲染 p95 仍低于 5 ms；在没有性能证据时改动整个 UI 架构会扩大回归面。2048 的保存状态机仍比 BaseGame 复杂，但已共享 attempt 身份、结果语义和退出保护，后续可在加入存档时再合并。

## 任务清单的取舍

### 已完成的 P0/P1 基础项

- 真正异步的本地 worker、短锁预算和 durable fallback；
- 统一请求模型、版本化 spool、32 进程安全和 payload 冲突；
- stable attempt UUID、revision 合并和严格 submission ID；
- QUIT、键盘、鼠标、重置统一未保存保护及可靠 close；
- 外部/内嵌旧库逐行容错，schema v2 事务迁移；
- personal best rank、最佳 attempt tie 时间和查询维度；
- `StorageStatus`、`DataResult`、`GameDataService` Protocol；
- core/api/dev 可选依赖、console scripts 和 wheel 构建；
- request receipt 清理，移除未使用的空表；
- 锁竞争、坏 spool、跨进程、强制退出和迁移失败测试。

### 作为后续产品或工程任务保留

- progress、save slot、settings 的真实 repository 与 UI；
- 本机家庭档案、继续游戏、数据导出/导入/删除；
- launcher 拆分、结构化日志和恢复管理页；
- 将现有大回归文件迁移为 pytest、覆盖率、CI 和三平台矩阵；
- InputManager、IME、手柄、音频、缩放、字体资源和可访问性；
- 纯规则 Engine、可注入 RNG、Zuma FSM；
- Tetris 7-bag/ghost/hold、2048 撤销/存档、推箱子编辑器等玩法；
- 桌面签名、安装包、自动发布、LICENSE/素材清单和新游戏。

这些建议大多有长期价值，但不是当前五款游戏的已复现缺陷。特别是 LICENSE 需要仓库所有者选择授权条款；删除历史和导入数据会改变用户资料；玩法扩展会改变计分规则。它们不能由本轮审查默认授权。

## 验证证据

- 原有回归：107 PASS，0 失败；
- 第四轮边界套件：21 项，覆盖锁竞争、spool、attempt、迁移、可选依赖和 UI 保护；
- 固定 seed 玩法：20,000 步；
- 32 个进程并发 spool：32/32 文件可解析；
- SQLite 并发：240 次写入，`integrity_check=ok`；
- 强制进程退出：Future 返回 durable pending 后直接 `os._exit`，重启恢复成功；
- 持锁异步提交：调用 p99 0.021 ms，durable fallback 成功；
- 五款游戏渲染 p95：约 2.3–4.7 ms，均低于 16.7 ms；
- 100 次客户端创建/关闭：线程集合回到基线，FD 无持续增长；
- `compileall`、Ruff、shell 语法、wheel 构建和 diff whitespace 检查通过；
- 测试前后仓库旧成绩库主文件指纹保持不变。

具体性能数字会随机器负载波动，验收依据是阈值和固定测试，而不是把某一次小数写成产品保证。
