# 第八次代码审查核对结果

本次核对以 `f51de90` 之后的实际代码、可运行测试和故障注入为准。任务书对并发恢复层的
多数判断成立；其中一项已由现有实现覆盖，两项事实成立但建议的直接做法不安全或需要权利人
决定。优化清单逐项状态见 `eighth-optimization-matrix-zh.md`。

## F01–F26

| 项目 | 判断 | 处理结果 |
| --- | --- | --- |
| F01 state journal compare-and-delete 跨进程 TOCTOU | 成立 | state key 使用稳定 inode 的 OS 文件锁；put、解析、隔离和 compare-and-delete 使用同一把锁。旧 worker 的 hash 不匹配时不能删除新 operation。 |
| F02 2048 临时读取失败被解释为空槽 | 成立 | `SlotLoadResult` 区分 loaded、no-slot、temporary-failure 和 profile-pending。失败继续保持输入/自动写入门禁，提供重试、二次确认新开和返回。 |
| F03 改名后 2048 attempt 不能继续 | 成立 | attempt 身份不再比较历史 `player` 字段，只比较 profile、游戏、mode 和 ruleset；改名后 revision 2 更新原 attempt 的集成用例通过。 |
| F04 state journal 写失败没有最后副本 | 成立 | journal 和 SQLite 同时失败时按 key 保留最新内存 operation，状态为 `NON_DURABLE_PENDING`；退出/重置保护会提示，恢复写入后清除。 |
| F05 profile Future 没有 generation | 成立 | 新增 `ProfileController`。load/save/list Future 都带 generation 与预期 profile；旧结果不能改写新选择。 |
| F06 journal 没冻结 ruleset | 成立 | state envelope v2 显式保存 ruleset，progress/slot 参数同步冻结；v1 journal 读取时按当时兼容规则升级并重写。 |
| F07 offline progress journal 覆盖式写入 | 成立 | journal 层使用 Sokoban/Zuma 各自的 schema 和 merge policy；多实例的解锁、完成集合和最高分不会回退。 |
| F08 state journal 缺目录 fsync | 对审查基线不成立 | 原 `put` 与成功删除在 `os.replace`/`unlink` 后已经调用目录 fsync。此次继续为 state quarantine 与 v1 重写补齐源、目标目录 fsync，但不把既有能力写成新修复。 |
| F09 state quarantine 没通知 | 成立 | 记录隔离数量和恢复通知，启动器可合并显示；隔离目录保留原始字节。 |
| F10 同 key 顺序只依赖 worker 到达时机 | 成立 | operation 在提交 Future 前取得 logical revision；journal 按 revision 和 operation ID 排序，晚执行的旧 setting/slot 不得覆盖新值。 |
| F11 schema fast path 不核对子表主键 | 成立 | current-schema 检查三张状态表的完整 UNIQUE/PK 列顺序，并执行 `foreign_key_check`；畸形 v5 fixture 会备份并重建。 |
| F12 profile 迁移冲突静默丢子状态 | 成立 | settings 选择最新有效 JSON，progress 做规则化 merge，slot 优先 slot revision；被舍弃或损坏的原文进入 `invalid_local_state`。有效值不会因另一侧损坏而丢失。 |
| F13 generic progress merge 没有游戏 schema | 成立 | 增加 Sokoban/Zuma 字段、类型、关卡范围和分数范围校验；未知 key/字段直接拒绝。 |
| F14 改名 attempt identity 冲突 | 成立 | 与 F03 同根，已修复并覆盖 row-ID hint 与 UUID 同时存在的续局路径。 |
| F15 2048 持久化 SQLite row ID | 成立 | slot v3 不再保存 submission ID；v2 的旧整数提示读取后丢弃，以 attempt UUID 为稳定身份。旧 row ID 返回 not-found 时会去掉提示安全重试。 |
| F16 语义坏 2048 slot 未隔离 | 成立 | 规则版本、棋盘、分数、won 和 attempt 元数据校验失败后调用 store quarantine API；UI 不会直接覆盖原槽。 |
| F17 本机状态没有最终事件 | 成立 | 增加 `LocalStateEvent` 队列和按 key 状态查询，覆盖 committed、durable/non-durable pending、permanent failure；2048、progress 和 profile 会消费回写结果。 |
| F18 Profile Future 覆盖新选择 | 成立 | 与 F05 同根；另补启动尚未解析时的 launch token，档案解析后绑定而不是丢点击或使用占位身份。 |
| F19 default profile 多个来源 | 成立 | `ProfileIdentity.default()` 是唯一入口；legacy `guest` 与 `anonymous` 映射同一稳定 UUID，迁移碰撞按显式规则合并。 |
| F20 晚到 progress read 降低 HUD | 成立 | Sokoban/Zuma 的读写都带 progress generation；写入开始后，旧 read 结果不再应用，HUD 先采用本局单调值。 |
| F21 正常 state read 使用写锁 | 成立 | setting/progress/slot 正常读取不再 `BEGIN IMMEDIATE`；只有发现坏原文后才短事务重读并做 compare-before-quarantine。 |
| F22 state 扫描和 count 线性增长 | 成立 | replay 每批最多 128 条并受文件数、单文件和总字节预算限制；日常 count 使用内存计数，跨进程发现时再有界刷新。 |
| F23 request lock 文件残留 | 事实成立，直接清理建议不安全 | 稳定 inode 是跨进程锁协议的一部分。即使清理者拿到锁，也可能已有 waiter 打开旧 inode；unlink 后新进程会锁新 inode，形成 split-brain。因此未按“按时间删除”实现。锁文件很小且不含隐私；若以后清理，必须先升级为带全局目录代际的协议。 |
| F24 committed SaveEvent 淘汰后不能重建 | 成立 | 内存未命中时读取 180 天幂等回执，重建 `COMMITTED` 事件；损坏或过期回执仍返回 unknown。 |
| F25 CI 存在但 branch protection 未闭环 | 部分成立 | workflow 增加 concurrency、timeout，core job 纳入第八轮用例，三平台 job 继续执行完整存储/压力检查。当前环境没有 `gh`，无法可靠读取或修改远端 required checks；没有把代码 workflow 等同于仓库保护设置。 |
| F26 没有 LICENSE | 事实成立，但不能代选 | 许可证会声明代码和视觉素材的授权权利。仓库没有足够来源记录，本次不替权利人选择许可证；这项在正式分发前仍需所有者确认。 |

## 验证结论

新增检查覆盖 state journal v2/CAS/跨进程锁、旧日志升级、双重写失败、typed slot load、
profile generation、默认身份、迁移碰撞、畸形 schema、改名续局和回执重建。完整功能、存储、
迁移、固定 seed 压力、渲染、SQLite 并发和资源循环均由项目脚本执行；最终数字记录在根目录
`task.md`，避免在审查结论中复制易过期的输出。
