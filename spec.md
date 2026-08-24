# 第十次审查修复规格

## 目标

逐条核对第十次审查的 23 条发现和 118 项建议。优先保证崩溃残留 journal、业务行损坏隔离和
旧数据库升级都不会丢失或回退本机状态；同时关闭已能在当前产品内完成的身份、进度 HUD、2048
存档冲突、数据恢复和发行门禁问题。

## 范围

- state journal schema 3 为每个单调 progress 贡献保存 component ID/hash，聚合使用独立 ID；
- SQLite schema 7 把 state receipt 与业务引用、权威值 hash 绑定，并为旧 profile、setting、
  progress、slot 建立 synthetic baseline；
- duplicate 前验证业务行，损坏 receipt 从权威行修复，缺失或隔离业务行同步失效所有相关回执；
- state clock 从数据库 high-water 恢复，损坏或超大时钟保留原件；merge receipt 建索引并按
  365 天有界清理，仍在 journal 中的 component 不清理；
- 延迟或重复 score 重放不刷新档案 `last_used`，有效写入使用真实 `occurred_at`；
- HTTP 调试启动器不再携带本机 default profile ID；Sokoban/Zuma 解包 state result 的 `value`；
- 2048 恢复终局棋盘，autosave schema 4 使用 owner token、released 状态和显式接管；
- 提供数据状态检查、JSON 导出、导入预览和带备份的原子导入命令；
- 增加 release 测试入口、JUnit/JSON 结果、只读 Actions 权限和顶层发行约束。

## 约束

- 保持默认本地运行，不增加账号、云同步、遥测、广告或联网对战；
- 不改变五款游戏当前计分和 ruleset；需改变玩法的建议进入逐项矩阵，不能冒充本轮已完成；
- 文件锁、SQLite 和 fsync 不进入 pygame 帧线程；跨进程 revision 仍在写线程持久分配；
- 数据导入默认只预览，必须显式 `--apply`，执行前创建数据库备份，现有冲突行优先；
- LICENSE 必须由权利人选择。本轮提供权利、素材、商标和版本清单，但不伪造授权结论；
- branch protection 和 required checks 是仓库设置，未经单独授权不修改。

## 关键决策

1. progress component 是幂等最小单元，aggregate 只是可重建的传输封装。相同 component ID/hash
   可重复，相同 ID 不同 hash 为冲突。
2. receipt 不是业务事实本身。任何 duplicate、状态重建或状态查询都以当前业务行为权威来源；
   回执缓存损坏时修回执，业务行缺失时删回执并保留 journal 的重建机会。
3. baseline 的 revision 来自业务 `updated_at`，新本机操作同时比较发生时间和顺序。比基线更旧的
   latest-value journal 被淘汰；单调 merge 仍可贡献尚未应用的 component。
4. 2048 terminal slot 是可查看的完成记录，不自动替换。active autosave 需要同 owner 或显式声明
   takeover，正常退出尽力写 released。
5. 测试仍保留 unittest、gameplay runner 和 stress 三层；`tests.release` 只负责编排与机器结果，
   不把现有测试强行改写成另一框架。

## 验收标准

- F01–F23 和 P0–P3 共 118 项均有成立性、处理状态和证据；
- commit-before-unlink 后晚到 progress 贡献不丢，重复 component 不增加版本；
- setting/progress/slot 被删或隔离后，有效 journal 可重建，坏 receipt JSON 不删除业务状态；
- v6 既有新值升级后拒绝更旧 profile、setting 和 slot journal，新 revision 高于基线；
- 晚到 merge 后 winner duplicate 返回当前合并值，未应用 stale merge 状态仍为 pending；
- HTTP 身份、score 时间、Sokoban/Zuma HUD、2048 terminal/ownership 均有定向测试；
- 完成两轮独立复查，完整 storage、gameplay、stress、Ruff、compile、wheel 和数据导入演练通过；
- 提交推送后 release-gate、三平台和 Python 兼容 CI 通过。
