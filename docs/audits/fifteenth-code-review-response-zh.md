# 第十五次代码审查答复

本轮以当前代码、macOS 13 本机实测、真实 spawn 子进程和完整测试为准。31 条 Finding 中，四项 P0 均可由
控制流或 Linux `flock(2)` 契约证实；Archive 历史 adapter、覆盖率、类型门禁和桌面发行等建议需要区分
“当前缺陷”和“长期路线”，没有把未实现路线写成修复完成。

| Finding | 判断 | 处理与证据 |
| --- | --- | --- |
| F01 POSIX EX→SH 非原子 | 成立 | POSIX 新增独立 transition gate，所有协作 application acquire 都先过 gate；handoff 后在 SH 下再次扫描 transaction root。spawn 子进程真实等待者证明维护进程不能插入转换窗口。 |
| F02 缺 journal 的 import root 被删除 | 成立 | prepare 使用 `.preparing-*`，完整 journal fsync 后才原子改名 `.import-*`；后者缺 journal 一律 recovery-required，rollback 文件不删除。 |
| F03 state 语义错误绕过 journal | 成立 | 非 retryable StoreError 直接 PERMANENT_FAILURE，结果标记 journal/database unchanged，不调用 SQLite；I/O 与 lock timeout 才允许数据库 fallback。 |
| F04 import planner 静默丢 pending | 成立 | `resolve_operations()` 被 live outbox 与 planner 共用；same order/different hash 报冲突，duplicate/superseded/merged/incoming 写入 preview 统计。 |
| F05 prepared marker 遇 target 缺失被删 | 成立 | target 缺失或损坏时恢复 marker 中 previous；incoming 仍在时保留 marker，其他 winner 不盲删。无需把 `current=None` 猜成 DB committed。 |
| F06 reject 失败无稳定状态 | 成立 | winner 改变发 SUPERSEDED；winner 未变而 filesystem recovery 失败发 RECOVERY_REQUIRED 并停止本轮 replay，避免永久错误热循环。 |
| F07 普通 orphan temp 无协议 | 成立 | score/state/clock temp 经 2 秒 grace 后在 request/key lock 下验证并提升、合并或隔离；新 temp 留在原位并让 complete export 失败；replace 删除未纳入 archive 的 temp。 |
| F08 legacy pending 跟随 symlink | 成立 | 使用 lstat、单链接普通文件检查和 no-follow descriptor 读取；symlink 自身隔离，不读取目标。 |
| F09 DB symlink 分裂 identity | 成立 | backend 初始化首步 canonicalize database；Store、lease、默认 score/state outbox 均使用同一 parent。显式 custom outbox 保留为高级调用边界。 |
| F10 score StoreError 全当冲突 | 成立 | request conflict 稳定拒绝；retryable lock error 可继续 DB fallback；损坏 canonical 先移入 quarantine，再发布有效 mutation。 |
| F11 构造失败依赖 GC 释放 lease | 成立 | outbox/state 初始化异常路径显式 close application session；回归立即取得 inactive lease，不等待 GC。 |
| F12 future reader 拒绝 v3 | 成立 | archive version 3 固定分派 manifest 3 validator，reader 范围与 archive 自身 format 比较，不与未来程序常量比较。 |
| F13 ruleset catalog 必须全等 | 成立 | catalog 允许合法历史子集和未知历史 ID；export 会补入 committed 历史游戏，新增当前游戏不使旧 v3 失效。 |
| F14 历史行用当前 validator | 部分成立 | 当前 ruleset 继续严格验证；历史/未知 ruleset 采用 bounded preserve-only，不按当前结构解释。任务书未提供可证明的旧结构转换规则，因此没有虚构 adapter。 |
| F15 replace 依赖健康目标 DB | 成立 | restore 在旁路构建 fresh DB，检查 schema/foreign key/quick check 后原子替换；健康库用 SQLite rollback，坏库用带 hash raw transaction v3，原 bytes 留作 backup。 |
| F16 v1 恢复未绑定 evidence | 成立 | CLI 要求 `--evidence` 和导出结果的 SHA-256；evidence 必须完整且 transaction/database identity 一致。低层 API 仍只用于受控测试。 |
| F17 root 未分型 | 与 F02 重复且成立 | `.preparing-*` 与 `.import-*` 已分型，status 将前者标为 import preparation。 |
| F18 claim 未绑定 revision/hash | 成立 | 游戏保存 claimed revision 和按 Store 规则计算的 expected hash；立即 ACK 与异步 COMMITTED event 都必须匹配 owner/epoch/revision/value hash。 |
| F19 每档案单 autosave | 不是 Bug，需明确策略 | 选择“一个档案一个 autosave、一个活动窗口”；已有 conflict/takeover UI，README 明确该限制。本轮不加入会改变 slot/attempt 产品模型的多槽。 |
| F20 claim 证据日志 | 合理但部分 | 用户可见状态保留 reload 原因、owner conflict 与接管路径；尚未建立统一 structured recovery logger，不把普通本机日志冒充完成。 |
| F21 practice 放弃 campaign | 成立 | 进入练习保存 level、player、boxes、history、moves/pushes、score/state；累计进度一律二次确认，按 C 或 overlay 返回原棋盘。 |
| F22 progress Future 共用 | 成立 | campaign/practice 各自持有 Future、generation、状态 key 和消息；一个完成不会覆盖另一个。 |
| F23 practice schema 可写 unlock | 成立 | practice validator 只允许 completed/score/moves/pushes；旧 unlock-only 行进入 invalid-local-state evidence。 |
| F24 practice overlay 显示 ranked total | 成立 | practice 的 `self.score` 与 overlay detail 使用练习关成绩；campaign total 保留但不作为主分数显示。 |
| F25 required checks | 成立，外部 | CI 结果不能代替 branch protection；本轮未以 push 授权推断仓库管理授权。 |
| F26 CI 依赖仍漂移 | 成立，部分修复 | pip 固定 26.2.1，core/dev/compat matrix 全部使用 release constraints；三平台 hash lock 仍待独立生成和验证。 |
| F27 coverage 60% | 成立，未冒进 | 新增 27 个 storage 测试并保留 fault/multiprocess 测试；总门槛未在一次协议改动中直接抬到无法满足的 80%。 |
| F28 无静态类型门禁 | 成立，待办 | service/store/archive 的动态 JSON 与 pygame 类型需分阶段标注；本轮未引入未锁定的新工具依赖。 |
| F29 LICENSE | 成立，需权利人 | NOTICE 门禁保留；许可证、AI 辅助政策、名称与素材权利不是代码可以代签的事项。 |
| F30 根目录重复 taskbook | 成立 | 第十五次任务书移入 `docs/audits/`，用户已删除的第十四次根目录副本保持删除；根目录只留项目入口文档。 |
| F31 模块过大 | 成立，未混改 | 本轮只抽出共享 resolver 和 fresh-DB materializer 等有测试的边界；没有在协议修复中同时拆分六个大模块并制造机械 diff。 |

## 两项关键反驳

- F01 的网页结论正确，但“handoff 后扫描”不是唯一安全实现。当前 transition gate 使所有协作请求在
  kernel 非原子转换期间也无法申请 application lock；二次扫描作为远程文件系统的额外防线保留。
- F14 不能仅凭 ruleset 字符串自动发明历史 payload 转换。当前策略是已知 current 严格验证、历史数据
  有界保留且不执行；真正转换 adapter 必须随具体旧 schema fixture 一起加入。
