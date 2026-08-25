# 第十二次代码审查核对结果

核对基线为 `ac45f00`。任务书列出的控制流与当时仓库一致，但它没有本地运行环境，因此部分结论
需要区分“确定缺陷”“极端但真实的恢复边界”和“产品路线”。本轮没有改动五款游戏的计分规则或
联网边界，修复集中在恢复协议、archive 完整性、2048 保存节流和发行验证。

## 29 条发现

| ID | 判断 | 处理与依据 |
|---|---|---|
| CG12-F01 | 成立 | 原顺序确实是 DB commit 后再逐文件恢复。现由 `ImportTransaction` 在 commit 前保存数据库回滚镜像、目标文件原件和 staged bytes，阶段为 `PREPARED → DB_APPLIED → FILES_PUBLISHED → COMPLETED`；异常和下次维护都可回滚。 |
| CG12-F02 | 成立 | `attempt_count` 现限制为 0–10,000，`set_attempt_count_max()` 用一次原子替换恢复，不再循环 fsync。 |
| CG12-F03 | 成立 | schema 1/2/3 都经 `PersistentStateOutbox._operation()` 与 `LocalGameStore.validate_state_operation()`，revision 限于 SQLite int64，时间限于 0–1e20，key、args、ruleset、components 与 hash 使用同一规范。 |
| CG12-F04 | 成立 | reject 使用 `.reject-*.txn` 保存 rejected hash 和 previous operation；启动和重试会完成隔离/恢复。旧实现遗留的 `.restore` 也有兼容扫描。 |
| CG12-F05 | 部分成立 | 单次 maintenance lock 只覆盖持久写，不能代表 worker 队列为空。现后端持有进程生命周期 application lease；export/preview/import/replace 取得独占 lease，游戏未关闭时直接返回 `maintenance_busy`。archive 明确只证明已持久化范围。 |
| CG12-F06 | 成立 | import/replace 先取得 application 与 maintenance 两层独占锁，再恢复中断事务、初始化或迁移 store。 |
| CG12-F07 | 成立 | score/state 只读扫描返回 `source_count/included_count/omitted_count/reasons/complete`；默认完整导出有遗漏即失败，`--allow-partial` 才允许取证。 |
| CG12-F08 | 成立 | state 单文件超限仍可隔离；累计总量超限只停止扫描、保留当前与后续合法文件，不再落入通用 quarantine 分支。 |
| CG12-F09 | 成立 | export 使用 `maintain=False/recover=False` 的 read-only snapshot，不升级旧 schema、不改文件名、不隔离坏文件。中断 import 回滚是单独的恢复协议，不算 journal 维护。 |
| CG12-F10 | 成立 | planner 将目标库复制到临时 SQLite，插入 planned rows 后实际调用 `record_mutation()` 和 `apply_state_operation()`；request/receipt、profile、ruleset、progress merge、slot ownership 等错误在 preview 阶段暴露。 |
| CG12-F11 | 成立 | 新增 `restore-replace`。只有带 `manifest.complete=true` 的当前格式 archive 可用；当前 DB 和 active journals 先备份，恢复失败自动回滚。 |
| CG12-F12 | 部分成立 | preview 返回包含 archive、table plan 和文件 hash 的 `plan_fingerprint`；apply 不接受一个可能过期的外部 plan，而是在同一独占锁内重新规划并立即执行。因此已关闭 TOCTOU 安全缺口，尚未提供可离线传递的 plan JSON。 |
| CG12-F13 | 成立 | evidence 只接受 POSIX 相对路径，拒绝反斜杠、drive、冒号/ADS、控制字符、保留名、尾随点空格和 `..`；事务层把目标 resolve 到数据库目录内并拒绝非普通文件。 |
| CG12-F14 | 成立 | recovery manifest 含完整性、source/included/omitted 和原因；preview/import 结果继续返回遗漏，replace 模式拒绝不完整 archive。 |
| CG12-F15 | 成立 | v2 reader 在 hash 校验后拒绝高于当前 `STORE_SCHEMA_VERSION` 的 archive，旧语义不会仅因 JSON 字段仍可解析而被接受。 |
| CG12-F16 | 部分成立 | 审查忽略了已有 owner/epoch 校验。现增加 pygame-free 的 2048 棋盘、终局、attempt 和 owner validator，game restore 与 archive import 共用；其他游戏还没有版本化 slot schema。 |
| CG12-F17 | 成立，未重写容器 | 128 MiB JSON 的多份内存峰值真实存在。现有总字节、节点、深度、字符串、表、pending 和 evidence 配额保证有界；流式/zip 格式会改变 archive 协议，保留到下一 schema，而不是在本轮冒险替换。 |
| CG12-F18 | 成立 | reject 文件恢复失败时，previous operation 留在 `_non_durable_state`；释放空间后 retry 先重放 transaction marker。rejected 与 previous 各有独立持久证据。 |
| CG12-F19 | 成立 | score worker 现在为 maintenance timeout/取消/未预期异常注册恢复 callback；若尚无 spool，mutation 与 occurred_at 进入 `_non_durable` 并产生可见 SaveEvent。 |
| CG12-F20 | 成立 | 普通写等待从 300 秒降到 5 秒，失败转 recovery 状态；close 仍给 writer 10 秒，正常退出不再可能被 maintenance gate 拖住 300 秒。 |
| CG12-F21 | 成立 | 2048 同时使用 150 ms quiet debounce 与 1.5 s max dirty age；暂停、终局、返回和 released owner 仍立即请求 flush。 |
| CG12-F22 | 成立 | 同一 autosave 只保留一个 in-flight Future；新棋盘合并到 dirty buffer，Future 完成后提交最新状态，前一 Future 的错误仍被轮询。 |
| CG12-F23 | 不是现有 bug | 当前产品策略明确为“每档案一个 autosave、单活动 owner”。CAS 的目标是阻止静默覆盖，不是声称支持多个长局。多槽仍是合理产品任务，不能以现有单槽没有数据安全缺陷为由标成已实现。 |
| CG12-F24 | 成立 | `status` 直接检查 sqlite schema/tables，旧库会返回 `schema_version`、`supported_schema_version`、`migration_needed` 和 `missing_tables`，不会为了查看状态而迁移。 |
| CG12-F25 | 成立 | maintenance 不再截断前 1,000 个 protected component；全部 ID 进入临时表，清理查询用 `NOT EXISTS`，同时避开 SQLite 变量数量上限。 |
| CG12-F26 | 成立，未伪装完成 | CI 成功不等于 required checks。本项需要仓库管理权限；最终推送后会再次检查设置。没有权限时保留为明确的仓库配置项。 |
| CG12-F27 | 部分成立 | wheel+sdist user-data smoke 已加入三平台 job，release 产出实际安装包清单并与 SBOM 同时归档。`constraints-release.txt` 仍是精确版本闭包而非跨平台 hash lock，文档明确该限制。 |
| CG12-F28 | 成立 | 安装测试从只读 cwd 导入包，通过 `GAMES_DATA_DIR` 在安装目录之外首次创建用户数据库，并验证 data console entry point；不依赖仓库 cwd。 |
| CG12-F29 | 成立，需权利人 | `NOTICE.md` 与治理清单已覆盖代码贡献、素材、名称和商标核对，但选择 `LICENSE` 会授予权利，只能由权利人决定。未擅自添加许可证。 |

## 实现结果

### 导入与恢复

- `game_service/import_transaction.py` 将数据库和文件发布串为可恢复阶段事务；事务目录只位于目标
  数据目录，journal 不完整时不会继续执行未知操作；
- merge import 保持“完全重复跳过、身份或语义冲突整次拒绝”；replace restore 则先以空的当前
  schema 规划，恢复后重新建立 state baseline；
- 每次成功 import/replace 仍留下普通数据库 backup。事务内部回滚镜像只在操作结束后删除；
- `status` 不迁移旧库，export/preview/import 会先回滚上次异常退出留下的阶段事务。

### Pending 与 archive

- score/state export 不再调用会 upgrade/quarantine 的正式 replay scanner；
- archive manifest 记录应用 ID、五个 ruleset、持久快照边界、pending/recovery 完整性和 hash；
- partial archive 可用于取证和严格 merge，但不能用于 replace restore；
- evidence 始终恢复到 `imported-recovery/<archive-id>/`，不会伪装成 active journal。

### 2048 与发行

- 2048 最大脏状态期限和单 in-flight 控制器关闭了连续操作延期与 Future 覆盖；当前仍是单 autosave
  策略；
- wheel smoke 同时构建、安装 wheel 与 sdist，并从只读 cwd 创建隔离 user-data；
- CI 的 Ubuntu、macOS、Windows Python 3.11 job 都执行 package smoke；release job归档 SBOM、
  JUnit/JSON 和 installed package manifest。

## 没有强行完成的事项

流式 archive、新的多游戏 slot registry、hash lock、完整数据管理 GUI、模块大拆分、输入/音频/DPI
框架、玩法模式、编辑器和三平台原生安装包都属合理工作。本轮矩阵逐项保留了验收条件；没有因为它们
是产品功能而否定，也没有用占位按钮、空类或文档宣称完成。`LICENSE` 和 required checks 分别受
权利确认与仓库管理权限约束，状态单独列出。

## 本地验证

- storage：161 项通过；新增用例覆盖阶段 crash、post-commit 文件失败、journal hash 篡改、partial
  export、replace restore、路径穿越、配额和 autosave 并发；
- gameplay：107 项 headless 检查通过，五款游戏、launcher、API 和视觉边界未回退；
- stress：固定 seed 20,000 步、240 次并发写、100 次资源循环通过，FD 19→19，SQLite
  `integrity_check=ok`；渲染 p95 最高为 Zuma 4.932 ms，本机保存 p99 4.984 ms；
- release：Ruff、dependency audit、CycloneDX SBOM、compile、wheel+sdist smoke、storage、stress、
  gameplay 八阶段通过；pip-audit 未发现当前约束中的已知漏洞。
