# 第五次审查修复记录

## 状态

- [x] 核对第五次审查任务书 F01–F21；
- [x] 修复 request ID、旧数据、规则版本和 attempt 合并问题；
- [x] 完成 spool、schema、自恢复、worker、API 与 UI 边界修改；
- [x] 增加第五轮存储和生命周期用例；
- [x] 完成两轮独立复查并再次验证；
- [x] 更新规格、README 和逐条审查结论；
- [x] 创建提交并推送远端。

## 实现摘要

- schema 升至 v3。当前规则版本分别为 `tetris-assist-2`、`snake-classic-1`、
  `2048-classic-2`、`sokoban-campaign-2` 和 `zuma-classic-2`；旧库导入为
  `legacy-v1`。
- 待保存文件的 request 冲突在访问 SQLite 前返回 409，并带原、新 payload hash。
- 旧附加信息依次尝试 JSON 和受限 `ast.literal_eval`；无法读取或超过限制时保留基础成绩。
- schema 快路径核对 attempts 和 receipt 的命名索引；同版本修复也先备份。未使用但非空的
  progress/save/settings 表改名存档，不直接删除。
- 2048/推箱子 revision 不允许降低分数，三款 final-only 游戏不接受后续状态更新，attempt
  状态不可从练习切换为完成或反向切换。
- receipt 损坏时删除缓存并从 attempt 重建；receipt 过期后，派生 attempt UUID 仍参与查询。
- spool fallback 使用逐 request 锁和原子替换，POSIX 发布与删除后同步目录；错名可恢复，坏文件
  隔离，单文件限制 64 KiB，并有 10,000 文件和 64 MiB 告警线。
- 读写 executor 分开，pending 每批最多处理 128 条，启动器每两秒在后台发现其他实例新增文件。
  临时初始化失败会在健康检查、读取或保存时重开仓储。
- `AttemptContext` 统一 game/profile/mode/ruleset/status、attempt UUID 和 revision。
- Flask leaderboard 不再接收 `profile_id`，所有端点拒绝未知查询参数并返回 JSON 400；未预期异常
  返回 JSON 500。本地同步保存也不再忽略未知关键字。
- 未保存退出/重置确认 3 秒后失效；待保存提示改为“已写入待保存文件”。

## 第一轮复查

- 发现不同路径旧库使用相同行号时仍可能产生相同 request ID；现改为按来源路径散列生成，新增
  两个来源、相同行号的导入用例。
- 发现回执表唯一约束未纳入 schema 快路径；增加命名唯一索引、重复回执清理和同版本修复用例。
- 旧库 marker 补齐来源指纹、有效/跳过/仅恢复分数和新增数量。
- `AttemptContext` 补齐 game、attempt UUID 与 revision，不再只封装查询维度。
- pending 目录扫描移到只读 executor，关闭时取消未开始的普通读取，只等待必要写入。

## 第二轮复查

- 发现 2048 在首个请求仍在途时只比较分数，会丢掉“同分但最终元数据不同”的后续状态；现比较
  score 与 extra，并增加异步用例。
- 旧库 upsert 改为先按 source key、attempt UUID 或 request ID 找原记录，兼容早期 marker 写法，
  修复来源后不会重复插入。
- spool 增加总大小告警；标识符在长度检查前先做 NFC 规范化。

## 验证结果

- 功能检查：107 项通过；
- 第五轮存储与生命周期用例：40 项通过；
- 固定输入：20,000 步；
- 渲染 p95：Tetris 2.315 ms、Snake 2.223 ms、2048 1.543 ms、Sokoban 1.118 ms、
  Zuma 4.338 ms；
- 本地保存 p95 1.968 ms、p99 2.251 ms；持锁异步提交 p99 0.029 ms；
- 100 次客户端创建/关闭后 FD 19→19；
- SQLite 并发写入 240 次，`integrity_check=ok`；
- Ruff 和 whitespace 检查通过；仓库 `data/scores.db` SHA-256 仍为
  `e0ae24d4f1361b98e009c7d158f060beff01a8e6b41bed9fa3b2c4c539ec42ca`。

详细判断见 `classic_games_fifth_code_review_response_zh.md`。GitHub 状态只在实际推送后更新。
