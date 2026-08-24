# 第七次审查优化清单核对

状态含义：`完成` 表示本轮或现有实现已有代码与检查；`部分` 表示合理部分已落地但验收未全部
满足；`后续` 表示建议合理但不是已复现缺陷，且需要单独设计或平台条件；`不采纳` 表示当前
建议本身不适合直接实施。

## P0

| ID | 状态 | 结论 |
| --- | --- | --- |
| P0-01 | 完成 | 已删除 `os.kill` 进程探测。 |
| P0-02 | 完成 | Windows `msvcrt`、POSIX `flock`。 |
| P0-03 | 完成 | 锁无 metadata，畸形内容可重复加锁。 |
| P0-04 | 完成 | spool schema 2 与双版本 parser。 |
| P0-05 | 完成 | e99 schema 1 显示名身份可升级。 |
| P0-06 | 完成 | 原字节备份；失败保留源文件并提示。 |
| P0-07 | 完成 | 目标升级为 schema v5，显式转换三张状态表。 |
| P0-08 | 部分 | schema 0/1 的既有迁移、v2 真实结构、v4→v5 与幂等均覆盖；仓库没有可核实的发布版 v3 数据包，未伪造“真实包”证明。 |
| P0-09 | 完成 | SQLite backup + 单事务；既有 failure injection 验证回滚。 |
| P0-10 | 完成 | 多进程同 request 检查会在三平台 CI 执行，Windows 分支另有无信号单测。 |
| P0-11 | 部分 | 旧 DB 与旧 pending 组合由测试现场构造并通过；没有历史发布附件可作外部 golden bundle。 |
| P0-12 | 部分 | CI 覆盖 push/tag/PR；是否设为 required check 是 GitHub 仓库设置，代码无法代替。 |

## P1

| ID | 状态 | 结论 |
| --- | --- | --- |
| P1-01 | 完成 | profile 未确认时排队启动。 |
| P1-02 | 完成 | launcher ensure 成功后才启动；standalone 先写 profile journal。 |
| P1-03 | 完成 | attempt 事务自动建 profile。 |
| P1-04 | 完成 | legacy import 自动建 profile。 |
| P1-05 | 完成 | 三张状态表有 profile FK/CASCADE。 |
| P1-06 | 完成 | 当前名展示策略已有 ADR。 |
| P1-07 | 完成 | leaderboard/recent join profiles。 |
| P1-08 | 完成 | progress 主键包含 ruleset。 |
| P1-09 | 完成 | Sokoban 集合、解锁和逐关分数单调合并。 |
| P1-10 | 完成 | Zuma 解锁、最高分和 completed_all 单调合并。 |
| P1-11 | 完成 | practice/campaign 分键。 |
| P1-12 | 完成 | Sokoban/Zuma 启动读取进度并显示解锁状态。 |
| P1-13 | 完成 | profile keyed journal。 |
| P1-14 | 完成 | settings keyed journal，latest-value。 |
| P1-15 | 完成 | progress keyed journal，失败可从 Future 和 pending 数量观察。 |
| P1-16 | 完成 | slot keyed journal，关闭 worker 会等待写入。 |
| P1-17 | 部分 | slot UI 显示写入失败，调用 Future 返回结构化结果；尚未为所有 local-state 另设一套 SaveEvent 队列。 |
| P1-18 | 完成 | 2048 loading gate。 |
| P1-19 | 完成 | slot schema 2 保存 attempt/revision/ACK。 |
| P1-20 | 完成 | gameover 不恢复为 playing。 |
| P1-21 | 完成 | 坏 JSON 隔离，语义坏 slot 回新棋盘。 |
| P1-22 | 完成 | schema 1 slot 可读，下一次写入升级 schema 2。 |
| P1-23 | 完成 | malformed/crash 路径不再依赖 PID 或 PID reuse。 |
| P1-24 | 完成 | shared pending 全部迁移或隔离成功后才改名。 |
| P1-25 | 完成 | schema repair → recovery_required。 |
| P1-26 | 完成 | repair 退避 60 秒。 |
| P1-27 | 完成 | future timestamp 规范化并披露。 |
| P1-28 | 完成 | 坏 settings 隔离后返回默认。 |
| P1-29 | 完成 | 坏 progress 隔离后返回默认。 |
| P1-30 | 部分 | 坏 slot 隔离并清除；原文可从 SQLite 隔离表读取，尚无图形化导出按钮。 |
| P1-31 | 完成 | profile identity/display name 共用 NFC 和控制字符规则。 |
| P1-32 | 完成 | pending 在状态缓存淘汰后可从 durable/non-durable 集合重建。 |
| P1-33 | 完成 | Protocol 补齐 profile/settings/progress/slot。 |
| P1-34 | 完成 | README 明确 HTTP 是成绩 API 调试模式。 |
| P1-35 | 完成 | current-schema 检查列、索引、外键和 trigger SQL 关键约束。 |
| P1-36 | 后续 | 数据导入/导出合理，但需先定义冲突、匿名化和回滚格式。 |
| P1-37 | 后续 | quarantine 导出合理；当前保留原文和备份，尚无 service API。 |
| P1-38 | 部分 | 成绩回执有 180 天清理，pending/current slot 不自动删除；隔离表尚无容量策略。 |
| P1-39 | 部分 | 未预期 worker 错误有 traceback；完整 rotating file log 尚未实现。 |
| P1-40 | 后续 | 恢复页合理，需建立导出和破坏性确认流程后再做。 |
| P1-41 | 后续 | unittest 与自定义 gameplay runner 可单项运行；全量 pytest 迁移收益不足以要求本轮重写。 |
| P1-42 | 完成 | CI 增加只安装核心依赖并执行 v5 本机数据用例的 job。 |
| P1-43 | 部分 | 三平台 CI 已有；required checks 需要仓库管理员配置。 |
| P1-44 | 部分 | coverage 纳入 storage 与 stress；子进程 gameplay runner 尚未接入并行 coverage。 |
| P1-45 | 部分 | 实测总覆盖率 62%，设置 60% 真实门槛；任务书的 80/90% 目前无测试证据，直接写入会让 CI 永久红灯。 |
| P1-46 | 部分 | coverage artifact 已有；JUnit、失败截图和 server log 尚未统一产出。 |
| P1-47 | 完成 | 3.11 三平台，3.12/3.13 Linux 兼容矩阵。 |
| P1-48 | 后续 | 类型检查合理，但现有 pygame 动态边界需先确定工具和基线。 |
| P1-49 | 部分 | 已有固定 seed 和大量不变量检查；尚未引入 Hypothesis。 |
| P1-50 | 部分 | pygame 运行依赖固定，optional/dev 仍采用兼容范围；完整 lock 需确定发布工具。 |
| P1-51 | 不采纳 | 在无法确认代码和素材权利时由维护任务直接选择 LICENSE 会作出无依据的法律声明。 |
| P1-52 | 完成 | 0.6.0 changelog 同步 schema、spool 和 slot 版本。 |
| P1-53 | 完成 | 审查和 ADR 移至 `docs/`，旧根目录材料按用户清理保留删除。 |

## P2

| ID | 状态 | 结论 |
| --- | --- | --- |
| P2-01 | 完成 | 启动器支持新建、轮换和改名；采用紧凑 selector 而非另开页面。 |
| P2-02 | 完成 | 档案均为本机 UUID，进度/FK 分离，无账号。 |
| P2-03 | 后续 | Enum 重构合理，但现有状态字符串已有集中测试，不是本轮缺陷。 |
| P2-04 | 部分 | BaseGame 统一成绩状态；2048 仍有必要的 milestone/slot 控制。完全合并需单独重构。 |
| P2-05 | 后续 | action map 有价值，需同时处理五款游戏的按住/队列/IME 差异。 |
| P2-06 | 完成 | 组合文本、退格、焦点和可见后缀已有检查。 |
| P2-07 | 后续 | 键位 UI 是合理产品功能，需先有 InputManager。 |
| P2-08 | 后续 | 无鼠标菜单导航合理，需焦点模型和可访问性设计。 |
| P2-09 | 后续 | 手柄需设备映射和真实硬件验收。 |
| P2-10 | 后续 | 音频需原创/授权素材和无设备测试，不能只加空调用。 |
| P2-11 | 后续 | 逻辑分辨率会影响全部坐标和命中测试，应独立实施。 |
| P2-12 | 部分 | 矢量绘制与高分辨率 Zuma 轨道已较清晰；尚无跨平台 DPI API 验收。 |
| P2-13 | 部分 | 已有系统 CJK fallback；随包字体需要明确许可。 |
| P2-14 | 后续 | 色弱符号合理，尤其 Zuma，需要完整视觉方案和截图检查。 |
| P2-15 | 后续 | 降低动态/高对比合理，需 settings UI 与逐游戏实现。 |
| P2-16 | 部分 | stress 使用固定 seed；Clock/RNG 尚未全面依赖注入。 |
| P2-17 | 部分 | 多项规则已有无窗口函数测试；整体 Engine 抽取是长期重构。 |
| P2-18 | 后续 | launcher 拆分合理，但本轮先修启动事务，没有为形式重排稳定代码。 |
| P2-19 | 部分 | 首页已有 best/recent 和档案，Sokoban/Zuma 显示进度；尚未改成完整继续游戏中心。 |
| P2-20 | 完成 | Zuma 轨道等静态 Surface 已按主题键缓存；其余是否缓存应由 profiler 决定。 |
| P2-21 | 部分 | Zuma reaction 有显式 pending 模型和重叠反应用例；尚未抽成 Enum FSM。 |
| P2-22 | 完成 | stress CLI 输出环境内固定 seed、渲染、存储和资源指标。 |
| P2-23 | 部分 | 20,000 步、100 次资源循环和 240 次并发写入通过；30–60 分钟 soak 留给发布机。 |
| P2-24 | 后续 | 游戏内规则页合理；README 当前列操作和 ruleset。 |
| P2-25 | 部分 | 游戏启动失败返回菜单并给出提示；独立日志查看页尚未实现。 |

## P3

| ID | 状态 | 结论 |
| --- | --- | --- |
| P3-01 | 后续 | 7-bag 合理，但必须用独立 Tetris ruleset。 |
| P3-02 | 后续 | ghost/hold/lock delay 会改变玩法与得分机会，需完整规则测试。 |
| P3-03 | 后续 | Snake 模式需分离 mode、最佳成绩和操作说明。 |
| P3-04 | 后续 | 双人同屏是新玩法，不是当前单人 bug。 |
| P3-05 | 后续 | 2048 撤销会影响 attempt 与排行语义，需先定规则。 |
| P3-06 | 后续 | 多 slot UI 合理；当前 autosave 可靠性先完成。 |
| P3-07 | 后续 | 棋盘尺寸必须独立 ruleset。 |
| P3-08 | 部分 | 已保存解锁进度并提供 K 前往最高已解锁关；尚无图形选关页。 |
| P3-09 | 后续 | 星级/最佳推动需要明确计算规则和迁移。 |
| P3-10 | 后续 | 死锁分析与提示合理，但需求解性能和可关闭 UI。 |
| P3-11 | 后续 | 编辑器需 XSB 合同、验证、预览与导入安全。 |
| P3-12 | 部分 | Zuma 已有不混入完整通关的层级过程；尚无训练选关 UI。 |
| P3-13 | 后续 | 色弱符号合理，需要整套球纹和命中可读性检查。 |
| P3-14 | 后续 | 道具/轨道是新内容，必须带确定性测试和新 ruleset。 |
| P3-15 | 后续 | 轨道编辑器需路径合法性、版本和预览工具。 |
| P3-16 | 后续 | 本机成就合理，但需事件模型和迁移。 |
| P3-17 | 后续 | 离线每日挑战需日期/时区和 seed 规范。 |
| P3-18 | 后续 | replay 需稳定 action log 和跨 ruleset 兼容合同。 |
| P3-19 | 部分 | 当前中文界面和 CJK 输入可用；完整中英文资源化尚未实施。 |
| P3-20 | 后续 | Windows 包需要真实 Windows 打包与安装 smoke。 |
| P3-21 | 后续 | macOS bundle 需要签名/公证凭据和 app 生命周期 smoke。 |
| P3-22 | 后续 | Linux 包需要 AppImage/等效工具和桌面环境验收。 |
| P3-23 | 后续 | 自动发布需前三项产物、release 权限和 tag 策略。 |
| P3-24 | 后续 | 展示素材应使用真实截图/GIF，未用占位图冒充完成。 |
| P3-25 | 完成 | 增加 Bug report 与 PR 模板。 |
| P3-26 | 完成 | 本轮没有盲目增加游戏；新增游戏仍须满足 catalog、规则、记录、输入和测试合同。 |
