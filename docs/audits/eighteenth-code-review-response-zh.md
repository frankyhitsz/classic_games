# 第十八次审查核对结果

核对日期：2026-09-07。基线：`984cd84`。任务书中的推演与当前实现分别核实；测试数据使用临时目录。
本轮版本为 0.10.0，SQLite schema 8，Archive reader 仍支持 v1–v4，2048 slot v6 不变，推箱子返回点新增 v2 reader/writer。

## 逐条结论

| Finding | 结论与处理 | 代码及验证证据 |
| --- | --- | --- |
| F01 | 成立。merge envelope hash 与 component hash 的确被混用。现在先比较两边 component map 的交集，再判断包含关系或合并。 | `PersistentStateOutbox._component_relation`；v16 raw merge、subset/superset、partial overlap、24 种排列和冲突测试。 |
| F02 | 成立。补齐 set/旧 merge 分支；schema 8 持久 reset frontier 在后续 merge 和重启后仍生效。任意历史 aggregate 跨 reset 的精确拆分尚未解决，不声称混合 set/merge 已有完整代数证明。 | Store/outbox method matrix、reset→merge→restart→old replay；限制见 ADR。 |
| F03 | 成立。final marker 按文件名 digest 先取锁，锁内读、校验文件名与内容、恢复或隔离。损坏文件也不在 BUSY 时隔离。 | `_recover_reject_transactions`；锁内读取断言、原有 BUSY 测试。 |
| F04 | 成立。隔离必须匹配 value hash、outer ruleset、state version；原始内容、删除和持久屏障同事务提交。采用“数据库提交才确认删除”，不新增可离线排队删除日志。 | Store/backend quarantine；CHANGED/ABSENT/QUARANTINED、重启 replay、删除→导出→replace、merge import 删除冲突测试。 |
| F05 | 描述不成立。基线已经在当前 parser 和 owner 判断前分类 outer ruleset。保留该顺序，新增不支持 slot/RNG 格式的保留分支。 | `Game2048._poll_slot_load`；v15 的 version=999 historical 测试。 |
| F06 | 部分描述成立。新增 exporter/upgrade 空库深度验证、v3→v4 升级和原子非覆盖发布。完整 v3 无法 replace 的说法不成立：基线已经接受 `{3, ARCHIVE_VERSION}`。 | `export_data`、`upgrade_archive`、`_verify_archive_object`、`safe_fs.rename_noreplace`；自读、v3 upgrade/replace、发布前后进程退出测试。未实现任意输出目录 temp 的自动回收。 |
| F07 | 成立。finish 保留 ROLLED_BACK，终态禁止反向改写；改名、删除、目录 fsync 的清理失败返回 PENDING，不改变已确认业务结果。 | ImportTransaction、import/replace 的 `business_outcome/cleanup_state/cleanup_path`；cleanup 故障与 rollback phase 测试。 |
| F08 | 成立。preparing 先迁到 cleanup namespace 再递归删除，避免半删 active root。若连改名也失败，保留原目录，不假称已经清理。 | `_retire_preparation`；rmtree PermissionError 定向测试。 |
| F09 | 成立。archive/recovery/status/transaction 使用共享 safe directory/regular predicates，拒绝 Windows reparse/junction。 | `safe_fs.py`；模拟 reparse metadata、Windows 专用 root/child junction 集成测试。完整 descriptor-relative、有统一全树预算的 walker 尚未完成。 |
| F10 | 成立。state lock deadline 不超过剩余扫描预算，睡眠也不跨越剩余时间。 | `_digest_lock`；25 ms 预算对持续争用的检查。该上限约束锁等待，不声称磁盘故障时所有系统调用总耗时可硬限 250 ms。 |
| F11 | 成立。Store 返回胜者 revision/id/payload/value hash；backend 缓存使用语义胜者，操作结果事件保留原请求身份。pending 胜者不冒充 COMMITTED。 | `_emit_local_state_event`；较大 component identity 不占据较小 aggregate winner 的测试。 |
| F12 | 成立。过渡期同时取得 legacy request 和 stripe 锁，路径排序和去重；不再宣称过渡期总锁文件固定 256。 | `_request_locks`；独立 legacy 进程持锁时新 writer 不能进入。 |
| F13 | 成立。证据 parser 增加有界 shape、文件名唯一性、每个 embedded file 的 size/hash 检查以及资源错误处理。 | `_validated_transaction_evidence`；篡改 embedded bytes 即使外层 hash 重算也被拒绝。 |
| F14 | 成立。transaction evidence 复用同一 publisher 和 reparse 策略；不依赖 hard link。 | `export_transaction_data`；禁止 os.link 的测试仍可导出。 |
| F15 | 成立。plan 后重新统计 staged bytes、before images、sidecars，并预留 rollback、用户备份、fresh DB 和余量。 | `_require_import_space`、preview 输出明细。是保守峰值估计，不是 SQLite 页分配的精确预测。 |
| F16 | 成立。增加只读 `preview-replace`，无 apply 的 restore-replace 也返回计划；CLI apply 必须携带指纹，目标数据变化会拒绝执行。 | `_replace_preview_details`；预览后修改设置的拒绝测试。 |
| F17 | 部分成立。增加 active row 的 profile、identifier 长度、version、score/revision、player/status 检查，保留 SQLite 约束与深度验证。 | `_semantic_row_check`、既有 historical/current import tests。未把每表全部规则迁成独立 schema 类；不安全历史行拒绝整次导入而非自动降级为证据。 |
| F18 | 限制属实。v4 catalog 不原地改义；后续按 committed/pending/evidence 分组的 observed ruleset sets 已写入 ADR。 | `eighteenth-protocol-boundaries.md`。未发布 v5。 |
| F19 | 成立。删除未使用的直接 pending/evidence 恢复 helper，恢复只经 planner 与事务。 | 仓库不再包含 `_restore_pending` / `_restore_recovery_evidence`。 |
| F20 | 起点未绑定的说法不成立：基线已检查 history[0] 和空 history 的初始棋盘。新增 playing score=0 不变量，v2 command history 从初始棋盘重新执行。 | `_restore_campaign_session`、`sokoban_history`；初始/非法历史既有测试与 compact round-trip。 |
| F21 | 成立。None、不明确 dict 和 Future 创建不算保存成功；准备练习时阻止移动，ACK 后进入目标关，失败解除等待；同槽排队只保留最新值。 | pending transition、None/exception、cancel queue 测试。真实本机关闭使用同步 durable intent；无该能力的第三方异步 adapter 关闭时仍需它自身提供有序 drain 保证。 |
| F22 | 成立。恢复后的 campaign 移动/撤销异步更新同槽返回点，终局清除，普通新 campaign 不因此自动开始存档。 | `_refresh_resumed_checkpoint`；移动后恢复、撤销后 commands 为空。 |
| F23 | 成立。新增 v2 指令历史、10,000 步与 60 KiB preflight，继续读 v1。10,000 次移动的指令编码约 10 KiB。 | `sokoban_history.py`、10k compact 测试。完整 session 的逐步验证仍在载入阶段，尚未整体移到 worker。 |
| F24 | 风险合理。unknown RNG state version 和 newer slot version 现在明确返回 unsupported 并保留。 | `validate_2048_state` 与游戏加载分支。未新增 RNG algorithm/producer 元数据格式，已知 RNG 格式的损坏仍按损坏处理。 |
| F25 | 风险属实。保留强幂等性，不按时间或概率摘要删除 component。set 安全截断旧组件，status 增加 receipts/barriers 数量。 | schema 8、maintenance、status；完整永久 merge 集合压缩仅记录设计边界，未假称有界。 |
| F26 | 成立。公开 export/upgrade/reader 将 MemoryError 转稳定错误；返回启发式峰值估计。 | `_bounded_memory`、MemoryError 注入测试。v4 未改成流式，不保证阻止系统级 OOM kill。 |
| F27 | “没有 close”不成立。基线 HTTP client 已 shutdown executor 并关闭 Session。统一返回 BackendCloseResult，重复/并发 close 不虚报已完成。 | `client/common/network.py`；10 次创建、异步工作、关闭及关闭后拒绝新任务。 |
| F28 | 成立。remote exposure 打开后所有请求均要求 token，包括 proxy 转来的 loopback。默认不信任 forwarded headers，不提供 reverse proxy 部署支持。 | `server.app`；loopback 无 token=401，正确 token=200。 |
| F29 | 部分成立。统一 close 类型，隔离 receipt TypedDict；新增 mypy 门禁覆盖 service、safe_fs、sokoban_history。 | 本机 mypy 通过，CI/release 增加 typing stage。archive/state/transaction 全字段类型化尚未完成。 |
| F30 | 部分完成。新增跨平台 hash lock、CI require-hashes 安装及两条 Ruff 正确性规则；实测三平台 wheel hash 校验。 | release/core locks、CI、release profile。main 仍未保护；当前无已认证 gh 可设置。90%/80% 覆盖率、LICENSE、权利确认、签名安装包尚未完成。 |

## 验证与边界

首轮完成 318 项 storage（1 项平台跳过）和 107 项 gameplay/API。两轮复查新增了隔离竞态、删除备份
恢复、失败 Future、跨版本进程锁、发布时进程退出、compact history、API token 与生命周期检查。
最终数量与 release/远端 CI 结果以根目录 `task.md` 为准；不将 Windows 专用跳过计作本机实测通过。

任务书 P0–P2 共 162 条 ID，另有 P3 玩法清单。逐项状态见优化矩阵。实现范围和保留的协议限制分别记录，
不把代码重构、数据管理 GUI 或新增玩法模式算成已经完成的 bug 修复。
