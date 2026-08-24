# 第九次审查修复记录

## 状态

- [x] 读取第九次任务书并核对 F01–F28；
- [x] 建立 P0–P3 共 132 项逐条优化矩阵；
- [x] 完成状态 receipt/CAS、幂等重放、持久逻辑时钟和 `SUPERSEDED` 模型；
- [x] 完成状态 getter 去 I/O、按 key 重建、v1 备份/规范迁移和隔离统计；
- [x] 完成启动档案状态机、queued launch 绑定、重试与显式 guest 选择；
- [x] 完成 2048 存档不变量、隔离确认和非阻塞 load 链；
- [x] 完成 score receipt 过期语义、schema v6 CHECK 和双 outbox 健康状态；
- [x] 将 gameplay 子进程纳入 coverage，并清理未使用的 pytest 依赖；
- [x] 完成第一轮独立复查并重新验证；
- [x] 完成第二轮独立复查并重新验证；
- [ ] 更新提交、推送并核验远端 CI。

## 初版验证

- 初版新增第九轮 13 项定向用例；完整 storage discover 共 103 项通过；
- `run_tests.sh`：107 项功能检查、103 项存储/生命周期用例、固定 seed 20,000 步全部通过；
- 240 次 SQLite 并发写入后 `integrity_check=ok`；100 次资源循环 FD 19→19；
- 渲染 p95：Tetris 2.258 ms、Snake 2.003 ms、2048 1.378 ms、Sokoban 1.508 ms、
  Zuma 4.035 ms；本机保存 p99 2.087 ms，持锁异步提交 p99 0.035 ms。

## 第一轮复查

- 发现最初把跨进程持久时钟分配放在状态提交入口，极端锁竞争会阻塞游戏输入。修订分配已移到
  write worker；入口只做进程内单调编号，重试沿用已经分配的 revision。
- schema v6 的新 CHECK 让旧 profile 碰撞 fixture 暴露了迁移顺序问题：旧状态表尚未具备新约束时，
  profile 归一化原先跳过语义合并，随后会按时间覆盖 progress。迁移现在只要求旧表具备必要列，
  先完成有效值选择/单调合并，再重建带约束的新表。
- unexpected Future 以前可能留下 `_unpublished_state` 或丢失内存 operation。完成回调现在检查异常，
  durable journal 存在则报告 durable pending，否则保留 non-durable operation 等待重试。
- 修复后 Ruff、编译、第六/第九轮 26 项定向用例和完整项目脚本重新通过。

## 第二轮复查

- 发现 CAS 不能直接淘汰晚到的 `merge_progress`：旧 set 是快照，但 merge 是单调增量，直接淘汰会
  丢失已完成关卡。schema v6 增加 `state_merge_receipts`；晚到 merge 只应用一次，业务值单调合并，
  winner revision 和更新时间不倒退，重复重放不增加 value version。
- 发现状态 getter 每次调用都可能重新安排 read-worker 查询，虽然不阻塞帧线程，仍会在 pending HUD
  下形成高频磁盘轮询。按 key 的后台刷新现在限制为每 0.5 秒一次，并保留外部 pending 可见性。
- 发现 2048 quarantine Future 失败后仍可用 N 二次确认新开，从而覆盖尚未隔离的原槽。失败现在进入
  `quarantine_failed`，只允许重试或返回；隔离确认成功后才允许新开。
- coverage runner 最初尝试把 inline `-c` 传给 coverage CLI，而 coverage 不支持该参数；runner 现在
  为每项生成临时脚本，并继承现有 PYTHONPATH。真实 coverage 模式下 107 项功能检查全部通过。
- 新增两项边界回归后，第九轮定向用例为 15 项，完整 storage discover 为 105 项。

## 最终验证

- `run_tests.sh`：107 项功能检查、105 项存储/迁移/生命周期用例全部通过；
- 固定 seed 20,000 步、240 次并发写入 `integrity_check=ok`、100 次资源循环 FD 19→19；
- 渲染 p95：Tetris 2.124 ms、Snake 2.036 ms、2048 1.386 ms、Sokoban 1.541 ms、
  Zuma 3.999 ms；本机保存 p99 2.167 ms；持锁异步提交 p99 0.030 ms；
- gameplay parallel coverage 模式 107 项通过，独立汇总已覆盖 client/game_service/server，结果 60%；
- Ruff、Python compileall、shell 语法、whitespace、28 条问题矩阵、132 条优化矩阵均通过；
- 无 build 模块依赖，使用 `pip wheel --no-deps --no-build-isolation` 成功生成 130,988 字节 wheel。
