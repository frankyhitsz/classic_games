# 第九次代码审查核对结果

核对基线为任务书记录的 `2491885`。结论来自本机代码、可控 Future、真实 SQLite、跨实例
journal 和 headless pygame 测试，不直接采信网页端的静态推断。

| 编号 | 判断 | 处理结果 |
| --- | --- | --- |
| F01 | 成立，发布阻断 | schema v6 新增 `state_receipts`；`apply_state_operation` 在同一事务比较 `(logical_revision, operation_id)`、写业务行和 receipt。旧 setting/profile/progress/slot 均无法回写。 |
| F02 | 成立，发布阻断 | `ProfileController` 增加 loading/load-failed/resolved；load 未结束时点击只排队，不 ensure guest。失败后点击重试读取，按 G 才显式选择 guest。 |
| F03 | 成立 | 相同 operation/hash 从 receipt 返回幂等结果，不修改 value version、timestamp 或 last-used；单调 merge 另有 operation receipt，晚到增量只应用一次；同 operation 不同 hash 返回 409 冲突。 |
| F04 | 成立 | state outbox 增加跨进程持久逻辑时钟；系统时间回拨仍产生更大 revision。时钟文件锁和 fsync 在 write worker 执行，不阻塞输入入口。 |
| F05 | 成立 | 新增 `SaveState.SUPERSEDED`；journal 或 DB 淘汰的旧 operation 不再发送 COMMITTED。 |
| F06 | 成立 | superseded 与成功路径按 winner 顺序清除旧 `_non_durable_state` 和 `_unpublished_state`；异常 Future 回调会保留可重试 operation。 |
| F07 | 成立 | `get_local_state_status`、`get_save_status` 只读取内存并安排 read-worker 重建，不再取得文件锁、扫描目录或同步查询 SQLite。 |
| F08 | 成立 | 后台重建同时比较 journal 与 receipt revision；外部较新 pending 可替换旧 committed 快照。 |
| F09 | 成立 | 增加 `read_key` 直接读取语义 key，不再为查一个 key 依赖 128 条批量扫描。 |
| F10 | 成立 | state v1 重写前把原字节 fsync 到独立 migration-backup 目录。 |
| F11 | 成立 | v1 progress 的 `current` key 迁到实际 ruleset key；冲突时继续使用相同顺序/merge 规则。 |
| F12 | 成立 | v1 使用固定 `LEGACY_RULESETS` 兼容表，不再读取升级时可能已经变化的 catalog 默认值。 |
| F13 | 成立 | state outbox 初始化时有界统计历史 quarantine 并恢复提示，后续扫描继续合并通知。 |
| F14 | 成立 | `StorageStatus` 分别报告 `score_outbox_writable` 和 `state_outbox_writable`，保留旧 aggregate 字段兼容调用者。 |
| F15 | 成立 | 2048 要求 `won` 与 max≥2048 一致；playing 死局规范为 gameover；slot revision 限制为 64 位非负整数。 |
| F16 | 成立 | 坏槽先显示“正在保留原始数据”，仅 quarantine Future 返回成功后显示“已隔离”；失败继续禁止覆盖。 |
| F17 | 部分成立，主要风险已修 | profile ensure 与 slot read 改成 Future callback 链，read 不再占 single writer；同 slot 未完成请求合并。Python 无法强杀已进入外部系统调用的线程，但晚到结果没有当前引用时不会应用。 |
| F18 | 成立，未冒充完成 | schema v6 阻止旧 revision 回写，但两个仍活跃的独立进程仍可按最后一次新写竞争同一 autosave。正确解决需要可见的 slot 所有权/接管或多槽交互，不能静默指定赢家。 |
| F19 | 成立 | receipt 缺失/过期时先查权威 attempt；相同 request 重建 receipt，不同 payload 返回 `request_id_conflict`，不落入裸 IntegrityError。 |
| F20 | 成立 | schema v6 给状态版本、时间、profile 形状、key/ruleset 非空增加 CHECK；旧表先语义迁移再重建。 |
| F21 | 部分成立，非当前故障 | fast path 的结构/坏行核对会随历史增长，但查询均为本机 SQLite 且带 `LIMIT 1`；压力测试未显示首帧退化。引入“跳过完整性检查”的 marker 会降低外部损坏自愈能力，需有基准后再做。 |
| F22 | 结论不准确 | HTTP 调试接口已接受显式 `profile_id`；省略时按名字生成稳定 legacy identity 是为了同一玩家的多次成绩归一，而不是所有名字共享 default profile。随机 ID 会把每次提交拆成新玩家。 |
| F23 | 成立但属于仓库设置 | workflow 已覆盖 push/PR 和多平台矩阵；required checks/branch protection 不能由仓库内 YAML 证明。本次推送后核对实际 CI，不伪造设置状态。 |
| F24 | 成立 | gameplay runner 的每个 pygame 子进程可用 parallel coverage；CI 在 gameplay 后 combine，再执行门槛与 XML。 |
| F25 | “混合”等于缺陷的判断不成立 | unittest 负责 repository fixture，自定义 runner 用独立子进程隔离 SDL re-init，stress 负责长循环；三者目的不同且均可单独运行。删除了未使用的 pytest 依赖，未做无收益的语法改写。 |
| F26 | 部分成立 | 清除未使用 pytest；pyproject 是安装范围，environment/requirements 固定 pygame 2.6.1，职责并不冲突。正式发行仍应增加生成式 lock 与依赖审计。 |
| F27 | 成立，需所有者决定 | 仓库确实没有 LICENSE/NOTICE；不能替所有者声明代码、经典游戏名称或素材权利。本轮保留为发布前明确决策，不擅自加入许可证。 |
| F28 | 成立，尚未完成 | 目前已有数据库备份、journal replay、quarantine 和迁移留证，但没有统一导出/导入/恢复页面。该缺口不影响本轮数据一致性修复，仍列为明确待办。 |

## 额外发现

- schema 加 CHECK 后，旧 profile 碰撞迁移会因为“旧表尚不 current”跳过 progress 单调合并；已改为按
  必要列识别可迁移旧表，先合并再重建。
- 持久时钟最初放在提交入口会让文件锁竞争进入输入帧；已移入 write worker，并增加重试不重新
  分配 revision 的约束。
- worker 出现预期外异常时，旧代码可能永久保留 unpublished 计数或丢失 operation；现在通过完成
  回调恢复 durable/non-durable pending 状态。
- 2048 ruleset 不兼容曾与损坏走同一隔离路径；现在只阻止加载，不删除仍可能由兼容版本读取的槽。
- 第二轮复查发现“旧 set 应淘汰”不能直接套到单调 merge：晚到的已完成关卡仍应并入。新增
  `state_merge_receipts` 后，晚到 merge 只应用一次，winner revision 保持不变。
- 状态 getter 虽已去除帧线程 I/O，但每帧都可能重新安排后台查询；现在按 key 以 0.5 秒节流，
  保留跨进程可见性而不制造 60 FPS 的磁盘轮询。
- quarantine Future 失败后原槽仍在，旧按 N 路径却可以覆盖它。现在进入 `quarantine_failed`，
  只允许重试或返回；只有隔离确认成功后才开放二次确认新开。
- Windows coverage runner 上，两个 HTTP 语义用例会偶发超过产品客户端 0.7 秒的快速失败预算。
  正确性用例现使用独立的 5 秒读预算和 UUID 测试身份；产品运行时超时保持不变。CI 失败注解
  同时带上断言上下文，后续平台问题不再只显示用例名。

F18、F21、F23、F26、F27、F28 的未完成边界保留在优化矩阵中；其余成立项均有自动化回归。
