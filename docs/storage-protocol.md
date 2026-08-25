# 本机存储协议

## 写入边界

桌面程序和可选 HTTP 适配器都必须先取得 application lease，再初始化 `LocalGameStore`。启动恢复持有
exclusive application lease；POSIX 用独立 transition gate 包住可能非原子的 `flock` EX→SH 转换，并在
shared lease 下再次扫描 transaction root；Windows 使用 byte 0 作为交接 gate、1–255 作为应用 session
slot。数据维护命令取得
exclusive application lease 和 maintenance lock 后才允许读取、导入或替换。

`LocalGameStore` 是存储实现，不是供桌面功能绕过协议直接调用的入口。维护脚本应使用
`game_service.data_cli`，运行时功能应使用 `GameDataService`/`LocalBackendClient`。测试可以直接构造
store 来验证 schema 和业务约束，但不能据此认定生产调用可以跳过 lease。`server.init_db()` 也会为返回
的 store 保留 lifetime lease；单独运行某款游戏与 launcher 共用同一个恢复/重试页。

## 恢复顺序

普通客户端和 HTTP 服务在打开数据库前清理未发布的 `.<database>.preparing-*`，并扫描已发布的
`.<database>.import-*`。已发布目录缺失 journal 时绝不递归删除；启动停止并保留 rollback image。可验证的
未完成事务自动回滚；
journal、阶段文件或数据库镜像的 hash 不匹配时停止启动，保留原目录，并通过启动页或
`classic-games-data transactions` 提示处理。恢复不会在验证失败后继续打开可能处于中间状态的库。

普通 import transaction 使用 v2 journal。数据库回滚镜像、每个 staged 文件和原目标 before image
都记录 size/sha256；校验函数返回的同一批 bytes 直接用于发布或 rollback，不再按路径重新读取。发布前
还会确认目标仍与 prepare 时相同。事务目标仅允许 active score/state journal、旧版 pending/migrated
evidence 的删除，以及 `imported-recovery/<archive-id>/` evidence。

transaction v1 没有内容摘要，普通启动默认停止；CLI 必须先导出 evidence，再提交 evidence 文件及其
SHA-256 才允许 legacy rollback。发现多份未完成 transaction 时没有可信 lineage，恢复器停止而不是按
目录名猜顺序。灾难 replace 遇到非 SQLite 目标时使用带 hash 的 transaction v3 raw rollback image；
正常 import 不升级格式。

state journal 在替换旧 winner 前写 reject v3 `prepared` marker。SQLite 永久拒绝 incoming 时先把 marker
改为 `rejected`，再隔离 incoming 并恢复 previous。启动时完整 `.tmp` 会提升为 final；只有 hash 或结构
无效的临时文件才隔离。canonical 缺失或损坏时先恢复 marker 中的 previous，不把 `current=None` 当作提交
证明。普通 score/state/clock temp 经过 grace window 后按 request/key lock 提升或合并；新 temp 保留并
阻止 complete export，避免与仍在写入的进程竞争。

## Archive v3

Archive v3 是有 128 MiB 上限的 canonical JSON。manifest format 3 校验 application ID/包版本、导出时
ruleset 目录、reader min/max/capability、表与 pending 计数、complete 关系及 recovery evidence 的
path/size/hash。reader 按 archive 自身 format dispatch，不拿未来程序的当前 format 与旧 archive 的
`max_version` 比较。ruleset catalog 可以是历史子集；当前 ruleset 行严格校验，未知历史版本只以
preserve-only 方式保留，默认排行榜仍只查询当前 ruleset。

`upgrade-archive` 可把严格 manifest format 2 的 v2 archive 重写为 v3，并保留其可证明的 complete 状态。
format-less v2 没有 active reject/restore inventory，归档自身无法证明当时没有遗漏，因此升级结果明确为
merge-only。这是证据边界，不允许通过补一个字段伪造成完整备份。

普通 `import` 只合并不存在的数据，任何 identity/semantic 冲突都会拒绝整次操作。
`restore-replace` 只接受完整 v3 manifest；它在旁路构建全新 current-schema DB，导入后检查 foreign key、
quick check 和完整 schema fingerprint，再在 exclusive locks 内原子替换目标。健康库使用 SQLite rollback
image，坏库使用 authenticated raw image；旧数据库和 sidecar 另存为 evidence，不会混入 active pending。

v3 仍沿用有硬上限的 canonical JSON，不假称流式格式。若 128 MiB 上限内的实测峰值不可接受，下一版
必须另行设计逐项 hash、随机访问索引和中断恢复，不能向已发布格式继续堆可选字段。

## 保留与清理

`status` 输出 recovery 项的名称、类型、字节数和修改时间；`transactions` 另外显示 phase、版本和
operation 数。事务可先用 `export-transaction` 保存为 no-follow JSON evidence，再用
`recover-transactions --apply` 重试安全回滚；v1 另需 `--allow-legacy-v1`、`--evidence` 和导出命令返回的
`--evidence-sha256`。

当前不自动删除 backup、quarantine 或 imported evidence。它们可能是唯一恢复副本，只有在完整导出
并由用户明确选择后才能加入清理流程。数据库幂等回执按既有 180 天策略清理；游戏历史不自动删除。

## 已冻结的协议面

以下格式的兼容修改必须增加版本并带迁移测试：SQLite schema、score spool envelope、state journal、
import transaction journal、archive manifest。游戏计分或完成条件变化则增加对应
`ruleset_version`，不改写历史记录的规则身份。
