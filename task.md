# 第四次审查修复记录

## 当前状态

- [x] 确认基线、远端和用户已有的任务书替换；
- [x] 完整阅读第四次审查任务书并建立 F01–F19 核对表；
- [x] 实现非阻塞 worker、durable spool 和统一 mutation；
- [x] 完成 schema v2、attempt UUID/revision 和查询修复；
- [x] 完成迁移、退出保护、类型状态和可选依赖修复；
- [x] 增加第四轮边界与压力检查；
- [x] 完成第一轮独立复核并重新验证；
- [x] 完成第二轮独立复核并重新验证；
- [x] 更新 README、规格和逐条审查答复。

## 初步核对

- F01–F09、F12–F16 均能由当前控制流或最小复现证实；
- F10 是已存在但未接入的数据维度，本轮随 schema v2 完整接入；
- F11 的三张表没有任何 API 或 UI，选择删除空壳而不是虚构功能；
- F17 是统计口径选择，README 改为“每次结算”，不擅自记录 abandoned；
- F18 混合了可靠性缺陷和数据管理产品功能，只修复前者；
- F19 的 LAN 风险成立，文档明确边界，不建设账号或复杂 token；
- P2/P3 的输入、音频、玩法、编辑器、打包和社区文件保留为路线建议。

## 实现记录

- `LocalWriteWorker` 以真实 Future 执行本地读写；SQLite busy timeout 从 5 秒缩短到 250 ms；
- 当前 schema 启动使用只读快路径，回执维护在后台执行；
- `ScoreMutation` 在 spool/SQLite/HTTP 前统一验证 extra、ID、维度和 revision；
- 共享 JSON 改为逐 request spool 文件，使用唯一临时文件、fsync 和排他发布；
- 坏 envelope 按原因移入唯一 quarantine，旧 JSON 数组可逐项迁移；
- schema v2 加入 attempt UUID、revision、profile/mode/ruleset/status 和 `score_achieved_at`；
- 2048 与 BaseGame 的重试复用 request ID、attempt UUID 和 revision；
- strict submission ID、personal best rank、最佳 attempt tie 查询和 stale revision 已完成；
- 外部旧库做列检查和逐行规范化，DDL 迁移使用显式事务；
- 统一破坏性操作保护覆盖窗口关闭、Esc、返回按钮、重置和推箱子关卡切换；
- `StorageStatus/DataResult/GameDataService` 解耦游戏与具体后端；
- `pyproject.toml` 将 core、api、dev 依赖分开，HTTP 客户端延迟导入。

## 第一轮独立复核

- 发现 schema DDL 使用 `executescript` 时不能证明整体回滚，改为显式事务，并增加注入失败测试；
- 发现同分 extra-only 更新会改变 tie 时间，增加 `score_achieved_at`；
- 发现 spool 的时间和计数字段会接受 bool/NaN，补严格有限数验证；
- 发现永久 payload 冲突可能留在重试状态，改为保留原 spool、明确拒绝新 payload；
- 修复后原有回归、18 项当时的边界套件和压力检查全部通过。

## 第二轮独立复核

- 发现已是当前 schema 的启动仍会争抢 5 秒写锁，增加只读 schema 快路径和后台维护；
- 增加强制 `os._exit` 后的 durable spool 恢复；
- 增加 UI 重试 attempt/revision 稳定性和持锁提交 p99 压力；
- 验证 32 个独立进程并发写 spool，32 条均可解析；
- 构建 `classic_games_hub-0.4.0` wheel 成功，清理并忽略本地构建产物；
- 阻止 requests 导入时，launcher 和游戏 core 模块仍能加载；
- shell 语法、compileall、Ruff 和 diff whitespace 检查通过。

## 验证结果

- 原有功能回归：107 PASS，0 失败；
- 第四轮边界套件：21 项通过；
- 固定 seed 玩法：20,000 步；
- 渲染 p95：五款游戏约 2.3–4.7 ms；
- 正常本地保存仍低于 16.7 ms 帧预算；持锁异步提交调用 p99 0.021 ms；
- 100 次客户端创建/关闭后线程回到基线，FD 无持续增长；
- 240 次并发 SQLite 写入完成，`PRAGMA integrity_check=ok`；
- 测试前后仓库 `data/scores.db` 主文件 SHA-256 均为
  `e0ae24d4f1361b98e009c7d158f060beff01a8e6b41bed9fa3b2c4c539ec42ca`；
- 逐条判断与未采纳项见 `classic_games_fourth_code_review_response_zh.md`。

GitHub 交付状态以实际远端提交为准，不在提交前写成已推送。
