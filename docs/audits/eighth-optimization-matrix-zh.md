# 第八次审查优化清单核对

状态含义：`完成` 表示已有实现和可重复检查；`部分` 表示合理部分已落地，仍列出缺口；`后续`
表示建议合理且保留为明确工作，不把它冒充本轮已修复；`不采纳` 表示建议的直接实现会带来
数据、法律或协议风险。

## P0

| ID | 状态 | 结论 |
| --- | --- | --- |
| P0-01 | 完成 | state key 使用跨进程 OS 文件锁。 |
| P0-02 | 完成 | compare 与 unlink 位于同一锁区，hash 不同不删除。 |
| P0-03 | 完成 | operation 在排队前取得 logical revision，旧值不得覆盖新值。 |
| P0-04 | 完成 | publish、删除、隔离和升级重写均同步相应目录。 |
| P0-05 | 完成 | typed `SlotLoadResult` 区分空槽与临时失败。 |
| P0-06 | 完成 | 2048 失败门禁、重试、二次确认新开和返回均已接入。 |
| P0-07 | 完成 | attempt identity 不再包含可变显示名。 |
| P0-08 | 完成 | rename + revision 2 更新原 2048 attempt 的集成测试通过。 |
| P0-09 | 完成 | journal 与 DB 同时失败时保留按 key 合并的内存 operation。 |
| P0-10 | 完成 | 游戏和启动器退出保护都检查非持久本机状态。 |
| P0-11 | 完成 | `ProfileController` 统一 generation 与预期 profile。 |
| P0-12 | 完成 | queued launch token 绑定 generation/profile；启动未解析时在解析后绑定。 |
| P0-13 | 完成 | `last_profile` 明确返回 None 前不会创建 guest。 |
| P0-14 | 完成 | state envelope v2 冻结 progress/slot ruleset。 |
| P0-15 | 完成 | 新增跨进程锁、CAS、双故障、slot load、rename 和 profile 竞态检查。 |
| P0-16 | 部分 | CI job 已纳入 P0 用例并设置 timeout/concurrency；required check 是远端仓库设置，当前环境无法用 `gh` 核实。 |

## P1

| ID | 状态 | 结论 |
| --- | --- | --- |
| P1-01 | 完成 | state journal schema v2，v1 可校验、冻结版本并原子重写。 |
| P1-02 | 完成 | progress journal 使用游戏级单调 merge。 |
| P1-03 | 完成 | setting/save-slot 按 logical revision 排序。 |
| P1-04 | 完成 | state quarantine 数量进入恢复通知。 |
| P1-05 | 完成 | 单文件、文件数、总字节和 JSON 复杂度均有限额。 |
| P1-06 | 完成 | `LocalStateEvent` 队列和按 key 状态查询已实现。 |
| P1-07 | 完成 | 2048 pending→saved/error 会从事件回写 UI。 |
| P1-08 | 完成 | Sokoban/Zuma 显示进度 pending/error，恢复成功后清除。 |
| P1-09 | 完成 | durable profile replay 成功后解除启动门禁。 |
| P1-10 | 完成 | profile list Future 也受 generation 保护。 |
| P1-11 | 完成 | guest/anonymous 统一到 canonical default identity。 |
| P1-12 | 完成 | fast path 核对三张状态表完整 PK/UNIQUE。 |
| P1-13 | 完成 | fast path 执行 `foreign_key_check`。 |
| P1-14 | 完成 | profile 碰撞按有效 JSON、单调进度和 slot revision 合并，舍弃项留证。 |
| P1-15 | 完成 | Sokoban progress 的字段、类型、范围和 level key 已校验。 |
| P1-16 | 完成 | Zuma 解锁、最高分、完成标记均有严格 schema。 |
| P1-17 | 完成 | progress 读写 generation 阻止晚到 read 降低 HUD。 |
| P1-18 | 完成 | progress API 与 journal 均传显式 ruleset。 |
| P1-19 | 完成 | save-slot API、journal 与 store 使用同一显式 ruleset。 |
| P1-20 | 完成 | 2048 slot v3 去除 durable SQLite row ID。 |
| P1-21 | 完成 | 旧 row-ID not-found 后按 attempt UUID 去提示重试。 |
| P1-22 | 完成 | 语义坏 slot 原文进入 `invalid_local_state`。 |
| P1-23 | 完成 | gameover 不恢复为可继续棋盘，也不重复提交旧 attempt。 |
| P1-24 | 完成 | slot load 8 秒超时，保持门禁并允许重试。 |
| P1-25 | 完成 | 正常 state read 不取写锁；隔离前短事务重读比较。 |
| P1-26 | 完成 | state journal 按 128 条分批并带总预算。 |
| P1-27 | 完成 | 常规写入更新内存 state count，不再每次遍历目录。 |
| P1-28 | 不采纳 | 直接删除稳定 inode 锁文件可能让已打开旧 inode 的 waiter 与新 inode 分裂；需先设计目录级代际协议。 |
| P1-29 | 完成 | committed 状态可从 180 天 durable receipt 重建。 |
| P1-30 | 完成 | 当前名展示策略、rename continuation 和榜单结果均有测试。 |
| P1-31 | 部分 | 已有轮换、新建、改名和当前档案门禁；尚未实现可滚动的明确列表页。 |
| P1-32 | 后续 | 当前未提供删除入口；实现前仍需原子导出格式和恢复演练。 |
| P1-33 | 部分 | 升级时已有确定的碰撞合并策略；交互式跨档案历史合并尚无预览/撤销。 |
| P1-34 | 后续 | 导出/导入需要版本化格式、hash、冲突预检、临时库校验和原子替换。 |
| P1-35 | 后续 | 原文已保存；用户导出 API 还需脱敏提示和目标路径确认。 |
| P1-36 | 后续 | `invalid_local_state` 尚无保留上限；应先提供导出，再按数量/期限分批清理。 |
| P1-37 | 部分 | worker 异常会进标准日志；尚未配置 rotating file handler 与统一上下文字段。 |
| P1-38 | 后续 | 恢复页应在导出、重试、隔离查看和破坏性确认 API 稳定后接入。 |
| P1-39 | 部分 | README 明示 HTTP 降级边界；尚无机器可读 capability negotiation。 |
| P1-40 | 完成 | Flask factory、strict payload、JSON 错误和统计边界已有合同测试。 |
| P1-41 | 后续 | 当前 unittest/自定义 runner 可单项运行；全量改写 pytest 需先证明 fixture 重构收益。 |
| P1-42 | 部分 | 已有固定 seed、多进程/CAS/故障模型测试；未引入 Hypothesis。 |
| P1-43 | 部分 | 2048 move、input、slot、attempt 边界覆盖较多；尚不是完整状态机属性生成。 |
| P1-44 | 部分 | 四款游戏已有规则不变量、求解器和固定 seed 压力；未形成统一属性测试框架。 |
| P1-45 | 部分 | workflow 可作为 required check；远端 branch settings 未在本环境核实。 |
| P1-46 | 部分 | 本地同等命令通过；推送后的三平台 run 需在 GitHub Actions 查看。 |
| P1-47 | 部分 | coverage 包含 storage/stress；多子进程 gameplay runner 尚未合并数据。 |
| P1-48 | 部分 | 现有真实门槛 60%；在覆盖游戏子进程前直接写 80/90 会制造无证据红线。 |
| P1-49 | 部分 | coverage XML 始终上传；JUnit、失败截图和 server log artifact 尚未统一。 |
| P1-50 | 完成 | workflow 配置并发取消和各 job 超时。 |
| P1-51 | 后续 | 类型检查应先为 pygame/JSON 边界建立 Protocol 与可达基线，再设阻断门槛。 |
| P1-52 | 后续 | 依赖审计合理；当前环境未配置 `pip-audit`，不伪造无漏洞结论。 |
| P1-53 | 部分 | 运行 pygame 已固定于 requirements/environment；optional/dev 仍是兼容范围。 |
| P1-54 | 部分 | 三处 pygame 版本策略一致且可解释；尚未生成单一跨平台 lock。 |
| P1-55 | 不采纳 | 未确认代码/视觉素材权利前不能代权利人加入 LICENSE/NOTICE。 |
| P1-56 | 后续 | 正式发行前需记录命名、经典玩法商标和每项素材来源；当前视觉为代码绘制但仍需权利人确认。 |
| P1-57 | 完成 | changelog、state envelope、slot v3 与兼容策略同步记录。 |
| P1-58 | 完成 | 任务书、结论和矩阵归档在 `docs/audits/`，根文档只保留当前规格/状态。 |

## P2

| ID | 状态 | 结论 |
| --- | --- | --- |
| P2-01 | 完成 | Profile Future 和 launch token 已抽入独立 controller。 |
| P2-02 | 部分 | 启动器可新建、切换、改名并查看部分进度；完整档案页仍待 P1-31/32。 |
| P2-03 | 后续 | GameState Enum 可减少拼写错误，需一次覆盖五款游戏与 overlay 的迁移。 |
| P2-04 | 部分 | BaseGame 已统一成绩保存；attempt/slot 尚未合并为单一 controller。 |
| P2-05 | 部分 | progress generation/merge/status 行为已统一，当前仍实现在两款游戏内。 |
| P2-06 | 部分 | 2048 已有完整 slot 状态机，尚未抽成可复用 `SaveSlotController`。 |
| P2-07 | 后续 | InputManager 需表达按住、缓冲、鼠标、IME 和 overlay 消费差异。 |
| P2-08 | 部分 | 组合输入、退格、可见后缀可用；光标移动、选择和剪贴板尚无控件。 |
| P2-09 | 后续 | 键位重映射依赖 P2-07，并需冲突和恢复默认设计。 |
| P2-10 | 后续 | 键盘菜单导航需明确 focus 顺序、激活和可见焦点。 |
| P2-11 | 后续 | 手柄需真实硬件、热插拔和不同控制器映射验收。 |
| P2-12 | 后续 | 音频需要授权素材、混音设置和无音频设备降级测试。 |
| P2-13 | 后续 | 逻辑分辨率会改变全局命中与布局，需逐窗口截图和输入映射检查。 |
| P2-14 | 部分 | 矢量/超采样图形保持清晰；尚无跨平台 DPI scale 控制。 |
| P2-15 | 部分 | 使用系统 CJK fallback；随包字体仍受许可证前置条件约束。 |
| P2-16 | 后续 | Zuma 球和其他颜色状态仍需形状/纹理冗余编码。 |
| P2-17 | 后续 | 高对比/降低动态需 settings 模型和逐游戏动画开关。 |
| P2-18 | 部分 | stress 固定 seed；Clock/RNG 未全面注入游戏对象。 |
| P2-19 | 部分 | 多项纯规则已可无 SDL 测试；完整 Engine 抽取需渐进完成。 |
| P2-20 | 部分 | 已抽 profile controller；launcher 的 data/render/event 仍在主模块。 |
| P2-21 | 部分 | 首页有 best/recent/profile；continue 与完整 progress dashboard 未完成。 |
| P2-22 | 完成 | Zuma 轨道等高成本静态 surface 已缓存；其余保持 profiler 驱动。 |
| P2-23 | 部分 | Zuma 已有显式 pending reaction 数据和顺序不变量；尚未抽 Enum FSM。 |
| P2-24 | 完成 | stress CLI 输出固定 seed、渲染、保存、FD 和并发指标。 |
| P2-25 | 部分 | 20,000 步、100 次资源循环通过；发布前仍需真实桌面 30–60 分钟 soak。 |
| P2-26 | 后续 | 游戏内规则页需从 catalog/ruleset 生成，避免说明与实现漂移。 |
| P2-27 | 部分 | 启动异常会返回菜单并显示提示；日志路径和恢复操作页未完成。 |
| P2-28 | 后续 | 设置页应承接窗口、音量、按键和辅助项，并使用现有 settings journal。 |

## P3

| ID | 状态 | 结论 |
| --- | --- | --- |
| P3-01 | 后续 | Tetris 7-bag 应使用独立 mode/ruleset，不能改写现有榜单语义。 |
| P3-02 | 后续 | ghost/hold/lock delay 可做舒适模式，需完整旋转/锁定/计分样例。 |
| P3-03 | 后续 | Snake 速度、穿墙和障碍需 mode 分区与独立最佳。 |
| P3-04 | 后续 | 双人同屏需输入冲突、碰撞和结算规则。 |
| P3-05 | 后续 | 2048 撤销需定义 attempt revision、自动存档和排行资格。 |
| P3-06 | 部分 | 底层 slot 已支持 slot ID；多槽列表、预览、删除确认尚未实现。 |
| P3-07 | 后续 | 棋盘尺寸需独立规则版本和布局/生成测试。 |
| P3-08 | 部分 | Sokoban 已持久化解锁并可前往最高关；正式可视选关页未完成。 |
| P3-09 | 部分 | schema 已预留 best moves/pushes；星级公式和 HUD 未定。 |
| P3-10 | 后续 | 死锁检测需正确性、性能和可关闭提示策略。 |
| P3-11 | 后续 | 编辑器需 XSB 解析、地图合法性、求解检查和安全导入。 |
| P3-12 | 部分 | Zuma 多关进度与 practice 存储策略具备；训练选关 UI 未完成。 |
| P3-13 | 后续 | 色弱辅助需球纹、预览和实际可辨识性检查。 |
| P3-14 | 后续 | 原创道具/轨道需独立 ruleset、素材来源和确定性测试。 |
| P3-15 | 后续 | 轨道编辑器需路径采样、合法性、版本化和预览。 |
| P3-16 | 后续 | 本机成就需稳定事件 schema、迁移和防重复触发。 |
| P3-17 | 后续 | 离线每日挑战需日期、时区、seed 和错过日期策略。 |
| P3-18 | 后续 | replay 需 action log、RNG seed 和跨 ruleset 兼容合同。 |
| P3-19 | 部分 | 中文和 CJK 输入可用；完整中英文资源化与长文本布局未完成。 |
| P3-20 | 后续 | Windows 安装包需真实 Windows 无 Python smoke。 |
| P3-21 | 后续 | macOS bundle 需签名/公证身份和生命周期 smoke。 |
| P3-22 | 后续 | Linux 包需 XDG、桌面文件和目标发行版验收。 |
| P3-23 | 后续 | 自动发布需三平台产物、tag 策略、权限和 smoke gate。 |
| P3-24 | 后续 | 签名/校验和依赖发布凭据和产物链。 |
| P3-25 | 后续 | 项目页应使用真实运行截图/GIF，不使用占位素材。 |
| P3-26 | 完成 | Bug/PR 模板已包含版本、系统、日志和本机数据状态。 |
| P3-27 | 完成 | 本轮没有盲目增加游戏；catalog/规则/数据/输入/测试仍是新增门槛。 |
