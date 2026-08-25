# 本机存储协议

## 写入边界

桌面程序和可选 HTTP 适配器都必须先取得 application lease，再初始化 `LocalGameStore`。普通写入
由 `LocalBackendClient` 完成，并在 shared maintenance lock 内把意图先写入 score/state journal，
再提交 SQLite。数据维护命令取得 exclusive application lease 和 maintenance lock 后才允许读取、
导入或替换。

`LocalGameStore` 是存储实现，不是供桌面功能绕过协议直接调用的入口。维护脚本应使用
`game_service.data_cli`，运行时功能应使用 `GameDataService`/`LocalBackendClient`。测试可以直接构造
store 来验证 schema 和业务约束，但不能据此认定生产调用可以跳过 lease。

## 恢复顺序

普通客户端和 HTTP 服务在打开数据库前扫描 `.<database>.import-*`。可验证的未完成事务自动回滚；
journal、阶段文件或数据库镜像的 hash 不匹配时停止启动，保留原目录，并通过启动页或
`classic-games-data transactions` 提示处理。恢复不会在验证失败后继续打开可能处于中间状态的库。

新 import transaction 使用 v2 journal。数据库回滚镜像、每个 staged 文件和原目标 before image
都记录 size/sha256；发布前还会确认目标仍与 prepare 时相同。事务目标仅允许 active score/state
journal、旧版 `pending_saves.json` 的删除，以及 `imported-recovery/<archive-id>/` evidence。

## Archive v2

Archive v2 是有 128 MiB 上限的 canonical JSON。当前 manifest format 为 2，校验 application ID、
当前 ruleset 映射、表与 pending 计数、complete 关系及 recovery evidence 的 path/size/hash。
旧的 v2 manifest 仍可作为 merge 输入，但不能授权 destructive replace。

普通 `import` 只合并不存在的数据，任何 identity/semantic 冲突都会拒绝整次操作。
`restore-replace` 只接受完整的当前 manifest；它清空当前产品表、未知旧表及完整 active journal
namespace，再写入 archive 内容。执行前生成的 SQLite backup 和 transaction before image 是恢复证据，
不会混入恢复后的 active pending。

Archive v3 暂不实现。本轮继续使用有硬上限的 v2，避免在没有确定随机访问、流式 hash、兼容和
恢复需求前同时维护两种不完整协议。若实际 128 MiB benchmark 显示峰值不可接受，v3 应采用流式
记录、逐项 hash 和可恢复索引；不能给 v2 继续增加可选字段来模拟流式格式。

## 保留与清理

`status` 输出 recovery 项的名称、类型、字节数和修改时间；`transactions` 另外显示 phase、版本和
operation 数。事务可先用 `export-transaction` 保存为 no-follow JSON evidence，再用
`recover-transactions --apply` 重试安全回滚。

当前不自动删除 backup、quarantine 或 imported evidence。它们可能是唯一恢复副本，只有在完整导出
并由用户明确选择后才能加入清理流程。数据库幂等回执按既有 180 天策略清理；游戏历史不自动删除。

## 已冻结的协议面

以下格式的兼容修改必须增加版本并带迁移测试：SQLite schema、score spool envelope、state journal、
import transaction journal、archive manifest。游戏计分或完成条件变化则增加对应
`ruleset_version`，不改写历史记录的规则身份。
