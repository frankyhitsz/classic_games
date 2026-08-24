# 第九次优化任务逐项核对

状态含义：`完成` 表示本轮已有实现与测试；`部分` 表示合理子问题已处理但验收条件未全部达到；
`保留` 表示建议合理但不是本轮已完成事实；`不采纳` 表示前提不成立或会降低当前设计质量。

## P0

| ID | 状态 | 核对结果 |
| --- | --- | --- |
| CG9-P0-01 | 完成 | schema v6 持久化 semantic key、revision 和 operation ID。 |
| CG9-P0-02 | 完成 | repository 在事务内拒绝旧 operation。 |
| CG9-P0-03 | 完成 | `state_receipts` 与 merge operation receipts 使 crash-after-commit 重放成为 no-op。 |
| CG9-P0-04 | 完成 | DB 使用 journal 的 `updated_at`，重放不刷新业务时间。 |
| CG9-P0-05 | 完成 | setting 新旧 operation 及 backend 事件用例已加入。 |
| CG9-P0-06 | 完成 | slot stale-write 用例验证新 slot 保留。 |
| CG9-P0-07 | 完成 | profile rename stale-write 用例验证新名字保留。 |
| CG9-P0-08 | 完成 | set-progress stale-write 用例验证进度不降低。 |
| CG9-P0-09 | 完成 | startup controller 明确 loading/load-failed/resolved。 |
| CG9-P0-10 | 完成 | unresolved launch 在 resolve 时绑定实际 profile。 |
| CG9-P0-11 | 完成 | controlled Future 覆盖失败、重试和排队 launch。 |
| CG9-P0-12 | 完成 | load 失败只重试；按 G 才显式使用 guest。 |
| CG9-P0-13 | 完成 | superseded/异常回调清理或恢复 non-durable 状态。 |
| CG9-P0-14 | 完成 | 新增 `SaveState.SUPERSEDED`。 |
| CG9-P0-15 | 完成 | status getter 只读内存，重建进入 read worker。 |
| CG9-P0-16 | 部分 | CI core job 执行全部 storage 用例；是否设为 required check 仍是远端设置。 |

## P1

| ID | 状态 | 核对结果 |
| --- | --- | --- |
| CG9-P1-01 | 完成 | 跨进程持久逻辑时钟覆盖系统时钟回拨。 |
| CG9-P1-02 | 完成 | 相同 revision 用 operation ID 确定顺序；同 ID 不同 hash 冲突。 |
| CG9-P1-03 | 完成 | snapshot 按 revision 接受外部较新 pending，并以 0.5 秒节流后台刷新。 |
| CG9-P1-04 | 完成 | `read_key` 直接读取，不扫 128 条。 |
| CG9-P1-05 | 完成 | state/save receipt 重建均在 read worker。 |
| CG9-P1-06 | 完成 | v1 重写前保留原字节并 fsync。 |
| CG9-P1-07 | 完成 | `current` 迁到规范 ruleset key。 |
| CG9-P1-08 | 完成 | 固定 legacy ruleset 兼容表。 |
| CG9-P1-09 | 完成 | 启动时有界统计历史 state quarantine。 |
| CG9-P1-10 | 保留 | 原始 quarantine 仍可从数据目录复制，但尚无导出 API。 |
| CG9-P1-11 | 保留 | 当前不自动删隔离证据；容量/保留 UI 尚未实现。 |
| CG9-P1-12 | 完成 | score/state outbox 健康状态独立报告。 |
| CG9-P1-13 | 完成 | `LocalStateEvent` 可报告 SUPERSEDED。 |
| CG9-P1-14 | 完成 | unexpected/cancelled Future callback 恢复 pending 并清 unpublished。 |
| CG9-P1-15 | 完成 | read worker 2 秒、write worker 10 秒 drain 上限；写超时不取消未完成任务。 |
| CG9-P1-16 | 完成 | 2048 max tile 与 won 双向一致。 |
| CG9-P1-17 | 完成 | playing 死局规范为 gameover，不恢复成可玩局。 |
| CG9-P1-18 | 完成 | quarantine Future 成功后才显示已隔离。 |
| CG9-P1-19 | 完成 | timeout 丢弃当前引用，晚到结果不会自行应用。 |
| CG9-P1-20 | 完成 | ensure/read 用 callback 链；同 slot 未完成 load 合并。 |
| CG9-P1-21 | 保留 | 两个仍活跃进程的可见 slot 所有权尚未设计。 |
| CG9-P1-22 | 保留 | repository 仍为单 autosave；多槽需要选择/接管交互。 |
| CG9-P1-23 | 完成 | receipt 过期后查询 attempt 并稳定重建/冲突。 |
| CG9-P1-24 | 完成 | API 把未预判的 SQLite constraint 映射为稳定 409。 |
| CG9-P1-25 | 完成 | schema v6 增加 state version/time/key/ruleset CHECK。 |
| CG9-P1-26 | 不采纳 | 无基准前用 marker 跳过坏行/结构核对会削弱外部损坏自愈。 |
| CG9-P1-27 | 部分 | 维护、隔离和 replay 已有批量上限；历史库增长继续由 benchmark 监测。 |
| CG9-P1-28 | 完成 | UI 明确重试与按 G 使用 guest，不静默降级。 |
| CG9-P1-29 | 部分 | profile identity 已用 UUID 区分；完整同名档案管理页未实现。 |
| CG9-P1-30 | 完成 | guest/anonymous 规范身份和碰撞迁移已有回归。 |
| CG9-P1-31 | 不采纳 | HTTP 省略 ID 时按名字稳定归一是 legacy 策略；显式 ID 已支持同名不同人。 |
| CG9-P1-32 | 部分 | local/HTTP 暴露明确 capability 集；Protocol 仍保留共同调用面。 |
| CG9-P1-33 | 保留 | 尚无统一 attempts/profiles/state 导出 service。 |
| CG9-P1-34 | 保留 | 导入需要校验、预览和单事务回滚，尚未实现。 |
| CG9-P1-35 | 保留 | migration backup 可追溯但没有恢复 UI。 |
| CG9-P1-36 | 保留 | invalid 表和原始值保留完整，尚无查看/清理界面。 |
| CG9-P1-37 | 部分 | worker/migration 已保留稳定错误码和 traceback 日志路径；未加 rotating handler。 |
| CG9-P1-38 | 保留 | 尚无统一恢复页面。 |
| CG9-P1-39 | 不采纳 | SDL 子进程隔离不是 pytest 语法能解决的问题；现有 unittest 可单项运行。 |
| CG9-P1-40 | 部分 | `run_tests.sh`、CI core/test/compatibility 已分层；未另建 release 命令。 |
| CG9-P1-41 | 部分 | CAS/merge/clock 有确定性边界与跨实例用例；未引入 Hypothesis。 |
| CG9-P1-42 | 部分 | 五款规则有 107 项功能检查和 20,000 步 stress；未引入 Hypothesis。 |
| CG9-P1-43 | 保留 | mutation testing 需先选定稳定核心与预算。 |
| CG9-P1-44 | 待推送核验 | 本地完整通过；推送后记录当前 head 的实际 matrix 结果。 |
| CG9-P1-45 | 保留 | branch protection 是 GitHub 设置，workflow 文件不能代替。 |
| CG9-P1-46 | 完成 | gameplay 子进程生成 parallel coverage 并在 CI combine。 |
| CG9-P1-47 | 部分 | 现有 60% 门槛保留；90/80 需按模块逐步提高，不能无测试硬改数字。 |
| CG9-P1-48 | 部分 | 完整日志始终上传并有 annotation；JUnit 与失败截图尚无。 |
| CG9-P1-49 | 保留 | 尚无 mypy/pyright 门禁；动态 pygame 边界需先补类型。 |
| CG9-P1-50 | 保留 | 尚无 pip-audit job。 |
| CG9-P1-51 | 保留 | 源码安装范围已约束，正式发布 lock/constraints 尚无。 |
| CG9-P1-52 | 部分 | pyproject、runtime requirements、conda 环境职责明确；移除未使用 pytest。 |
| CG9-P1-53 | 需所有者决定 | 不能擅自声明代码权利或选择许可证。 |
| CG9-P1-54 | 需所有者决定 | NOTICE 需要素材来源和授权事实。 |
| CG9-P1-55 | 需所有者决定 | 商标/名称检查属于正式分发前的权利核对。 |
| CG9-P1-56 | 部分 | schema/ruleset/CHANGELOG 有版本记录；尚无独立 ADR。 |
| CG9-P1-57 | 完成 | 审查材料集中到 `docs/audits`，spec/task 只记录当前轮。 |
| CG9-P1-58 | 部分 | journal、receipt、坏库和迁移恢复有自动化用例；未做统一 UI 演练。 |

## P2

| ID | 状态 | 核对结果 |
| --- | --- | --- |
| CG9-P2-01 | 部分 | ProfileController 已有明确 startup 状态；launcher 其余 Future 尚未全部收口。 |
| CG9-P2-02 | 保留 | 档案管理页有价值，当前仅提供切换和新建。 |
| CG9-P2-03 | 保留 | 当前没有删除档案入口；未来增加时必须先导出和二次确认。 |
| CG9-P2-04 | 保留 | profile merge 需要冲突预览与回滚，不能自动猜。 |
| CG9-P2-05 | 保留 | 游戏状态字符串现有测试充分；统一 Enum 可渐进迁移。 |
| CG9-P2-06 | 保留 | BaseGame 与 2048 的 attempt 保存语义仍有专用差异。 |
| CG9-P2-07 | 部分 | LocalStateEvent 已统一状态结果；完整 controller 未抽取。 |
| CG9-P2-08 | 部分 | Sokoban/Zuma 已有 generation 与 schema merge；未抽公共类。 |
| CG9-P2-09 | 部分 | 2048 已实现 load/retry/new-game 门禁；未抽公共 SaveSlotController。 |
| CG9-P2-10 | 保留 | 五款输入规则差异明显，统一 action map 需逐款验收。 |
| CG9-P2-11 | 部分 | launcher 支持 TEXTINPUT/TEXTEDITING、组合文本和宽度裁切；尚无完整选择控件。 |
| CG9-P2-12 | 保留 | 尚无键位重映射 UI。 |
| CG9-P2-13 | 保留 | 游戏内键盘可用，launcher 卡片/档案仍主要依赖鼠标。 |
| CG9-P2-14 | 保留 | 尚无手柄支持。 |
| CG9-P2-15 | 保留 | 尚无 BGM/SFX 系统；无音频设备路径已有 dummy smoke。 |
| CG9-P2-16 | 保留 | 当前固定逻辑尺寸；完整缩放 canvas 是较大布局改造。 |
| CG9-P2-17 | 保留 | macOS/Windows 高 DPI 尚无专门验收。 |
| CG9-P2-18 | 部分 | 已有系统 CJK fallback；打包字体需要明确授权。 |
| CG9-P2-19 | 部分 | Zuma 等使用形状/高光辅助，但颜色仍是部分玩法主信息。 |
| CG9-P2-20 | 保留 | 尚无高对比/降低动态设置。 |
| CG9-P2-21 | 部分 | stress 与部分规则可固定 seed；Clock/RNG 尚未全面注入。 |
| CG9-P2-22 | 部分 | progress/parser/solver 等已有纯逻辑模块；五款 engine 未全部抽离 SDL。 |
| CG9-P2-23 | 保留 | launcher `main` 仍较长；本轮只收口 profile 状态。 |
| CG9-P2-24 | 保留 | 首页已有 best/recent，继续游戏和进度中心尚无。 |
| CG9-P2-25 | 不采纳为当前任务 | stress p95 远低于帧预算，没有 profiler 证据支持先做缓存失效系统。 |
| CG9-P2-26 | 部分 | Zuma reaction 已有 staged pending 模型和回归；尚未独立 FSM 类。 |
| CG9-P2-27 | 完成 | stress 输出 OS 无关的渲染/保存分位数、固定步数和并发完整性。 |
| CG9-P2-28 | 部分 | 20,000 步、100 次资源循环和 240 并发写入已覆盖；未跑 60 分钟墙钟 soak。 |
| CG9-P2-29 | 保留 | 控制提示已有，完整 ruleset/规则页面尚无。 |
| CG9-P2-30 | 部分 | launcher 捕获子游戏异常并返回菜单；专门崩溃恢复页尚无。 |
| CG9-P2-31 | 保留 | 尚无集中设置页面。 |
| CG9-P2-32 | 部分 | leaderboard 默认按当前 ruleset 隔离；旧规则浏览页尚无。 |

## P3

P3 均是可选内容或发行能力，不是第九轮已确认的数据一致性 bug。逐项结论如下。

| ID | 状态 | 核对结果 |
| --- | --- | --- |
| CG9-P3-01 | 保留 | 7-bag 应作为独立 Tetris ruleset，不能改写当前榜单规则。 |
| CG9-P3-02 | 保留 | ghost/hold/lock delay 需独立 comfort 规则与测试。 |
| CG9-P3-03 | 保留 | Snake 新模式应按 mode/ruleset 分榜。 |
| CG9-P3-04 | 保留 | 双人同屏是新增玩法，需独立输入与碰撞规则。 |
| CG9-P3-05 | 保留 | 2048 undo 会影响 attempt/排行榜语义，不能直接加入当前经典模式。 |
| CG9-P3-06 | 保留 | 多存档与 F18 的 slot ownership 一并设计。 |
| CG9-P3-07 | 保留 | 棋盘尺寸必须隔离 ruleset 和榜单。 |
| CG9-P3-08 | 保留 | Sokoban 可增加正式选关，campaign/practice 必须分开。 |
| CG9-P3-09 | 保留 | 星级/推动数需先固定计量规则。 |
| CG9-P3-10 | 保留 | 死锁分析需可关闭且不能把启发式误报成定论。 |
| CG9-P3-11 | 保留 | 编辑器需 XSB 校验、可解性与导入隔离。 |
| CG9-P3-12 | 保留 | Zuma 训练/选关不得混入完整通关成绩。 |
| CG9-P3-13 | 保留 | 色弱符号是合理可访问性增强。 |
| CG9-P3-14 | 保留 | 原创道具/轨道需确定规则和 seed 测试。 |
| CG9-P3-15 | 保留 | 轨道编辑器需路径合法性与版本化。 |
| CG9-P3-16 | 保留 | 本机成就应保持离线且不引入账号。 |
| CG9-P3-17 | 保留 | 每日挑战可用本地 date seed，但需处理时区/改钟语义。 |
| CG9-P3-18 | 保留 | replay 需稳定 command log 与 ruleset 版本。 |
| CG9-P3-19 | 保留 | 中英文需要资源抽取和长文本布局回归。 |
| CG9-P3-20 | 保留 | Windows installer/portable 需真实无 Python 机器验收。 |
| CG9-P3-21 | 保留 | macOS bundle 需真实签名、数据目录和关闭 smoke。 |
| CG9-P3-22 | 保留 | Linux package 需 XDG 与桌面集成验收。 |
| CG9-P3-23 | 保留 | tagged build 自动发布应在许可证和三平台包完成后启用。 |
| CG9-P3-24 | 保留 | 签名/校验和需要发布密钥和 owner 流程。 |
| CG9-P3-25 | 保留 | README 展示素材需要稳定 UI 和素材权利确认。 |
| CG9-P3-26 | 保留 | 新游戏必须同时满足 catalog、规则、数据、输入和测试契约。 |
