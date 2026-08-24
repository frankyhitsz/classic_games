# 第六次代码审查核对结论

本文按 `classic_games_sixth_code_review_local_first_taskbook_zh.md` 核对当前实现。判断依据包括
实际代码路径、临时 SQLite/文件系统故障注入、headless pygame 操作和固定 seed 压力检查。
任务书的两个发布阻断和大部分 F 项成立；少数条目把稳定版路线图、法律决定或桌面发行工作
混入了缺陷清单，下表分别说明实际处理边界。

## F01–F22

| 编号 | 判断 | 处理结果 |
| --- | --- | --- |
| F01 | 成立 | SQLite 错误先读 `sqlite_errorcode` 的基础码。FULL、只读、I/O、锁等均保留 mutation；spool 已落盘时保留文件，spool 也 ENOSPC 时保留内存副本。释放故障后可重试提交，只有永久 mutation 语义错误才清除 pending。 |
| F02 | 成立 | 旧 pending 使用 `parse_constant` 拒绝 NaN/Infinity，解析前限制 4 MiB，解析后限制 32 层、100,000 节点、64 KiB 字符串和 10,000 项。隔离时即使原值无法再次序列化或生成 repr，也只写类型/安全摘要；隔离目录不可写不会逃出构造函数。 |
| F03 | 成立 | 新增逐 request `SaveEvent` 和状态查询。后台重试成功、需恢复、隔离或永久失败后，BaseGame 与 2048 会更新保存提示；commit 后才刷新结果榜单。状态缓存有上限。 |
| F04 | 成立 | `retry_failed_saves()` 返回 Future，目录遍历在 read worker；启动器按 S 只安排任务。新增慢扫描注入证明调用本身不等待目录扫描。 |
| F05 | 成立 | current schema 快路径独立调用 external import，不再因旧库失败而反复进入 schema 备份。失败 marker 有 5 分钟退避；成功路径同时保留内容 marker 和路径状态 marker，未变化路径无需每次重算内容 hash。 |
| F06 | 成立 | 旧行身份由规范化 game/player/score/extra/created time 的语义 hash 决定，不包含绝对路径；同一旧库复制或移动后不会重复。不同语义的来源仍可导入。 |
| F07 | 成立 | submit 时在调用线程记录结算时间，pending envelope 保存该时间，重放写入 started/finished/score-achieved/created 时继续使用；`updated_at` 才表示实际落库时间。 |
| F08 | 成立 | schema v4 快路径检查行级 ID、UUID、game、status、revision、score 和时间不变量。迁移先修可恢复的旧 ID/profile，再把剩余坏行分批写入 `invalid_attempts`；修复前保留 backup。INSERT/UPDATE trigger 阻止后续绕过 repository 写坏行。 |
| F09 | 成立 | busy/full/read-only/I/O/cant-open/corrupt/not-a-db/constraint/interrupt/schema 使用 SQLite code 映射；英文消息只作为旧运行库没有 code 时的保守后备。非英文消息注入已覆盖。 |
| F10 | 成立 | legacy extra 在 JSON/`literal_eval` 前检查 UTF-8 原始大小，之后用迭代栈限制深度、节点、字符串和非有限 float；行游标逐条读取，不使用 `fetchall()`。 |
| F11 | 成立 | spool 扫描对每个文件分别捕获 stat/read/rename/lock 的 OSError。一个权限错误文件保留原位并给出提示，其他有效文件继续重放。 |
| F12 | 成立 | store reopen 区分 transient、repair-required 和 permanent-newer-schema；临时错误指数退避，迁移/修复错误至少冷却 60 秒，unsupported schema 永久停止本版本重开。 |
| F13 | 成立 | `save_requests(expires_at)` 有索引，初始化和 maintenance 每次按 expiry 顺序最多删除 500 条。 |
| F14 | 成立 | request lock 写入 owner PID；冲突时用进程存活判断，只回收已退出 owner 的锁，不再用 wall clock 和固定 30 秒。无法判断时宁可超时保留文件。 |
| F15 | 成立 | score policy 改为 `ScorePolicy` 枚举；catalog import 时验证 ID 唯一、必要字段和 policy 类型，拼错字符串会立即失败。 |
| F16 | 成立 | `_confirmed_total` 改为 `None` sentinel，完整通关总分 0 仍会提交并在 ACK 后确认。 |
| F17 | 成立 | medal 根据真实 rank，所有并列第一均显示金牌；最近记录使用普通圆点和游戏标签，不再伪造 1/2/3 竞技名次。首页仍保留最佳和最近，同时 2048 有继续数据、推箱子/祖玛有进度数据；受现有 980×680 布局限制，没有把所有信息同时塞入卡片。 |
| F18 | 成立 | 新增本机 profile repository。身份为 32 位 UUID，显示名可改且持久化 last-used；旧显示名/旧自定义 ID 迁移为确定性 UUID。五款游戏显式传 profile UUID。启动器改用 `TEXTINPUT/TEXTEDITING`，支持系统 IME 组合文本。 |
| F19 | 成立 | 删除 2048 自己维护的 submit/poll/retry/pending 状态机，改用 BaseGame 的统一保存流程；2048 仅保留“同 attempt 的下一 revision”队列和确认后的别名字段。 |
| F20 | 成立，按调用边界修复 | 启动器使用 deferred initialization，schema 检查、迁移和旧库 hash 在后台 read worker；首次 pending 和 quarantine 扫描也在后台。显式同步 `LocalGameStore`/`storage_status()` 仍保持同步，这是脚本与 Flask API 的既有契约，不是 pygame 路径。 |
| F21 | 成立 | stats 不再接受/忽略 limit；HTTPException 统一 JSON；StoreError details 保留；HTTP replace 重试按 attempt UUID/mode/ruleset 清理，不会误删同名玩家的另一局。失败队列仍为内存，因为该适配器明确是本机调试路径；默认桌面保存不依赖它。 |
| F22 | 部分成立 | 增加三平台 GitHub Actions、Ruff、全部存储/功能/stress、coverage XML artifact、0.5.0、固定 pygame 版本、CHANGELOG、CONTRIBUTING 和 SECURITY。没有擅自添加 LICENSE：授权条款会改变他人复制、修改和分发代码的法律权利，必须由仓库所有者明确选择。现有 unittest 用例可由 pytest 收集，强制机械改写测试框架不会增加覆盖。mypy/Hypothesis、安装包和 JUnit 可在真正采用相应代码/发布流程时加入，不能把“配置文件存在”冒充三平台桌面发行已验证。 |

## 完整优化清单核对

P0 的八项要求均已落实到实现或 CI：FULL/ENOSPC 恢复、严格 legacy parser、隔离降级、逐文件
I/O 边界和故障注入都有直接用例。P1 中与本轮数据闭环相关的 SaveEvent、异步 retry、退避、
结算时间、导入 marker、schema 行约束、legacy 限制、receipt 维护、catalog、零分、profile、
progress、save slot、settings、统一保存控制器、Flask、HTTP attempt key、CI、coverage、依赖固定、
SemVer 和维护文档已经完成。

以下条目是合理的后续能力，但任务书没有给出足以安全实施的恢复/删除格式：quarantine 导出、
数据库备份恢复和按日期清理。它们会移动或删除玩家文件；在没有目标位置、冲突策略、恢复校验和
取消语义时直接加按钮，反而会扩大数据损失风险。本轮保留原始 DB、migration backup、pending
和 quarantine，并保持清晰状态，不执行猜测性的破坏操作。

P2 中直接可验收的 IME、tie medal、recent 语义和启动线程问题已完成。GameState/InputManager、
键位重映射、手柄、音频、逻辑分辨率、高 DPI、色弱/高对比、RNG 注入和规则引擎抽取本身是可用
方向，但当前审查没有复现这些项造成的错误，也没有输入冲突、资源许可、窗口尺寸或交互规格。
因此没有用一次提交同时重写五款游戏的输入和渲染架构。

P3 中完成了 2048 自动存档/继续数据、推箱子关卡进度和祖玛关卡进度。7-bag、hold、撤销、
新模式、道具、编辑器、每日挑战等会改变规则或内容；若直接放入当前 ruleset，会让新旧最佳分数
失去可比性。它们不是当前代码与既定规则不一致的 Bug，因此本轮不静默改变玩法。桌面安装包和
自动 Release 同样不能由 headless macOS 开发环境证明已在三平台安装运行；CI 已先补 smoke 门禁。

## 新增与更新的验证

- FULL（durable 与 non-durable）、ENOSPC、busy、非英文 SQLite 消息和释放故障后重试；
- NaN、深嵌套、无法重新序列化的隔离值、单 spool OSError；
- durable pending→committed 的事件和 BaseGame UI 回写；
- 手动 retry 慢扫描不阻塞调用线程；
- 重放完成时间、旧库换路径、current 坏行、旧 profile UUID 迁移；
- legacy raw/depth、profile/settings/progress/save-slot 往返；
- 推箱子零分、并列 medal、recent 非排名、2048 自动存档恢复；
- Flask stats limit、JSON 404/405 和 catalog contract。

最终运行数字记录在 `task.md`；性能数字只描述本机 headless 检查，不替代真实桌面试玩和三平台
安装器验收。
