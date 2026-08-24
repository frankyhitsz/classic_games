# 第三次代码审查核对结论

本文记录对 `classic_games_third_code_review_local_first_taskbook_zh.md` 的本机核对。
判断依据是 `f88a6ff` 基线、可执行复现、修改后的隔离检查和 macOS 13.7.8
真实项目环境，不把可选玩法或发行路线写成已经存在的缺陷。

## CG3-F01 至 F13

| 编号 | 结论 | 处理 |
| --- | --- | --- |
| F01 | 成立，已修复 | 默认桌面端先把同一逻辑保存写入原子 JSON outbox，再用短事务写进程内 SQLite；成功后移除 outbox。进程退出或重启会自动恢复 pending。本地库与 outbox 都不可写时失败可见，并要求再次确认才放弃退出。 |
| F02 | 成立，已修复 | BaseGame 和 2048 的人工重试复用原 request ID；BackendClient 按 request ID 去重失败项，UI 重试成功会清除启动器里的同一项，不再创建第二条逻辑保存。 |
| F03 | 已复现，已修复 | HTTP Future 现在只有一个完成链：先归档成功/失败，再从 pending 移除并唤醒 condition。`drain()` 等待 pending 集合真正为空；确定性测试不使用 sleep。 |
| F04 | 已复现，已修复 | 2048 `_move()` 返回 `bool`；动画结束后连续丢弃无效果方向，直到某个方向产生移动或队列为空。旧命令不会迟到到下一次移动。 |
| F05 | 成立，已修复 | 2048 进入 won/gameover、暂停、重置和继续边界都会清理方向队列。 |
| F06 | 成立，已修复 | `attempts` 保存每次完整结算，个人最佳由查询聚合。UI 区分“本局已记录”“本局已记录 · 新纪录”，较低成绩仍有 attempt 和最近记录。 |
| F07 | 成立，已修复 | 低于已有 2048 attempt 的更新返回 `no_op=True`，不改 score、extra 或 updated_at，最近记录时间保持不变。 |
| F08 | 成立，已修复 | 幂等 hash 覆盖 game、player、score、extra、replace 和 submission_id；同 ID 的任一语义差异均返回 409。 |
| F09 | 成立，已修复 | attempts 不再因更新个人最佳而物理删除；save_requests 保存完整响应快照，不依赖可能变化的成绩行。并发同 request ID 也只产生一个 attempt。 |
| F10 | 成立，已修复 | HTTP 结果带 retryable 分类；4xx 和请求冲突不会进入全局重试队列，UI 显示不可重试错误。网络和数据库临时错误才允许重试。 |
| F11 | 成立，已修复 | 全局重试逐条进入 retrying 状态，记录在调度成功前不会从失败集合消失；关闭后的调度失败仍保留原项。 |
| F12 | 成立，已修复 | `server.app` import 不创建目录、数据库或迁移；`create_app(config)` 显式注入并初始化 LocalGameStore，CLI 在 main 中创建 app。 |
| F13 | 属于架构目标，已落实核心路径 | `run.sh`、启动器和独立游戏默认直接使用用户数据目录的 SQLite，不需要 Flask、health、端口或 requests。Flask 只在明确运行 `run_server.sh` 或设置 `GAMES_USE_HTTP=1` 时使用，并复用同一仓储。 |

## 模块建议核对

### network 与共享 UI

1. 内存失败队列、双重重试、drain 竞态、永久错误分类和先清空后调度均已处理；默认桌面保存不再经过 HTTP。
2. HTTP 的读写 worker 仍共享一个两线程池，但它现在只是显式 API 调试路径，不能再阻塞默认本地保存。按端点继续拆池没有当前收益。
3. 结果仍用稳定 dict 契约而非 dataclass；已有严格解析、错误码、retryable、attempt_recorded、new_personal_best 和 no_op，改成具体类只影响类型风格。
4. `_confirmed_replace_scores` 没有 mode/ruleset 维度属实；当前五款游戏只有一个可计分模式。增加模式时应把它纳入 attempt，而不是提前制造未使用维度。
5. UI 魔法字符串、enter/exit hook 和 2048 独立保存控制仍是维护性债务；现有状态边界已有确定性检查，完整状态机重构不作为本轮正确性前提。
6. broad exception 只保留在记录展示和可选 API 边界，保存失败会转成可见状态，不会被静默当成成功。
7. 固定窗口、IME、字体资产、设置、音频、主题、键位和可访问性是产品功能，需独立交互与素材规格。
8. 每帧 Surface/Button 分配没有当前性能故障：可重复基准中五游戏 p95 均低于 16.7 ms，因此不做无依据缓存重写。

### 启动器

1. 游戏 ID、名称、描述、模块、标签和颜色已集中到 `game_service.catalog.GameDescriptor`；Flask `/api/games` 也读取该注册表。
2. 默认首页改为“本机最佳 + 最近游戏”，同名 guest 通过每玩家 MAX 只占一个最佳行；HTTP 状态只在显式 API 模式出现。
3. 启动异常已有 UI 提示；损坏成绩库会显示备份名称，无法初始化时显示本机记录不可用。
4. `main()` 拆分、动态卡片布局、键盘焦点、手柄和完整 IME 都是合理的后续维护/可访问性任务，但当前五卡鼠标产品没有相反验收要求。
5. `/api/games` 对默认桌面不再必要，但作为可选 API 自描述端点仍有用途，不是死接口。

### 五款游戏

- Tetris：审查列出的 7-bag、lock delay、ghost、hold、RNG 注入和帮助页是可选规则；当前文档继续明确为 SRS-inspired 自定义旋转，不冒充标准 SRS。
- Snake：升级发生在 catch-up 内时会立即重算下一步 interval；RNG、模式和色弱纹理属于后续功能。
- 2048：无效果与胜利残留输入已修；本机 attempt、个人最佳和同局更新已落地。撤销、存档、重置确认和棋盘尺寸是新规则；独立保存控制器仍可在后续状态机重构时合并。
- Sokoban：每次完整闯关形成 attempt，个人最佳单独聚合；`extra.level` 已与 UI 统一为一基，同时保留 `level_index`。当前 16 关已经求解器验证；任意地图闭合、可达、死角、提示和编辑器需等用户地图功能。
- Zuma：重叠反应、顺序单调、长帧余量和临界救场已有检查；deque、显式 reaction FSM、训练、色弱符号、道具和编辑器属于低优先级优化或新内容。

### 服务端、数据库和脚本

1. 新仓储分离 attempts、个人最佳查询、recent、progress、save_slots 和 settings；当前游戏实际使用 attempts，其他表为后续功能预留，不虚报已有设置或存档 UI。
2. schema version 已建立。旧仓库 `scores.db` 可从外部只读导入，也可在显式指定旧库本身时原地幂等导入；升级前自动生成 SQLite 备份，高于当前版本的库拒绝覆写。
3. 损坏数据库保留 `.corrupt-时间戳` 后重建；只读或不可用路径不阻止游戏启动，保存错误保持可见。完整导出/恢复界面尚未建设。
4. 默认数据库位于 macOS/Windows/Linux 用户数据目录；`GAMES_DATA_DIR` 和 `GAMES_DB` 可覆盖。仓库旧库只作为迁移输入。
5. 新 schema 不再创建未使用的 events 表；旧库已有的 events 不会被破坏性删除。
6. Flask 默认绑定 127.0.0.1。LAN 暴露必须显式配置；为一个可选本机调试适配器增加账号认证不符合项目边界。
7. `run.sh` 不再拉起 Flask。Windows console script、依赖锁、pyproject 和桌面打包仍属于发行工程，不是当前 macOS 源码运行错误。

## P0 清单

| 任务 | 状态 |
| --- | --- |
| P0-01、02 | 2048 no-op 消费和所有状态边界清理已完成。 |
| P0-03、04 | 稳定 request ID 是单一保存所有权；自动、结算页和启动器重试不会重复 attempt。 |
| P0-05 | 失败写入 outbox，跨客户端和进程恢复；本地事务成功即完成持久化。 |
| P0-06 | condition 驱动的 drain 等待完成归档。 |
| P0-07 | 本地同步事务无需退出 flush；持久 pending 可安全退出，完全未落盘必须二次确认放弃。 |
| P0-08 | 临时/永久失败分类已进入 API、HTTP client 和 UI。 |
| P0-09 | 本局 attempt 与个人最佳状态及文案已经分开。 |
| P0-10 | canonical payload hash、响应快照及并发同 ID 已实现。 |
| P0-11 | 低分更新为真正 no-op，不改变 recency。 |
| P0-12 | 所列核心场景均有确定性检查；回调测试使用 Event/Condition，不用 sleep 掩盖时序。 |

## P1、P2、P3 清单

### P1

- P1-02 至 P1-08 的共享本地仓储、默认本地入口、Flask 适配器、用户目录、schema/备份、attempts 和旧数据迁移已完成核心能力。
- P1-09 的本机最佳和最近记录已进入启动器；关卡进度尚无 UI。
- P1-10 的单一注册表已完成；P1-11 launcher 拆分和 P1-12 全局状态枚举是后续重构。
- P1-13 app factory 已完成；P1-14 已用响应快照消除悬空，但没有在缺少真实规模问题时删除旧幂等记录。
- P1-15 至 P1-17 的 pytest、CI、pyproject 和锁文件是合理的工程升级，不是本轮运行缺陷；现有 runner 继续可独立隔离场景。
- P1-18 已覆盖启动错误、损坏库备份和不可用提示；轮转日志/完整崩溃页未实现。
- P1-19 的导入/导出 UI、P1-21 跨平台 console script 需要发行规格。
- P1-20 的根目录整理与全局约定要求根目录 `spec.md`/`task.md` 冲突，本轮保留任务入口；用户已删除上一轮两份审查文档。
- P1-22 中与本轮有关的启动、数据位置、迁移、测试和 API 文档已更新。

### P2

P2-01 至 P2-11、P2-13、P2-16 和 P2-17 是 InputManager、IME、设置、音频、缩放、字体、可访问性、纯规则引擎、Zuma FSM、存档和备份 UI，均需要独立产品设计。P2-12 明确要求 profiler 证明后再缓存，当前证据不支持缓存。P2-14 已形成可重复基准，P2-15 完成 100 轮资源循环；30–60 分钟发行 soak 仍是后续门禁。

### P3

P3-01 至 P3-20 都是舒适模式、内容、档案、成就、replay、本地化、打包、许可和社区治理。它们大多是合理候选，但没有证据表明当前代码违反既有玩法。LICENSE 需要仓库所有者选择许可，不能擅自代填；桌面包和自动发布应在跨平台入口确定后单独实施。

## 测试和性能判断

任务书称 runner “可数为 95”而文档写 103 不一致，这一推断不成立：95 是编号场景数；部分场景分别对五款游戏产生 PASS，`f88a6ff` 基线的真实输出就是 103 PASS、0 失败。改造后是 99 个编号场景、107 PASS、0 失败。

新增 `python -m tests.stress` 并纳入 `run_tests.sh`，固定 seed 为 20260824，覆盖：

- 五款游戏累计 20,000 次随机步进；
- 每款游戏 80 帧渲染基准；
- 300 次含 outbox 的同步本地保存；
- 100 次本地客户端创建/关闭、线程和 FD 基线；
- 16 worker、240 次 SQLite 并发写入及 integrity check。

最终重复运行数据（macOS 13.7.8 arm64、Python 3.11.15、pygame 2.6.1、dummy 视音频驱动）：

- 渲染 p95：Tetris 2.382 ms、Snake 2.232 ms、2048 1.525 ms、Sokoban 1.087 ms、Zuma 4.604 ms；
- 本地保存：median 0.982 ms、p95 1.336 ms、p99 1.572 ms；
- 100 次资源循环 FD 为 19→19；
- 240 次并发写入后 `PRAGMA integrity_check=ok`。

pytest、JUnit、coverage、mutation testing、Windows/macOS/Linux 打包 smoke 和任意覆盖率阈值是工程工具或发行门槛，不能反推为当前 Bug。当前自定义 runner 的缺点成立，但本轮没有为更换框架而重写近三千行已验证场景。
