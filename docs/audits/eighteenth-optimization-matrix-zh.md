# 第十八次审查优化矩阵

状态按实际代码和测试记录；“部分”保留尚未达到的验收项。“待实施”不表示建议不合理。
逐 Finding 的理由和反证见 `eighteenth-code-review-response-zh.md`。

## P0

| ID | 任务 | 状态 | 说明 |
| --- | --- | --- | --- |
| CG18-P0-01 | Component map统一表示 | 部分 | 共享 resolver 留在 PersistentStateOutbox，尚未拆为独立模块。 |
| CG18-P0-02 | Component overlap conflict | 完成 | component map 交集比较 hash。 |
| CG18-P0-03 | Component subset duplicate | 完成 | raw merge / subset 重放测试。 |
| CG18-P0-04 | Component superset dominance | 完成 | 同一 map 定义下的 superset dominance。 |
| CG18-P0-05 | Partial overlap union | 完成 | partial overlap 合并，component 按 ID 排序。 |
| CG18-P0-06 | Live/import/migration共享resolver | 完成 | live、import、legacy migration 仍调用同一 resolve_operations。 |
| CG18-P0-07 | Raw merge replay regression | 完成 | v16 raw merge replay 定向测试。 |
| CG18-P0-08 | Aggregate permutation model test | 部分 | 4 个 merge 的 24 种排列；不对混合 set 宣称交换/结合律。 |
| CG18-P0-09 | Store method matrix | 完成 | 补齐旧 merge 不跨 newer set。 |
| CG18-P0-10 | Store component-first处理 | 完成 | component 查重/冲突先于 barrier/order。 |
| CG18-P0-11 | Set replacement frontier | 完成 | schema 8 reset frontier；仍保留历史 aggregate 精确拆分限制。 |
| CG18-P0-12 | Store/outbox differential test | 部分 | 已测双方相同序列 method matrix，未完成全空间模型证明。 |
| CG18-P0-13 | Persisted DB restart test | 完成 | set→merge→重启→旧 merge 不回退。 |
| CG18-P0-14 | Final reject filename digest lock | 完成 | final filename digest 先锁后读。 |
| CG18-P0-15 | Final marker stable reread | 完成 | 锁内重新读取 final bytes。 |
| CG18-P0-16 | Marker identity post-check | 完成 | filename/key/hash 一致性校验。 |
| CG18-P0-17 | Corrupt marker lock-first quarantine | 完成 | 损坏 final 的隔离仍在对应锁内，BUSY 保留。 |
| CG18-P0-18 | Prepared→rejected race test | 部分 | 锁内读取已验证；尚未实现独立进程 phase 切换的全部 interleaving。 |
| CG18-P0-19 | Slot quarantine expected hash | 完成 | expected_value_hash CAS。 |
| CG18-P0-20 | Slot quarantine expected ruleset/version | 完成 | 同时比较 outer ruleset/state version。 |
| CG18-P0-21 | Quarantine result enum | 完成 | SlotQuarantineReceipt 状态与 committed 字段。 |
| CG18-P0-22 | Durable slot tombstone设计 | 替代实现 | 采用 SQLite 原子删除屏障；只有 commit 后确认，不承诺离线排队删除。 |
| CG18-P0-23 | Tombstone journal实现 | 替代实现 | 替代实现为 schema 8 state_barriers；未增加 delete journal。 |
| CG18-P0-24 | Multiwindow quarantine race | 部分 | Store 确定性模拟加载后另一写入，另校验 pending；未穷举真实双窗口调度。 |
| CG18-P0-25 | 2048 outer ruleset classify-first | 完成 | 基线已完成，保留反证测试。 |
| CG18-P0-26 | Historical slot CAS archive | 部分 | 历史存档二次确认、CAS、CHANGED 提示重读；未自动重读。 |
| CG18-P0-27 | Historical active-owner test | 完成 | historical 先于 owner 分支；旧格式现有定向检查。 |
| CG18-P0-28 | Export deep self-verify | 完成 | 发布前临时空 schema 深度验证。 |
| CG18-P0-29 | active_data_complete真实计算 | 完成 | 仅通过深度验证的输出才能发布并声明 active complete。 |
| CG18-P0-30 | Upgrade共享validate/publish | 完成 | upgrade 同 reader、shape、deep verify、publisher。 |
| CG18-P0-31 | v3→v4 upgrade | 完成 | 完整 v3→v4→replace 定向验证。 |
| CG18-P0-32 | v3 incomplete兼容映射 | 部分 | 非 complete 保持不赋权；专用 incomplete v3 组合样本仍需增加。 |
| CG18-P0-33 | AtomicOutputPublisher | 完成 | safe_fs 原子非覆盖改名；不支持时拒绝。 |
| CG18-P0-34 | Archive crash publication test | 完成 | 进程在改名前/后退出：无输出或完整单链接输出。 |
| CG18-P0-35 | Transaction evidence复用publisher | 完成 | evidence 复用 publisher；无 hardlink 环境测试。 |
| CG18-P0-36 | Import committed finalization | 完成 | commit cleanup 失败仍 COMMITTED。 |
| CG18-P0-37 | Rolled-back finalization | 完成 | ROLLED_BACK 不改为 COMPLETED。 |
| CG18-P0-38 | Cleanup result结构化 | 完成 | business_outcome、cleanup_state、cleanup_path。 |
| CG18-P0-39 | Terminal cleanup failure test | 完成 | rename/rmtree 失败与 import 成功回执测试。 |
| CG18-P0-40 | P0 release gate | 完成 | v16 自动纳入 storage discovery 和 release/CI。 |

## P1

| ID | 任务 | 状态 | 说明 |
| --- | --- | --- | --- |
| CG18-P1-01 | Preparing root先rename再删 | 完成 | preparing 先迁 cleanup；改名失败保留原目录。 |
| CG18-P1-02 | Preparing cleanup namespace | 完成 | preparing 先迁 cleanup；改名失败保留原目录。 |
| CG18-P1-03 | SafeTreeWalker | 部分 | 共享 no-follow/reparse predicates；Windows 测试交给 CI，全树 descriptor/budget 抽象未完成。 |
| CG18-P1-04 | Windows junction实机test | 部分 | 共享 no-follow/reparse predicates；Windows 测试交给 CI，全树 descriptor/budget 抽象未完成。 |
| CG18-P1-05 | Recovery lock deadline传播 | 完成 | 恢复锁使用剩余 deadline；不声称能中断阻塞的文件系统调用。 |
| CG18-P1-06 | Nonblocking recovery try-lock | 完成 | 恢复锁使用剩余 deadline；不声称能中断阻塞的文件系统调用。 |
| CG18-P1-07 | Winner identity in Store result | 完成 | Store winner hash/revision/ID 与 backend semantic status、operation event 分离。 |
| CG18-P1-08 | Semantic status与operation event拆分 | 完成 | Store winner hash/revision/ID 与 backend semantic status、operation event 分离。 |
| CG18-P1-09 | Lower authoritative identity test | 完成 | Store winner hash/revision/ID 与 backend semantic status、operation event 分离。 |
| CG18-P1-10 | Dual legacy+stripe lock过渡 | 完成 | 双锁兼容过渡；独立 legacy 持锁进程测试，移除条件写入 ADR。 |
| CG18-P1-11 | Cross-version process fixture | 完成 | 双锁兼容过渡；独立 legacy 持锁进程测试，移除条件写入 ADR。 |
| CG18-P1-12 | Stripe transition移除条件 | 完成 | 双锁兼容过渡；独立 legacy 持锁进程测试，移除条件写入 ADR。 |
| CG18-P1-13 | Transaction evidence shape validator | 完成 | shape、embedded file hash 与 reparse 门禁共享；篡改测试。 |
| CG18-P1-14 | Evidence file entries hash验证 | 完成 | shape、embedded file hash 与 reparse 门禁共享；篡改测试。 |
| CG18-P1-15 | Evidence parser异常包装 | 完成 | shape、embedded file hash 与 reparse 门禁共享；篡改测试。 |
| CG18-P1-16 | Transaction evidence root reparse拒绝 | 完成 | shape、embedded file hash 与 reparse 门禁共享；篡改测试。 |
| CG18-P1-17 | Exact import space estimator | 部分 | plan 后统计 before/staged/sidecar/fresh DB；输出保守空间明细，不称精确页预测。 |
| CG18-P1-18 | Space report UI字段 | 完成 | plan 后统计 before/staged/sidecar/fresh DB；输出保守空间明细，不称精确页预测。 |
| CG18-P1-19 | `preview-replace` | 完成 | preview-replace 与 apply 指纹；目标变化拒绝。 |
| CG18-P1-20 | Apply绑定plan fingerprint | 完成 | preview-replace 与 apply 指纹；目标变化拒绝。 |
| CG18-P1-21 | Archive per-table schemas | 部分 | 增加边界字段校验；未拆为每表 schema 类，不安全历史行拒绝导入而非自动降级。 |
| CG18-P1-22 | Historical unsafe row evidence-only | 部分 | 增加边界字段校验；未拆为每表 schema 类，不安全历史行拒绝导入而非自动降级。 |
| CG18-P1-23 | Archive v4 catalog限制ADR | 完成 | ADR 固定 v4 含义；v5 observed sets 仅设计约束。 |
| CG18-P1-24 | Archive v5 observed ruleset设计 | 完成 | ADR 固定 v4 含义；v5 observed sets 仅设计约束。 |
| CG18-P1-25 | 删除`_restore_pending`旧helper | 完成 | 删除旧直接恢复 helper。 |
| CG18-P1-26 | 删除直接evidence restore helper | 完成 | 删除旧直接恢复 helper。 |
| CG18-P1-27 | Sokoban initial board binding | 完成 | 基线已有 initial binding；保留原有测试。 |
| CG18-P1-28 | Sokoban playing score invariant | 完成 | playing checkpoint 要求 score=0。 |
| CG18-P1-29 | Async slot receipt schema | 完成 | 明确 ACK、pending practice、新值队列、恢复后异步更新；v2 compact commands 与大小预检。 |
| CG18-P1-30 | Pending practice transition state | 完成 | 明确 ACK、pending practice、新值队列、恢复后异步更新；v2 compact commands 与大小预检。 |
| CG18-P1-31 | Active/tombstone newest queue | 完成 | 明确 ACK、pending practice、新值队列、恢复后异步更新；v2 compact commands 与大小预检。 |
| CG18-P1-32 | Stale restored checkpoint策略 | 完成 | 明确 ACK、pending practice、新值队列、恢复后异步更新；v2 compact commands 与大小预检。 |
| CG18-P1-33 | Compact Sokoban undo history | 完成 | 明确 ACK、pending practice、新值队列、恢复后异步更新；v2 compact commands 与大小预检。 |
| CG18-P1-34 | Sokoban encoded-size preflight | 完成 | 明确 ACK、pending practice、新值队列、恢复后异步更新；v2 compact commands 与大小预检。 |
| CG18-P1-35 | Sokoban background validation | 待实施 | 完整 session 验证仍在加载阶段，未整体迁到后台。 |
| CG18-P1-36 | 2048 RNG algorithm metadata | 部分 | unsupported RNG/slot 保留；未发布 algorithm/producer 元数据版本。 |
| CG18-P1-37 | Custom RNG capability contract | 部分 | unsupported RNG/slot 保留；未发布 algorithm/producer 元数据版本。 |
| CG18-P1-38 | Merge receipt compaction ADR | 部分 | set 截断与 status cardinality 已有；永久 merge 集合压缩仅记录正确性边界。 |
| CG18-P1-39 | Merge receipt cardinality status | 完成 | set 截断与 status cardinality 已有；永久 merge 集合压缩仅记录正确性边界。 |
| CG18-P1-40 | Archive MemoryError contract | 部分 | 结构化资源错误和启发式估计；v4 未流式化，v5 仅 ADR。 |
| CG18-P1-41 | Archive memory estimate | 部分 | 结构化资源错误和启发式估计；v4 未流式化，v5 仅 ADR。 |
| CG18-P1-42 | Archive v5 streaming ADR | 完成 | 结构化资源错误和启发式估计；v4 未流式化，v5 仅 ADR。 |
| CG18-P1-43 | HttpBackend `close()` | 完成 | 原已有 HTTP close；本轮统一类型并校验重复创建/关闭。 |
| CG18-P1-44 | HttpBackend lifecycle tests | 完成 | 原已有 HTTP close；本轮统一类型并校验重复创建/关闭。 |
| CG18-P1-45 | Remote exposure all-token mode | 完成 | exposure 模式全请求 token；默认不支持 reverse proxy，不解析不可信 forwarded headers。 |
| CG18-P1-46 | Trusted proxy显式配置 | 替代策略 | exposure 模式全请求 token；默认不支持 reverse proxy，不解析不可信 forwarded headers。 |
| CG18-P1-47 | Typed state operation | 部分 | close 与 quarantine receipt 类型落地；state/archive/transaction 完整 TypedDict 尚未完成。 |
| CG18-P1-48 | Typed slot receipts | 部分 | close 与 quarantine receipt 类型落地；state/archive/transaction 完整 TypedDict 尚未完成。 |
| CG18-P1-49 | Typed Archive v4 manifest | 部分 | close 与 quarantine receipt 类型落地；state/archive/transaction 完整 TypedDict 尚未完成。 |
| CG18-P1-50 | Typed transaction journal | 部分 | close 与 quarantine receipt 类型落地；state/archive/transaction 完整 TypedDict 尚未完成。 |
| CG18-P1-51 | Protocol close返回类型统一 | 完成 | Local/HTTP/Protocol 都返回 BackendCloseResult。 |
| CG18-P1-52 | Main branch protection | 外部未完成 | 远端 main protected=false；本机无已认证 gh，现有连接器无 ruleset 写入口。 |
| CG18-P1-53 | Repository ruleset | 外部未完成 | 远端 main protected=false；本机无已认证 gh，现有连接器无 ruleset 写入口。 |
| CG18-P1-54 | 三平台hash lock | 完成 | release/core wheel hash locks；Windows/Linux 下载校验与 macOS 强制重装，CI 使用 require-hashes。 |
| CG18-P1-55 | Core coverage 90% | 部分 | 已实测覆盖率并保留报告；未达到 90%/80%，不修改排除项凑阈值。 |
| CG18-P1-56 | 全仓coverage渐进80% | 部分 | 已实测覆盖率并保留报告；未达到 90%/80%，不修改排除项凑阈值。 |
| CG18-P1-57 | Pyright/mypy gate | 部分 | mypy 门禁先覆盖 service/safe_fs/sokoban_history，未覆盖全部 archive/store。 |
| CG18-P1-58 | Ruff规则渐进扩展 | 完成 | 增加 B012、B018；全仓检查通过。 |
| CG18-P1-59 | LICENSE权利决定 | 外部未完成 | 需所有者确认贡献权利及许可证选择；未擅自添加 LICENSE。 |
| CG18-P1-60 | AI生成代码来源记录 | 完成 | Git 提交与 audits 可追溯辅助修改；不据此宣称历史权利已确认。 |
| CG18-P1-61 | 名称/字体/图形/音效清单 | 部分 | NOTICE 与权利清单记录无捆绑外部字体/图片/音频；名称及分发权利仍待确认。 |
| CG18-P1-62 | 持久化协议封板ADR | 部分 | ADR 记录版本和未解决边界；未宣称所有协议都已最终封板。 |

## P2

| ID | 任务 | 状态 | 说明 |
| --- | --- | --- | --- |
| CG18-P2-01 | `local_backend.py`拆分score outbox | 待实施 | 生产逻辑局部收口；未完成整模块/包拆分。 |
| CG18-P2-02 | 拆分state outbox/resolver/recovery | 部分 | 生产逻辑局部收口；未完成整模块/包拆分。 |
| CG18-P2-03 | 拆分backend status/workers/lifecycle | 部分 | 生产逻辑局部收口；未完成整模块/包拆分。 |
| CG18-P2-04 | `store.py`拆分repositories | 待实施 | 生产逻辑局部收口；未完成整模块/包拆分。 |
| CG18-P2-05 | `data_cli.py`拆分archive package | 待实施 | 生产逻辑局部收口；未完成整模块/包拆分。 |
| CG18-P2-06 | transaction package化 | 待实施 | 生产逻辑局部收口；未完成整模块/包拆分。 |
| CG18-P2-07 | shared bounded FS/JSON utilities | 部分 | 共享文件类型、原子发布与纯历史编码模块；bounded JSON 仍未统一。 |
| CG18-P2-08 | fault injection registry | 部分 | 有 mock、进程中断和跨进程锁故障测试；无统一 injection registry。 |
| CG18-P2-09 | structured logger | 待实施 | 尚未建立统一结构化 logger。 |
| CG18-P2-10 | machine-readable diagnostics | 部分 | CLI JSON 已覆盖维护结果；GUI 尚未统一消费。 |
| CG18-P2-11 | DataManagementService | 部分 | 已有 GameDataService；尚无独立 DataManagementService。 |
| CG18-P2-12 | 数据状态页 | 待实施 | CLI 能力存在或本轮增强；对应 GUI 页面未实施，不能将命令行算作页面。 |
| CG18-P2-13 | Archive inspect/verify页 | 部分 | CLI 能力存在或本轮增强；对应 GUI 页面未实施，不能将命令行算作页面。 |
| CG18-P2-14 | Export页 | 部分 | CLI 能力存在或本轮增强；对应 GUI 页面未实施，不能将命令行算作页面。 |
| CG18-P2-15 | Import merge preview页 | 部分 | CLI 能力存在或本轮增强；对应 GUI 页面未实施，不能将命令行算作页面。 |
| CG18-P2-16 | Replace preview页 | 部分 | CLI 能力存在或本轮增强；对应 GUI 页面未实施，不能将命令行算作页面。 |
| CG18-P2-17 | Transaction recovery页 | 部分 | CLI 能力存在或本轮增强；对应 GUI 页面未实施，不能将命令行算作页面。 |
| CG18-P2-18 | Recovery cleanup页 | 部分 | CLI 能力存在或本轮增强；对应 GUI 页面未实施，不能将命令行算作页面。 |
| CG18-P2-19 | 打开数据目录 | 待实施 | 尚未增加打开数据目录按钮。 |
| CG18-P2-20 | Export-before-delete | 待实施 | 尚无完整档案删除 GUI 工作流。 |
| CG18-P2-21 | 档案列表页 | 部分 | 现有档案入口/排行榜提供部分基础；独立页面、摘要/迁移工作流未完成。 |
| CG18-P2-22 | 每游戏进度摘要 | 部分 | 现有档案入口/排行榜提供部分基础；独立页面、摘要/迁移工作流未完成。 |
| CG18-P2-23 | 最后游玩和继续游戏 | 部分 | 现有档案入口/排行榜提供部分基础；独立页面、摘要/迁移工作流未完成。 |
| CG18-P2-24 | 单档案导出 | 待实施 | 现有档案入口/排行榜提供部分基础；独立页面、摘要/迁移工作流未完成。 |
| CG18-P2-25 | 删除档案二次确认 | 部分 | 现有档案入口/排行榜提供部分基础；独立页面、摘要/迁移工作流未完成。 |
| CG18-P2-26 | 本地档案merge preview | 部分 | 现有档案入口/排行榜提供部分基础；独立页面、摘要/迁移工作流未完成。 |
| CG18-P2-27 | guest转正式档案 | 待实施 | 现有档案入口/排行榜提供部分基础；独立页面、摘要/迁移工作流未完成。 |
| CG18-P2-28 | 隐私说明 | 完成 | README 与数据 manifest 说明默认本机、离线与数据内容。 |
| CG18-P2-29 | InputManager/action map | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-30 | 键位重映射 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-31 | 全键盘launcher focus | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-32 | 手柄支持 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-33 | 输入设备提示切换 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-34 | IME文本控件 | 部分 | 沿用现有名称输入；完整 IME composition/选择控件未完成。 |
| CG18-P2-35 | AudioManager | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-36 | 无音频设备降级 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-37 | 统一设置页 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-38 | 逻辑分辨率和viewport | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-39 | fullscreen与高DPI | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-40 | CJK fallback | 部分 | 沿用系统 CJK 字体 fallback；无字体环境的替代方案未完成。 |
| CG18-P2-41 | 高对比模式 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-42 | 色弱图案/符号 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-43 | 降低动画 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-44 | 大字号 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-45 | 明确focus ring | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-46 | 闪烁/震动限制 | 待实施 | 没有把当前快捷键或基础显示支持算作统一输入/设置/无障碍实现。 |
| CG18-P2-47 | 统一Clock注入 | 部分 | 2048 有 RNG state 与 move digest；尚未统一五款游戏的 Clock/RNG/command contract。 |
| CG18-P2-48 | 统一RNG接口 | 部分 | 2048 有 RNG state 与 move digest；尚未统一五款游戏的 Clock/RNG/command contract。 |
| CG18-P2-49 | RNG state capability | 部分 | 2048 有 RNG state 与 move digest；尚未统一五款游戏的 Clock/RNG/command contract。 |
| CG18-P2-50 | InputCommand stream | 部分 | 2048 有 RNG state 与 move digest；尚未统一五款游戏的 Clock/RNG/command contract。 |
| CG18-P2-51 | 纯2048 engine | 待实施 | 已有可测规则方法，尚未完成完整独立 engine/FSM。 |
| CG18-P2-52 | 纯Tetris engine | 待实施 | 已有可测规则方法，尚未完成完整独立 engine/FSM。 |
| CG18-P2-53 | 纯Snake engine | 待实施 | 已有可测规则方法，尚未完成完整独立 engine/FSM。 |
| CG18-P2-54 | Sokoban board primitives | 部分 | 新增无 pygame 的 compact history 编解码；整体 board engine 未拆出。 |
| CG18-P2-55 | Zuma reaction FSM | 待实施 | 已有可测规则方法，尚未完成完整独立 engine/FSM。 |
| CG18-P2-56 | Replay格式 | 待实施 | 尚未发布通用 replay 文件格式与 viewer。 |
| CG18-P2-57 | Replay viewer | 待实施 | 尚未发布通用 replay 文件格式与 viewer。 |
| CG18-P2-58 | Model-based gameplay tests | 部分 | 既有游戏不变量、stress 与本轮 deterministic tests；未建立通用 model engine。 |
| CG18-P2-59 | Benchmark CLI | 部分 | 已有 stress CLI 指标；完整跨平台 benchmark 规格未完成。 |
| CG18-P2-60 | 30–60分钟soak | 待实施 | 本轮运行既有 stress/resource/concurrency 检查，未冒称完成 30–60 分钟 soak。 |

## P3 玩法清单

俄罗斯方块、贪吃蛇、2048、推箱子、祖玛以及全局离线内容六组建议均保留为产品方向。本轮只落地推箱子紧凑 undo 历史和返回点生命周期；未新增 sprint、双人、5×5、编辑器、通用 replay、成就或语言/主题系统。现有 practice、部分 next/hold、seed/RNG、关卡和本地排行不等于清单全部完成。
