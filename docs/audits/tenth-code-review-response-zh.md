# 第十次代码审查核对结果

核对基线为任务书注明的 `9ac703f`。结论来自当前本机环境的代码路径、SQLite 故障注入、pygame
dummy driver、完整存储测试和压力检查；没有把任务书中“审查环境未安装 pygame/Flask”的限制
当作当前项目的运行证据。

## 逐条结论

| 发现 | 结论 | 处理 |
| --- | --- | --- |
| F01 progress 聚合复用已提交 operation ID | 成立，已复现 | journal schema 3 保存 component；多贡献 aggregate 使用独立确定 ID，SQLite 逐 component 幂等。 |
| F02 隔离业务行后 receipt 仍有效 | 成立，已复现 | setting/progress/slot 的隔离与显式 quarantine 在同一事务删除普通和 merge receipt；duplicate 前也检查业务行。 |
| F03 v5→v6 缺 baseline | 成立，属于升级边界 | schema 7 为所有既有 profile、setting、progress、slot 生成 baseline，旧 latest-value journal 不能覆盖。测试用 v6 等价 fixture 覆盖该升级组合。 |
| F04 receipt 表结构修复不完整 | 成立 | verifier 检查列、主键、CHECK 和索引；同版本畸形表触发数据库备份、显式重建和 baseline。 |
| F05 坏 `result_json` 无修复 | 成立 | `get_state_receipt` 与 duplicate 路径从业务行重建结果和 value hash，不删除有效 journal。 |
| F06 late merge 后 winner result 过期 | 成立 | stale merge 在保留 winner 顺序的同时刷新 winner receipt 的权威值；winner 重放返回当前合并结果。 |
| F07 stale merge 状态误报 committed | 成立 | 状态重建逐 component 查询 merge receipt；只要一个贡献未应用就保持 `DURABLE_PENDING`。 |
| F08 merge receipt 无限增长 | 成立 | 新增 `(semantic_key, applied_at)` 索引和 365 天分批清理；当前 journal 引用的 component 受保护。 |
| F09 state clock 无 DB high-water 恢复 | 成立 | backend 启动读取 receipt 高水位；坏、负数或超大 clock 原件移入 quarantine 后再分配。 |
| F10 revision 按 worker 处理顺序 | 现象成立，不能直接按建议同步化 | 持久 revision 在 worker 分配是防止文件锁/fsync 阻塞帧线程的明确取舍；用户动作时间已在入口写入 `updated_at`。跨进程冲突采用 durable commit-order，规则写入 ADR。 |
| F11 direct store 写绕过 receipt | 成立 | 兼容 API 保留，但每次直接写在同一事务更新 baseline receipt；桌面调用仍走 durable operation。 |
| F12 score retry 刷新 `last_used` | 成立，已复现 | duplicate、receipt rebuild、stale/no-op 不更新档案时间；新建或有效更新使用 `occurred_at`。 |
| F13 HTTP launcher 复用 default profile | 成立，API 本身无错 | 后端无 `profiles` capability 时档案按钮显示调试模式，启动游戏传 `profile_id=None`，由服务端按名字派生稳定身份。 |
| F14 Sokoban/Zuma 未解包 `value` | 成立 | 两款游戏统一接受 getter 的裸值和 state write 的 `{value: ...}`，定向 HUD 测试覆盖。 |
| F15 2048 terminal slot 被随机局替换 | 成立 | 恢复终局棋盘与结果页；只有明确 R/按钮重开才生成新局。 |
| F16 多个 2048 实例争用 autosave | 成立 | autosave schema 4 增加 owner token、active/released 和 takeover；冲突界面要求 K 接管，仓储拒绝旧 owner 延迟写。 |
| F17 quarantine/backup 缺恢复闭环 | 成立，完成命令行闭环，图形页待办 | `data_cli status/export` 显示并可归档恢复文件和 invalid rows；导入先预览、显式确认且自动备份。启动器内独立状态页未在本轮加入。 |
| F18 startup 检查未来可能变慢 | 是合理的容量风险，不是当前缺陷 | 当前 verifier 主要查 schema/index/有限约束，没有证据显示现有历史量启动超标。保留可重复 benchmark 任务，不为假设风险改成后台后造成读写竞态。 |
| F19 未启用 branch protection | GitHub 设置事实，不是代码缺陷 | workflow 已加 `contents: read` 和 release-gate。本次“提交到 GitHub”不等于授权修改仓库保护规则，未代替所有者开启。 |
| F20 缺统一 release profile | 成立 | `python -m tests.release fast/full/release` 编排现有三层测试，并输出 JUnit/JSON；CI 增加独立 release-gate。 |
| F21 发行依赖不可完全复现 | 部分成立 | `constraints-release.txt` 固定已验证的顶层版本，release-gate 用 pip-audit 检查；普通 pyproject 范围继续用于兼容检查。完整传递依赖 hash lock 仍待完成。 |
| F22 LICENSE/NOTICE 缺失 | 发行治理问题成立，但不能臆造权利 | 新增权利、素材、商标、NOTICE 和版本清单。当前没有外部图片/音频/字体文件；LICENSE 仍须权利人选择。 |
| F23 数据工具缺统一入口 | 成立，主要路径已完成 | 支持 status、JSON export、preview-import、显式 `--apply` 的原子 import 和导入前备份；破坏性清理与任意 backup 原地恢复仍保留为后续受控操作。 |

## 额外发现

首轮完整存储测试发现：后端启动时先用 probe 创建 `pending/`，随后只判断目录是否存在就启动
自动重放。若启动后才写入一条旧 spool，再提交同 request ID 的冲突请求，后台线程可能在冲突
检查前把旧成绩落库。现在初始化先取得 pending 快照，只有当时确有记录才自动重放；运行期扫描
仍会按原策略发现后来写入的记录。

## 未照单执行的项目

- 把持久 revision 分配移回 pygame 入口会重新引入跨进程文件锁和 fsync 的帧阻塞，未采用；
- 不把三类测试强制迁移到 pytest。任务书已经撤销该结论，当前分层职责清楚；
- 不为没有性能数据的 F18 预先后台化完整性修复；
- 不擅自开启 branch protection、选择 LICENSE 或声称素材权利；
- P2/P3 中改变输入、玩法、内容和桌面打包的建议均保留为接受的产品任务，并在优化矩阵逐项记录，
  但未混入这次数据格式升级中冒充已验证能力。

## 验证结果

- 第十轮定向用例 22 项通过；完整 storage/迁移/生命周期 128 项通过；
- gameplay 107 项通过，包含五款游戏、启动器、HTTP、本机后端和视觉边界；
- 固定 seed 20,000 步、240 次 SQLite 并发写入、100 次资源循环通过，`integrity_check=ok`；
- Ruff、compileall、shell 语法和 whitespace 通过；
- `tests.release full` 的 storage/stress/gameplay 三层均成功，并生成 JUnit/JSON；
- pip-audit 未发现固定发行依赖的已知漏洞；wheel smoke 成功生成 141,569 字节 wheel；
- P0/P1/P2/P3 矩阵分别为 12/48/32/26 行，共 118 行。
- `de04abc` 推送后的 GitHub Actions CI #24 共 7 个 job 全部成功，包括独立 release-gate、
  core-only、Python 3.12/3.13 和 Ubuntu/macOS/Windows 全量检查。
- 随后的纯文档 CI #25 在 macOS 暴露 stress fixture 固定 1 秒 Future 等待；改用 CI 30 秒总完成
  期限等待真实状态，不放宽异步入队 p99、同步保存 p95 或产品数据库预算。
