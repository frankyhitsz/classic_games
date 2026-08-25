# 第十四次审查修复规格

## 目标

逐条核对第十四次审查提出的 28 条发现和 P0–P3 建议。优先关闭启动恢复交接、被拒状态的旧
pending 保全和 2048 所有权确认三个数据正确性问题；随后处理可证明的归档兼容、事务恢复、练习
模式隔离和独立启动问题。审查建议与真实平台行为冲突时，以不变量、代码和可复现测试为准。

## 本轮范围

- 导入恢复持有 exclusive application lease，并把同一 lease 原子交接为 shared session；恢复与数据库
  打开之间不再释放 application gate；
- state outbox 在替换 canonical winner 前发布 reject v3 `prepared` marker；永久拒绝先把 marker 改为
  `rejected`，完整临时 marker 可在启动时提升，损坏临时文件才隔离；
- 2048 owner claim 只有权威回执中的 token、epoch 和 active 状态匹配当前窗口才开放输入；关闭时用更高
  revision 的 released intent 取消未完成 claim；
- archive v3 记录包版本、导出时 ruleset 目录和 reader capability 范围；历史 ruleset 行可恢复但不参加
  当前规则默认排行；v2 upgrader 只在证据足够时保留 replace eligibility；
- import transaction 的 hash 校验与实际使用共用同一批 bytes；未认证 v1 默认要求人工确认，多份未完成
  transaction 在没有 lineage 时拒绝猜测顺序；
- journal、quarantine 和 migration root 拒绝符号链接及 Windows reparse point；迁移后的 legacy score 文件
  纳入 status/export/cleanup/replace inventory；
- replace restore 移除未知 table/view/trigger/index、重建当前显式 schema object、清空 sequence，并核对
  完整 schema fingerprint；
- Sokoban campaign 与 practice ledger 分离；练习不解锁关卡、不改变 ranked total，paused/won 不接受选关，
  N 与 selector 使用同一 unlocked 规则；
- launcher 与单游戏入口共用 maintenance/recovery 重试页，`server.init_db()` 持有 application lease；
- 包版本升为 0.7.0，README、CHANGELOG 和存储协议与实现同步。

## 约束与非目标

- 不通过二次无锁目录扫描冒充原子交接；协作维护必须先取得 application exclusive lease；
- format-less v2 没有 active reject/restore inventory，单凭归档内容无法证明当时不存在遗漏，因此只能升级
  为 merge-only v3，并明确报告不可证明项；
- archive 中的导出时 ruleset 目录是解释信息，不是必须等于当前目录的全局门禁；业务行仍按 game ID、
  自身 ruleset 和 payload 语义校验；
- 低层 `LocalGameStore` 继续供迁移和测试使用；正式运行入口必须走 service/lease；
- branch protection、三平台 hash lock、签名制品和 LICENSE 需要仓库管理或权利人决定，不以代码注释冒充；
- 本轮不为完成清单而重写 pygame 架构或同时引入另一套 journal。

## 验收标准

- F01–F28 均有成立性、处理状态与证据；P0–P3 每个 ID 均有状态；
- recovery handoff 无 unlock/reacquire 调用，shared session 存续期间维护 exclusive 获取失败；
- reject 在 incoming publish 前已有 previous，valid temp 可提升，所有拒绝路径可恢复 previous；
- superseded/unproven claim 不进入 ready、不接受移动，claim 中关闭留下 released winner；
- v3 archive 可 replace；严格 v2 可升级；format-less v2 明确 merge-only；ruleset 升级不使无关历史失效；
- v1 transaction 默认不自动使用未认证 bytes；多 transaction 默认 recovery-required；
- practice clear 不改变 campaign ledger/unlock；paused/won selector 无效；
- 完成至少两轮“发现问题—修复—重新验证”，并通过 lint、compile、storage、gameplay、stress 和 release；
- 变更提交并推送到 `origin/main`，最终远端多平台 CI 成功且本地与远端 SHA 一致。
