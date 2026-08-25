# 更新记录

## 0.7.0

- 启动恢复将 exclusive application lease 原子交接为 shared session，不再在 import recovery 与数据库
  打开之间释放 application gate；Windows control file 使用 reparse-point-aware open。
- state reject transaction 升级为 prepare-before-replace v3；完整临时 marker 可继续恢复，SQLite 永久
  拒绝后始终能找回上一条 pending。
- 2048 owner claim 必须由权威 token/epoch 回执确认；superseded 或 recovery-required 不开放输入，退出
  中的 claim 由更高 revision 的 released intent 取消。
- archive 升级到 v3，记录包版本与 reader contract，允许历史 ruleset；增加安全 v2 upgrader，replace
  会重建并核对 table/index/trigger/view schema fingerprint。
- ImportTransaction 直接使用校验返回的 bytes；未认证 v1 默认人工处理，多份未完成 transaction 不再
  按目录名猜测 rollback 顺序。
- 推箱子练习与 ranked campaign ledger 分离，练习不再解锁后续关卡；N 和选关快捷键统一遵守已解锁范围。
- 包版本升为 0.7.0；单游戏入口复用 launcher 的恢复页，`server.init_db()` 纳入 application lease。

- 普通客户端与 Flask 在打开数据库前恢复中断导入；import transaction v2 为 rollback image、
  staged 和 before 内容加 hash，并限制目标 namespace、符号链接和发布竞态。
- v2 compatibility reader 校验应用、规则、计数和 completeness；recovery 扫描 no-follow，
  replace restore 清理完整 active journal namespace。
- 2048 在 owner claim ACK 前保持输入门禁，关闭时同步发布 durable release intent；旧的 in-flight
  保存不能再覆盖较新的退出状态。
- 俄罗斯方块采用 7-bag，并增加落点影子和每块一次的保留交换；规则版本升为
  `tetris-assist-3`。推箱子可用 `[/]`、PageUp/PageDown 或数字键选择已解锁练习关。
- 数据工具增加事务查看、证据导出、显式恢复和带完整 archive 证明的 recovery 清理；release gate
  对照安装清单/SBOM，并拒绝未登记的运行时素材。
- 数据库升级为 schema v7，state receipt 绑定业务值 hash；旧库为已有档案、设置、进度和存档
  建立基线，损坏回执可从业务行修复，业务行隔离时同步失效回执。
- state journal 升级为 schema 3，单调进度按 component 幂等；聚合使用独立 ID，不再因
  commit-before-unlink 后的晚到贡献改变 payload hash 而丢失进度。
- state clock 损坏时保留原件并从数据库高水位恢复；merge component 回执加入索引、保留期和
  pending 保护。
- 2048 终局存档恢复原棋盘和结果页；自动存档 schema 4 使用 owner token，支持显式接管并拒绝
  旧实例的延迟覆盖。
- 修复 HTTP 调试启动器复用本机 default profile、延迟成绩重放刷新档案时间，以及推箱子/祖玛
  写入进度后 HUD 未读取权威值的问题。
- 增加本机数据状态、导出、导入预览与原子导入命令，以及统一 release 检查、依赖审计和机器结果文件。
- 数据库升级为 schema v6：每个本机状态键在同一事务保存胜出 revision、operation ID 和回执；
  旧进程不能在新 journal 删除后把旧 setting、档案、进度或存档写回。
- 状态重放改为幂等操作，保留原发生时间；被较新操作淘汰时报告 `SUPERSEDED`，不再冒充提交成功。
- 晚到的单调 progress merge 会在独立 operation receipt 保护下合并一次，不丢成就也不重复加版本。
- state journal 使用后台分配的跨进程持久逻辑时钟；v1 升级保留原字节并迁到固定 ruleset 的规范 key。
- 启动档案读取未完成时只排队游戏；失败后可重试，或按 G 明确选择 guest，不再静默竞争身份。
- 2048 补全 won/死局不变量，隔离完成前不显示成功；slot load 不再占用单写 worker。
- score receipt 过期后从 attempt 语义重建；状态查询不再在 pygame 帧线程读取文件或 SQLite。
- 本机状态日志升级为 schema 2：同 key 跨进程加锁、按 logical revision 排序、CAS 删除，
  并冻结 ruleset；旧 schema 1 日志可原子升级。
- 日志与 SQLite 同时不可写时保留最新内存状态，退出前提示；后台补写通过
  `LocalStateEvent` 回传最终结果。
- 2048 自动存档升级为 schema 3，不再持久化数据库 row ID；临时读取失败、超时和语义损坏
  保持输入门禁，并提供重试或确认新开。
- 档案异步操作和排队启动加入 generation/profile token；guest 与 anonymous 统一为默认身份。
- 状态表快速检查增加 PK/UNIQUE 与外键完整性；档案归一化碰撞会合并有效子状态并保留证据。

## 0.6.0

- 待保存成绩升级为 schema 2，旧版显示名档案会保留原件并转换为稳定 UUID。
- 数据库升级为 schema v5，显式迁移旧设置、进度和存档表，并隔离损坏 JSON。
- Windows 与 POSIX 请求互斥改用操作系统文件锁，不再探测或向进程发送信号。
- 档案、设置、进度和存档增加最新值日志；数据库暂时被锁时可在之后补写。
- 推箱子和祖玛进度按规则版本隔离并做单调合并；启动器等待档案确认后再启动游戏。
- 2048 在存档读完前屏蔽移动，存档携带 attempt 身份，终局棋盘不再恢复为进行中。

## 0.5.0

- 本地数据 schema 升至 v4，增加行约束、失效回执索引和分批清理。
- 磁盘满、只读、I/O、锁和损坏库按 SQLite 错误码分类；存储失败默认保留待保存记录。
- 严格解析旧 pending 和旧附加信息，限制非有限数、原始大小、嵌套深度和节点数。
- 保存状态支持从待处理回写为已完成、需恢复或已隔离；手动重试和首次目录扫描不阻塞界面。
- 本机档案改用独立 UUID，并持久化昵称、设置、关卡进度和 2048 自动存档。
- 启动器支持系统输入法组合输入；并列名次使用相同奖牌，最近记录不再显示竞技奖牌。
- 增加 Linux、macOS、Windows 的 GitHub Actions 检查。

## 0.4.0

- 本地记录改用 attempt/revision 模型，加入规则版本和持久化待保存目录。
- Flask 保留为可选的本机调试适配器。
