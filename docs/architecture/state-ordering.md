# 本机状态顺序与恢复规则

## 顺序

界面线程在用户动作发生时记录 `updated_at`，并生成进程内递增的候选 revision。写线程取得
跨进程文件锁后，把候选值推进到持久时钟的下一个值。这样文件锁和 fsync 不会阻塞 pygame
输入；跨进程竞争以实际取得持久写序的先后为准，业务发生时间仍保留在 operation 中。

SQLite 使用 `(logical_revision, operation_id)` 选择 setting、profile、slot 和快照型 progress
的胜出项。数据库中的基线回执代表升级或直接仓储写入时已经存在的权威值；发生时间不晚于
基线的旧 journal 不得覆盖它。时钟文件丢失或损坏时，下一次分配同时参考数据库回执高水位，
损坏原件移入 state quarantine。

## 单调进度

`merge_progress` 不是最新值写入。每次用户贡献有自己的 component ID 和 hash；journal 聚合会
生成不同于任一 component 的 aggregate ID，并携带完整 component 列表。SQLite 对每个 component
做幂等记录。已经提交的 component 与晚到 component 聚合时，只应用尚未记录的贡献，不会因
aggregate hash 改变而丢弃 journal。

## 回执与业务行

state receipt 保存业务引用和权威值 hash。返回 duplicate 前必须确认业务行存在、可解析且 hash
一致。业务行缺失时回执失效，journal 可重建该行；回执 JSON 损坏但业务行有效时，从业务行
重建回执。setting、progress 或 slot 被隔离时，在同一事务删除相应普通回执和 merge component
回执。

直接调用仓储写接口仍被兼容，但同一事务会生成新的 baseline receipt，不再形成绕过 CAS 的
无回执状态。桌面游戏的新写入继续统一走 durable state operation。

## 2048 自动存档

终局自动存档恢复后显示原棋盘和结果页，只有玩家明确重开才创建随机棋盘。schema 4 存档带
实例 owner token：正常退出写入 `released`；异常退出留下 `active`。另一个实例遇到 active owner
时停止自动写入，并要求按 K 接管或返回。接管写入必须声明旧 owner，之后旧实例的延迟保存会被
仓储拒绝，不能静默覆盖当前局。
