# 第十七次代码审查逐条答复

核对基线为 `4e723c7`。以下结论来自当前工作区控制流、故障注入和本机测试，不直接采信网页端推演。

## 总体结论

任务书列出的 F01–F30 都指向真实缺口，但部分建议的实现方式需要调整：state recovery 是内部流程，使用
稳定错误码和“只在持锁后隔离”的行为契约比新增一层公共枚举更合适；future timestamp 不应在 parser 中
按当前时间判断，也不应为冻结的 state schema 强加新字段；同步 `publish_slot_intent` 已经 durable，真正
的问题只在异步 fallback。其余建议均按当前架构实现。

## Findings

| ID | 判断 | 处理与证据 |
| --- | --- | --- |
| F01 | 成立 | resolver 在 LWW 前检查 aggregate component ID/hash；被吸收 set replay 返回 duplicate，同 ID 异 hash 返回 conflict。Store 对普通 set 也查询 merge receipt。 |
| F02 | 成立 | v1 upgrade 不再直接调用 merge helper，统一进入 `resolve_operations`；v2/current、orphan 和 import 同用该 resolver。 |
| F03 | 成立 | reject temp 改为唯一名；scanner 在 digest lock、grace、完整 fingerprint 下读取。`list_entries` 二次持锁复验，marker/restore 的 lock timeout 原位保留。没有新增对外枚举，因为这些函数不构成公共 API；稳定 `state_lock_timeout` 与不隔离行为已覆盖区分。 |
| F04 | 成立 | export 发布前执行与 reader 相同的 node/depth/string/byte、manifest 和 hash 检查，并在内存中完成 `_decode_archive` round-trip；结果返回机器可读预算。 |
| F05 | 成立 | terminal journal fsync 后先把 `.import-*` 原子改名为 `.transaction-cleanup-*`，再尽力删除；失败目录进入 status/recovery inventory，但不再算 active。 |
| F06 | 成立 | 2048 每次有效移动保留 pre-move settled snapshot。动画中关闭保存该 snapshot，结算后才保存 merge、score、spawn 和终态；queued direction 在关闭时清空。 |
| F07 | 成立 | score/state SQLite commit 后的 outbox cleanup 捕获 `OSError` 和 `StoreError`；结果仍为 COMMITTED，并附 `cleanup_pending=true`，scanner 后续清理。 |
| F08 | 成立 | parser 仅验证固定绝对范围，不再读取当前 wall clock；新建调用仍拒绝明显超前时间。没有给冻结 schema 增加 `source_updated_at`，logical revision 继续承担 ordering。 |
| F09 | 成立 | state event reducer 按 identity、authoritative receipt、终态优先级和 reconstructed/live 来源归并；同 identity 不再从终态回退 pending。 |
| F10 | 成立 | clock quarantine 返回 bool；失败时停止覆盖。有效 clock temp 遇到损坏 current 时先保存 current，失败则保留 temp 与 current。 |
| F11 | 成立 | backend close 返回结构化 drain 结果。read/write worker 未结束时 application lease 由后台 shutdown 线程继续持有，两个队列真正清空后才释放。 |
| F12 | 成立 | score request lock 改为 256 stripes；status 报告 stripe/legacy/unsafe 数量，`cleanup-score-locks --apply` 只在 application inactive gate 内清除旧锁。 |
| F13 | 成立 | inspect 使用 lexical absolute path，final symlink 由 no-follow reader 拒绝，不再预先 `resolve()`。 |
| F14 | 成立 | `inspect-header`/`inspect-archive` 明确为结构与 hash 检查；新增 `verify-archive`，在临时空 schema 中验证行语义、自然键、外键、pending、evidence 和完整 import plan，不打开用户 DB。 |
| F15 | 成立 | score/state pending 在调用 current parser 前先按 game/ruleset 分类；旧 ruleset 与 removed game 一律 evidence-only。 |
| F16 | 成立 | `classify_import_transaction_root` 由 startup、recovery、status 和 export 共用；合法 terminal root 在四处均不算 active。 |
| F17 | 成立 | Archive v4 分开 active data、active journals、transaction inventory、forensic inventory/content 和 replace eligibility；forensic 缺内容不再否定 active replace。v3 reader 保持兼容。 |
| F18 | 成立 | 大于 8 MiB 的 recovery file 用 no-follow 稳定流式 SHA-256 进入 hash-only inventory；cleanup 两次复核均使用同一流式 hash，不再受 8 MiB reader 限制。 |
| F19 | 成立 | `imported-recovery` 默认只记录 hash inventory，不重新嵌入内容；再次 import 不生成嵌套 evidence tree。 |
| F20 | 成立 | transaction parser 拒绝 NaN/Infinity，限制 depth/nodes/string，完成 open/read/post-fstat 检查，并包装 Unicode、Recursion、Memory 和 JSON 错误。 |
| F21 | 成立 | writer 限制 operation count 与 encoded bytes，写入后立即用正式 reader 自读；成功 publish 的 journal 必须可由当前 reader 打开。 |
| F22 | 成立 | `_bounded_rows` 对 SQLite 动态类型逐列验证，BLOB 等值返回 `invalid_archive_source` 及 table/row/column，不再泄漏 traceback。 |
| F23 | 成立 | BaseGame 新增 `restore_attempt_identity()`，一次同步 context、private UUID 和 revision；Sokoban、2048 共用。 |
| F24 | 成立 | Sokoban 成功恢复后保持 active checkpoint；明确返回 campaign 或正常关闭才发布 tombstone。异步 tombstone 未确认前，旧 active 仍可恢复。 |
| F25 | 成立 | 加载 practice-return 时先检查 outer slot ruleset；旧 ruleset 原样保留、提示但不激活。 |
| F26 | 成立 | validator 检查 attempt 字符集/63-bit revision、score/move/push bounds、exact-int 坐标、ledger 总和与 key、一致 completed set、history 起点和每一步合法 walk/push 可达性。无效 current slot 调用 quarantine 保留原始 JSON。 |
| F27 | 成立，但原表述部分不准确 | LocalBackend 的同步 intent 原本已 durable；现以 `durable_slot_intent` capability 或 receipt 明确确认。异步 save Future 创建只进入等待，完成 ACK 前不允许切换 practice。 |
| F28 | 成立 | 2048 在 current state parser 前先识别旧 ruleset；二次确认后才调用本机 quarantine，原始 JSON 进入 `invalid_local_state`，删除旧 active slot 成功后再新开 current slot。 |
| F29 | 属于优化而非损坏 bug，但合理 | 2048 slot v6 保存 RNG state、move count 和 move digest；读取时校验并恢复随机序列。自定义 RNG 不支持 state 时明确记录 `None`，不会伪称可重放。 |
| F30 | 成立 | BaseGame shutdown 仍保证 SDL 释放，同时记录带 game/attempt identity 的 exception log，并向本机 backend 写 recovery notice。 |

## 未伪装为已完成的外部或长期事项

- main required checks/branch protection 需要 GitHub 仓库设置权限；代码与 CI profile 已准备，但不把设置状态写成完成。
- 三平台 `--require-hashes` 锁、全仓 80%/核心 90% coverage、static type gate 需要独立依赖治理工作；本轮没有用局部测试冒充完成。
- LICENSE、名称、代码生成来源、字体、图形和音效权利必须由仓库所有者确认；不能由代码审查代替权利结论。
- P2/P3 的大型 UI、控制器、音频、编辑器、安装包和玩法模式均在完整矩阵中逐项保留。它们没有被描述为“不属于产品”，只是如实区分本轮已实现、已有基础和仍需继续开发。

完整任务状态见 [seventeenth-optimization-matrix-zh.md](seventeenth-optimization-matrix-zh.md)，测试与两轮复查记录见仓库根目录 `task.md`。
