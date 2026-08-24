# 第九次审查修复规格

## 目标

核对第九次审查提出的状态并发、启动档案、2048 自动存档和工程门禁问题。状态日志与 SQLite
必须共享同一套胜出顺序；读取、重放或进程时钟回拨都不能让旧值覆盖已经提交的新值。

## 范围

- SQLite schema v6 增加 `state_receipts`，状态值与胜出 revision、operation ID、payload hash、
  occurred time 在同一事务提交；单调 progress merge 另存 operation receipt，保证晚到增量只合并一次；
- 跨进程持久逻辑时钟在后台写线程分配，旧 operation 返回 `SUPERSEDED`，重复 operation 为
  不修改业务行的幂等重放；
- 状态查询只读内存快照，journal 和 receipt 重建转移到 read worker，并提供按 key 直接读取；
- state journal v1 升级保留原字节、迁移到规范 ruleset key，并使用固定兼容表而非未来 catalog；
- 启动档案显式区分 loading、load-failed 和 resolved；未解析时只排队游戏，不创建 guest；
  读取失败可重试，或由用户按 G 明确选择 guest；
- 2048 校验 won/最大方块、playing/死局和 slot revision；ruleset 不兼容不当作损坏，隔离成功
  前不显示“已隔离”；profile ensure 与 slot load 使用异步链和同 key 合并，不占用写 worker；
- score receipt 过期后从 attempt 语义重建，相同请求保持幂等，不同 payload 返回稳定冲突；
- setting、progress、slot 增加版本、时间、ruleset 等数据库约束；score/state outbox 分别报告健康；
- gameplay 子进程进入 coverage 汇总，移除未使用的 pytest 依赖。

## 非目标与约束

- 不改变五款游戏的计分、关卡或 ruleset；
- 不增加账号、云同步、联网对战、遥测或广告；
- 不替仓库所有者选择 LICENSE、素材授权声明或商标结论；
- 同一档案的两个独立 2048 进程仍共享 `autosave` 语义键。schema v6 可阻止旧 revision 回写，
  但“两个仍活跃的实例如何选择所有者”需要可见的接管/多槽交互，不能静默猜测；
- GitHub branch protection 是仓库设置。本次验证 workflow 和推送后的实际运行结果，但不把
  本地文件当作 required-check 已开启的证明。

## 关键决策

1. `(logical_revision, operation_id)` 是状态的全局确定顺序。revision 较大者胜出；相同 revision
   用 operation ID 确定顺序；相同 operation 与 hash 是幂等重放，hash 不同是冲突。
2. `apply_state_operation` 在 `BEGIN IMMEDIATE` 内比较 receipt、修改业务表并更新 receipt。旧
   operation 不触碰 value、version、timestamp 或 profile last-used。
   `merge_progress` 是例外：旧快照写入仍淘汰，较旧但尚未应用的单调 merge 会合并一次，且不
   回退胜出 revision。
3. operation 的发生时间由 journal 保存，重放使用该时间；后台持久时钟只决定顺序，不冒充
   业务发生时间。
4. getter 不访问文件或 SQLite。首次无缓存时返回 `None`，后台重建后通过同一状态缓存可见；
   外部较新 pending 可按 revision 覆盖旧 committed 快照。
5. v1 ruleset 兼容映射是版本化常量。升级前写入 migration-backup，规范 key 与旧 key 冲突时
   仍按相同 revision 规则合并。
6. 档案读取失败不是“没有档案”。只有明确 no-profile 才 ensure 默认 guest；错误状态只能重试
   或由用户明确选择 guest。
7. 2048 的坏槽删除是一个需要确认的异步动作。Future 未成功前保持玩法与自动写入门禁。

## 验收标准

- F01–F28 逐项给出成立性、代码证据和未完成边界；132 项优化建议逐项标记；
- setting、profile rename、set-progress 和 slot 的旧 operation 均无法覆盖已提交的新 operation；
- crash-after-commit 重放不增加版本、不刷新时间；receipt 过期的 score 重试不产生第二条 attempt；
- 主线程状态 getter 和状态提交入口不等待 journal 文件锁；时钟回拨后 revision 仍递增；
- v1 原文件有备份并迁入规范 key；历史 state quarantine 重启后仍提示；
- 启动 load 未完成时不 ensure guest，失败后不静默降级；queued launch 绑定最终档案；
- 2048 不恢复矛盾 won 状态或死局 playing 状态，隔离文案等待真实确认；
- 两轮独立复查完成，完整功能、存储、压力、静态检查、编译、coverage 和远端 CI 通过。
