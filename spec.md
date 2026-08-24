# 第八次审查修复规格

## 目标

核对第八次审查提出的本机状态一致性问题，使多进程 profile、setting、progress 和 save-slot
写入具有确定顺序、可恢复的最终状态和可见故障；2048 在不能确认自动存档读取结果时不得
静默开始或覆盖旧槽。

## 范围

- state journal schema v2：每个语义 key 使用 OS 文件锁、logical revision、hash CAS、显式
  ruleset、目录 fsync、隔离通知和有界扫描；
- journal 与 SQLite 同时失败时保留按 key 最新的内存 operation，退出前按持久性提示；
- Sokoban/Zuma progress 使用各自字段 schema 和单调合并，UI 以 generation 拒绝晚到 read；
- profile startup/save/list 和 queued launch 使用 generation 与预期 profile token；默认 guest 与
  legacy anonymous 使用同一稳定身份；
- SQLite current-schema 检查状态表 PK/UNIQUE 与外键完整性；profile 归一化碰撞合并子状态并
  在 `invalid_local_state` 留证；正常读取不取写锁；
- 2048 使用 typed slot load、超时/重试/二次确认门禁、语义坏槽隔离和 slot schema v3；稳定
  attempt UUID 替代持久化 SQLite row ID；
- 本机状态通过 `LocalStateEvent` 报告最终结果，成绩 committed 状态可从 durable receipt 重建；
- CI 设置并发取消、job 超时并执行第八轮故障与迁移用例。

## 非目标与约束

- 不改变五款游戏现有 ruleset、计分或关卡内容；
- 不增加账号、云同步、遥测或公网服务；
- 不在没有权利来源确认时替仓库所有者选择 LICENSE；
- 不按文件年龄删除跨进程锁 inode。该做法会让已经打开旧 inode 的 waiter 与新建 inode
  同时获得“同一 key”的锁；清理必须先设计目录级代际协议；
- GitHub required checks、签名、公证和安装器需要远端权限或真实平台验收，代码结果不能冒充
  仓库设置或发行证明。

## 关键决策

1. state operation 在提交到 worker 前取得 `(logical_revision, operation_id)`；setting/profile/slot
   使用 last-write ordering，progress 无论到达顺序都做 schema-aware merge。
2. put、读取重写、隔离与 remove-if-current 使用由 key 摘要确定的同一 OS 锁。锁文件使用稳定
   inode，文件内容不表示进程存活。
3. state envelope v1 先核对原 hash，再按当前兼容 catalog 冻结旧 operation 的 ruleset并原子
   重写为 v2。现有规则升级必须保留旧 catalog 版本，不能让 pending 漂移。
4. journal publish 成功后 operation 已可安全退出；publish 与数据库都失败时进入内存队列并触发
   二次确认门禁，后台重试会先恢复 journal 再写库。
5. profile 迁移碰撞只从有效 JSON 中选值：progress 单调合并，setting 取较新有效值，slot 优先
   `slot_revision`；损坏和被舍弃原文均进入隔离表。
6. 2048 slot v3 保存 attempt UUID、attempt revision、slot revision、确认分数和棋盘，不保存
   SQLite row ID。v2 row ID 只作为废弃字段读取并丢弃。
7. slot 临时失败、profile pending、超时和语义损坏都保持玩法门禁。只有明确 no-slot 或用户二次
   确认新开后才能写 autosave。

## 验收标准

- F01–F26 逐项有代码证据、明确反驳或仍受外部条件约束的状态；
- 旧 worker 不能删除/覆盖较新 journal；另一进程持同 key lock 时写入必须等待；
- progress 多实例写入不回退，v1 journal 升级后 ruleset 固定；
- journal 与数据库同时失败时退出保护生效，恢复后 setting/slot/progress 可读；
- 临时 slot read 不等于 no-slot；2048 读取超时或损坏时输入和自动写入保持关闭；
- 改名后的 2048 revision 继续写入同一 attempt，v2 row ID 不进入 v3 slot；
- 畸形 v5 PK 会备份重建；guest/anonymous 子状态碰撞不静默丢失有效值；
- 两轮独立复查完成，每轮发现的问题均修复并重新验证；
- 功能、存储、迁移、压力、Ruff、编译、构建和默认数据库指纹检查通过。
