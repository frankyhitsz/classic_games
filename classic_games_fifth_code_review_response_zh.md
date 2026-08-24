# 第五次代码审查核对结论

本文逐条核对 `classic_games_fifth_code_review_local_first_taskbook_zh.md`。判断依据是当前
代码、项目 Conda 环境中的实际执行结果，以及为第五轮问题补充的边界用例。任务书对核心
存储路径的三项 P0 判断准确；部分产品、玩法和工程化建议有价值，但不是当前缺陷，也不适合
和数据迁移修补放在同一次提交中。

## F01–F21

| 编号 | 判断 | 处理结果 |
| --- | --- | --- |
| F01 | 成立 | `_save_mutation()` 现在遇到 outbox `StoreError` 会在访问 SQLite 前返回。request ID 已绑定不同 payload 时返回 409、`retryable=false`，同时给出原、新 hash；原 pending 不删除、不覆盖。集成用例验证数据库为空时 B 不会写入，A 仍可解析。 |
| F02 | 成立 | 旧 `extra` 先按 JSON 解析，再用 `ast.literal_eval()` 解析早期 Python repr；递归限制为 JSON 可表示的安全类型。无法解析、类型不合适或超过 8 KiB 时只舍弃 `extra`，基础 score/player/game/time 仍导入，并记录“成绩已恢复，附加信息无法读取”。 |
| F03 | 成立 | schema 升至 v3。当前五款游戏使用显式规则版本，导入来源一律为 `legacy-v1`。默认 leaderboard/recent/stats 只查当前规则；旧记录仍可用显式 ruleset 查询。v2 中非导入来源的规则“1”按游戏迁移到当前版本。 |
| F04 | 成立 | 硬链接仍是首选发布方式；不支持硬链接时，使用逐 request 的跨进程锁，把已完成文件写入和 fsync 的临时文件通过 `os.replace()` 原子发布。POSIX 下发布、删除和隔离后同步目录。没有继续使用会暴露半文件的 final-path 直接写入。 |
| F05 | 成立 | 扫描时校验文件名必须是 envelope 的 `<request_id>.json`。错名但内容有效的文件会移回 canonical 路径；canonical 已存在且 hash 相同则去重，不同则隔离错名项。删除只根据已验证的 request ID 定位。 |
| F06 | 成立 | `retry_failed_saves()` 每次重新发现目录内容；启动器每两秒在只读 executor 中触发一次扫描，因此长期运行的实例能发现另一实例后来写入的 pending，不在渲染线程遍历目录。 |
| F07 | 成立 | 初始化写锁预算由 5 秒降到不超过 250 ms。临时锁、目录或数据库错误不会永久固定 `store=None`；健康检查、读取和保存都会尝试重开。`unsupported_schema` 仍是永久错误，防止用旧程序修改新数据库。 |
| F08 | 成立 | 当前 schema 快路径除列外，还核对 attempts 的 request/attempt/source/best/recent 索引和 receipt request 唯一索引。当前版本缺索引时也先做 SQLite backup，再在事务内修复；重复 receipt 会删除缓存后重建，attempt 不受影响。 |
| F09 | 成立 | transport ID 只有 `None` 会自动生成。显式空字符串返回 `invalid_request_id` 或 `invalid_attempt_uuid`，不再悄悄替换。 |
| F10 | 成立 | revision 和 submission ID 都限制在 SQLite 有符号 64 位正整数范围内，过大值在绑定 SQLite 前返回稳定 400。worker 对业务错误、IntegrityError、OperationalError、数据库损坏和未预期异常分别分类；未预期异常记录 traceback 并隔离对应 spool。 |
| F11 | 成立 | catalog 为每款游戏声明 `final_only` 或 `monotonic_revision`。2048/推箱子高 revision 不能降低 score，同分可更新 extra；俄罗斯方块、贪吃蛇、祖玛的已结算 attempt 不接受高 revision。`practice/completed` 状态不可变，不再组合“旧高分 + 新低分元数据”。 |
| F12 | 成立 | 查询 attempt 不再依赖“客户端是否显式给了 UUID”。旧式请求由 request ID 派生出的 attempt UUID 同样查询；删除 receipt 后重放会命中原 attempt、返回 no-op 并重建 receipt，不触发唯一约束异常。 |
| F13 | 成立 | receipt 的 `response_json` 若损坏或不是成功对象，会删除该缓存并从 attempt 重建。SQLite lock/busy/只读/I/O 错误可重试，IntegrityError 和损坏库不会永久循环重试；相关 pending 会移入 quarantine。可选 Flask 也为未预期异常返回 JSON 500。 |
| F14 | 成立 | Flask 端点分别声明查询参数：leaderboard 只接受 mode/ruleset/status，stats 可加 profile，recent 可加 profile/game。`profile_id` 传给 leaderboard 现在返回 JSON 400，不再形成 TypeError/HTML 500；未知参数和非法维度同样得到 JSON 错误。 |
| F15 | 成立 | 新增 `AttemptContext`，包含 game/profile/mode/ruleset/status、attempt UUID 和 revision。BaseGame 每局创建一次，2048 复用同一上下文；保存调用显式传递维度，不再依赖仓储默认值恰好与游戏一致。 |
| F16 | 现状判断成立，但列为本次修复项不合理 | profile UUID、家庭成员切换、昵称持久化和 IME 是一组产品功能，不是可局部修补的存储 Bug。仓库目前没有档案选择、改名语义、合并策略或 settings UI；直接生成 UUID 会让现有显示名历史失去归属。README 已明确当前显示名也是 profile 标识。本轮不虚构半套档案系统，后续应连同迁移和 UI 独立设计。 |
| F17 | 主要成立，按数据安全取舍 | 读写已分为独立 executor，SQLite 仍只有一个 writer；pending 每批最多 128 条，目录轮询有间隔，maintenance 排在必要恢复之后。关闭会取消未开始的普通读取，只等待写入/spool 转换。没有给必要写入设置强制取消时限，因为那会与“退出不丢成绩”直接冲突；调用方仍可用 `drain(timeout)` 做有界等待和提示。 |
| F18 | 成立 | BaseGame 与启动器的二次确认均为 3 秒，过期后必须重新确认。quarantine 通知会在后续扫描后刷新；“待保存记录已安全落盘”改为更准确的“已写入待保存文件”。未把所有“本机最佳”改成竞技排行榜措辞，当前界面已经主要使用“本机最佳/最近游戏”。 |
| F19 | 成立，数据管理 UI 部分另议 | 单文件上限 64 KiB，单次最多扫描 10,000 个 JSON，并对总量超过 64 MiB 提示；错名、重复、坏文件和旧 quarantine 均有治理。每次重放前原子递增 `attempt_count`。导出/手动清理按钮属于会移动用户数据的管理 UI，本轮只保留原文件和明确提示，不擅自增加删除操作。 |
| F20 | 成立 | 外部 marker 按源绝对路径分开，记录 path、size、mtime、migration version、valid、skipped、metadata-recovered、imported 和时间。源文件更换或修改后会重新评估；source/request/attempt 身份均含来源，不同路径相同行号不会冲突。旧空壳表若非空先改名为 `legacy_*`，仅空表删除。 |
| F21 | 成立 | 本地同步 `submit_score()` 删除 `**_ignored`；未知关键字现在和 async/HTTP 一样被拒绝。 |

## 分模块建议

### mutation、store 与本地服务

以下建议已经随 F 项落实：空 ID、SQLite 整数上界、Unicode NFC、分数策略、receipt
恢复、错误分类、读写分离、结构索引验证和来源 marker。

任务书建议把 mode/ruleset 限制为当前注册表枚举。当前只有 `classic` 会由游戏产生，
`AttemptContext` 已阻止游戏端随意拼接；仓储仍允许显式字符串是有意保留的调试/未来模式
扩展接口，并有长度、控制字符与 status 校验。此处不存在 SQL 注入，也不会进入默认当前规则
查询，因此没有再增加一份容易与 catalog 漂移的硬编码白名单。

DB-level CHECK、migration registry、数据导出/清理、旋转日志和恢复页面都是可用的后续增强，
但需要新的 schema/API/交互规格。当前 mutation 是所有写入口，迁移器也逐行规范化；仅为形式上
增加 CHECK 不值得在本次兼容迁移中重建整张 attempts 表。

### UI、启动器与五款游戏

保存状态字符串、BaseGame/2048 两套控制流、launcher 拆分、固定窗口、Surface 缓存、键盘菜单、
IME、音频和可访问性判断基本准确，但多数是维护性或产品任务。实测五款游戏渲染 p95 为
1.1–4.4 ms，未出现需要立即大规模缓存/重构的性能证据。

本轮额外修复了任务书在 2048 模块中指出的同分状态问题：里程碑请求在途时，如果最终分数相同
但 extra 不同，后续 revision 仍会提交。Tetris 7-bag/ghost/hold、Snake 多模式、2048 存档/撤销、
Sokoban 进度/编辑器、Zuma FSM/训练/道具等会改变玩法或规则版本，应分别设计并单独验收。

### Flask、测试与仓库治理

Flask 的 query schema 与 JSON 异常已修复。HTTP client 的失败队列仍是进程内语义；它只是显式
开启的调试适配器，不能反过来增加默认桌面的网络复杂度。

pytest 迁移、CI 三平台矩阵、coverage/JUnit、桌面安装包和社区文件是合理工程计划，但并非本轮
代码审查发现的运行错误。现有 `run_tests.sh` 能独立执行 107 项功能检查、40 项存储边界检查和
压力检查。LICENSE 需要仓库所有者选择授权条款，不能由维护提交擅自决定。

## 保留的限制

- 当前 profile 仍以显示名标识，改名会形成另一组本机记录，同名家庭成员会合并；
- 没有 pending/quarantine 的导出、清理界面；文件会保留在数据目录；
- 异步提交刚返回 Future、worker 尚未开始写 spool 时若进程被强制终止，无法保证恢复。要关闭这
  个窗口只能把磁盘 journal 放回调用线程或引入常驻进程，两者都改变当前低延迟边界。返回
  `durable_pending` 后立即 `os._exit` 的恢复路径已经验证；
- 没有 Windows/macOS 特殊文件系统的 CI 结果。本机 macOS、无硬链接注入、32 进程并发和
  POSIX fsync 路径均已覆盖，但不能把注入测试写成跨平台实机证明。

## 验证结果

- 107 项功能检查全部通过；
- 40 项第五轮存储与生命周期用例通过，覆盖任务书 9.1–9.4 的关键场景；
- 固定 seed 玩法执行 20,000 步；
- 渲染 p95：Tetris 2.315 ms、Snake 2.223 ms、2048 1.543 ms、Sokoban 1.118 ms、
  Zuma 4.338 ms；
- 本地保存 p95 1.968 ms、p99 2.251 ms；SQLite 持锁时异步调用 p99 0.029 ms；
- 32 进程待保存文件写入、100 次客户端创建/关闭、240 次 SQLite 并发写入均通过，
  `integrity_check=ok`，FD 19→19；
- Ruff、Python 编译、shell 语法、wheel 构建和 whitespace 检查通过；
- 验证前后 `data/scores.db` SHA-256 均为
  `e0ae24d4f1361b98e009c7d158f060beff01a8e6b41bed9fa3b2c4c539ec42ca`。

性能数字只说明本次机器上的检查结果，验收依据仍是测试阈值和数据不变量。
