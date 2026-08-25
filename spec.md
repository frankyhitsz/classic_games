# 第十五次审查修复规格

## 目标

核对第十五次审查提出的 31 条 Finding 与 P0–P3 建议。首先关闭启动 lease 交接、import root、state
journal 错误分类和 pending import 冲突四项数据正确性问题；随后处理能够在当前本地运行架构内安全闭环的
恢复、Archive 演进、2048 claim 与推箱子练习会话问题。

## 范围

- POSIX application lock 使用独立 transition gate 包住 EX→SH 转换，并在 shared lease 下重新扫描
  transaction root；Windows 保留 byte-range transition gate；
- import 准备目录使用 `.preparing-*`，只有完整 journal 发布后才改名为 `.import-*`；已发布目录缺
  journal 时保留全部 evidence 并阻止启动；
- state journal 的非重试 StoreError 不再绕过 journal 写 SQLite；在线 outbox 与 import planner 复用同一
  duplicate、superseded、merge、conflict resolver；
- prepared reject 的 canonical target 缺失或损坏时恢复 previous；永久 replay 无法完成 reject 时发出
  SUPERSEDED 或 RECOVERY_REQUIRED，不再热循环；
- score/state/clock orphan temp 在 grace window 后校验、合并或隔离；complete export 和 replace 覆盖未决
  temp；legacy pending 使用 no-follow 读取；
- database、lease 与默认 pending 路径使用同一 canonical identity；outbox 构造失败显式释放 application
  session；control hard link 被拒绝；
- Archive v3 按 archive 自身 format dispatch，允许历史 catalog 子集；当前 ruleset 使用严格 validator，
  历史 ruleset 采用 preserve-only 策略；坏目标数据库通过 fresh DB、原子替换和 authenticated rollback
  image 恢复；v1 CLI 恢复绑定导出 evidence SHA-256；
- 2048 claim ACK 同时核对 owner、epoch、slot revision 和 authoritative value hash；
- 推箱子练习保存原 campaign board 并提供返回入口；campaign/practice write Future 和状态消息分离；
  practice schema 不接受 unlock，结果页显示练习成绩；
- Snake 与 Zuma 支持注入 RNG，俄罗斯方块 hold 区绘制方块预览；CI 的 pip 与 dev 安装使用固定 constraints；
- 版本升为 0.8.0，审查任务书归档到 `docs/audits/`。

## 约束与非目标

- 不新增另一套 journal/receipt；transition gate 是无业务 payload 的协作锁文件；
- orphan temp grace 用于避免扫描仍在写入的跨进程文件；未到期 temp 会使 complete export 报告未决；
- 未知历史 ruleset 只保留，不按当前规则执行或进入当前默认排行；
- 默认 outbox 跟随 canonical database parent；显式 custom outbox 仍是测试和高级调用边界；
- branch protection、三平台 hash lock、LICENSE、商标与签名需要仓库管理员或权利人决定，不写成已完成；
- P2/P3 大型重构和新模式不与本轮存储协议修复混做，也不把未实现建议标成已完成。

## 验收标准

- F01–F31 均有成立性与处理结论，P0–P3 共 121 项均有状态；
- 全部 P0 有真实进程、故障或冲突测试，任何语义 conflict 都不写 SQLite；
- missing-journal `.import-*` 保留 rollback image，`.preparing-*` 可清理；
- 同 identity 不同 payload 的 import preview 明确失败，lower order 在 preview 中报告 superseded；
- target 缺失不删除 previous 最后副本，reject 失败产生稳定状态；
- v3 在未来 reader 常量变化和 catalog 增加时仍可读；坏目标 DB 可 replace restore；
- 练习可返回原 campaign board，两个 progress Future 不互相覆盖；
- 至少两轮“发现—修复—复验”，并通过 Ruff、compile、storage、gameplay、stress、release 与远端多平台 CI；
- 本地与 `origin/main` 最终 SHA 一致，工作区干净。
