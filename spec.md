# 第四次审查修复规格

## 目标

核实 `classic_games_fourth_code_review_local_first_taskbook_zh.md` 的 F01–F19，
关闭本地保存的阻塞、损坏启动、跨进程丢记录和同局重复 attempt 问题。保留
默认离线、单机、本地 SQLite 的产品边界；没有真实需求的玩法和发行建议不冒充
当前缺陷。

## 范围

- 本地读写全部移出 pygame 线程，SQLite 写锁不能冻结画面；
- 统一成绩规范化、幂等 payload 和类型化保存/存储状态；
- 版本化、逐请求、跨进程安全的 durable spool 与 quarantine；
- schema v2 的 stable attempt UUID、revision、profile/mode/ruleset/status；
- stale revision、旧 submission ID、个人最佳 rank 和 tie 时间；
- 外部/内嵌旧库逐行容错、唯一备份和事务迁移；
- QUIT、Esc、鼠标返回、R/重置使用同一未保存保护；
- core/api/dev 可选依赖和中立 `GameDataService` 协议；
- 锁竞争、多进程、强制退出、坏数据和迁移失败验证。

## 非目标

- 不建设账号、云端排行、匹配、反作弊、遥测或公网 API；
- 不默认增加 progress/save slot/settings，功能有 UI 和迁移设计后再建表；
- 不把中途放弃算作 attempt；当前统计口径是已结算成绩；
- 不在本轮加入音效、IME、手柄、缩放、编辑器、撤销或新游戏；
- 不擅自选择 LICENSE，不提供会删除/覆盖用户历史的导入清理 UI；
- 不为可选 LAN 调试建设复杂鉴权，默认仍只监听 loopback。

## 关键决策

1. `LocalWriteWorker` 是桌面 I/O 的唯一执行线程；异步入口立即返回真实 Future。
2. spool 使用 `pending/<request_id>.json`，每个文件先完整写入、fsync，再排他发布。
3. `ScoreMutation` 是本地、spool 和 HTTP 的共同请求模型；非法数据不进入磁盘队列。
4. 新局开始生成 attempt UUID；新状态递增 revision；低 revision 永不覆盖高 revision。
5. `submission_id` 只保留兼容性，显式 stale/mismatch 必须返回 404/409。
6. 排榜单位是 profile 的个人最佳；达到最佳分数的时间与普通 attempt 更新时间分开。
7. schema v2 只保留实际接入的 attempts 和 request receipts；回执保留 180 天。
8. `completed` 表示成绩已结算，不等同于胜利；`practice` 不进入默认最佳。
9. core 只依赖 pygame；Flask/requests 由 `[api]` extra 安装并延迟导入。

## 验收标准

- F01–F19 每条都有修复、部分采纳或带证据的反驳；
- 持有 SQLite 写锁时，`submit_score_async()` 调用 p99 不高于 2 ms；
- 锁冲突返回 durable pending，重启和强制退出后可以恢复；
- 32 进程并发 spool 不丢记录、不覆盖不同 payload；
- malformed/unsupported/hash mismatch 单项隔离且不阻断好记录；
- 同一 2048 milestone/final/replay 最终只有一条 attempt；
- stale revision、stale/mismatched submission ID、rank/tie 均有确定性测试；
- QUIT、Esc、鼠标按钮和重置遵循同一未保存确认；
- core 导入不需要 Flask/requests，wheel 能构建；
- 原有 107 项回归、第四轮边界套件、固定 seed 压力、静态检查全部通过；
- 测试不改动仓库旧成绩库；完成后只提交本次相关变更并推送 GitHub。
