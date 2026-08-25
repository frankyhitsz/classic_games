# 第十二次审查修复规格

## 目标

逐条核对第十二次审查的 29 条发现与 108 项建议，优先关闭可能造成导入部分成功、pending
恢复失控、拒绝回滚中断或完整备份遗漏持久化记录的问题。长期玩法和桌面发行建议仍逐项判断，
但不把未经实现、验证或权利确认的事项写成已交付。

## 本轮范围

- 导入取得维护锁后再初始化目标库；数据库、score/state pending 和 recovery evidence 使用可恢复
  的阶段事务，异常或下次启动时能够回滚到导入前状态；
- score envelope 对次数与时间设统一边界，恢复次数使用一次原子替换；state journal 与 store 共用
  int64、时间、key、arguments、ruleset、component 和 payload hash 校验；
- rejected state rollback 使用可识别的事务文件，启动扫描能恢复中断的 previous journal；失败时
  previous 进入后端 non-durable 状态而不是被遗忘；
- export 使用只读 pending snapshot，报告 source/included/omitted/complete；完整模式遇到截断、损坏
  或 unreadable journal 即失败，不因读取而升级或隔离源文件；
- archive 严格拒绝高于当前程序的数据库 schema，记录 ruleset、持久化快照边界和 recovery omissions；
  evidence 路径采用 POSIX 相对路径并拒绝 drive、ADS、保留名与 symlink parent；
- planner 在隔离目标副本中预演 pending score/state，提前发现 receipt、profile、ownership和业务
  约束冲突；导入 apply 只使用本次锁内生成并验证的计划；
- 2048 autosave 同时支持 quiet debounce 和最大脏状态时间，同一时刻只保留一个写 Future，完成后
  合并提交最新棋盘；
- release smoke 覆盖 sdist、只读工作目录和真实 user-data 初始化，并保留三平台 CI。

## 约束与非目标

- 默认仍为本地单机，不增加账号、云同步、强制联网、遥测、广告或在线竞技；
- `LICENSE` 由代码与素材权利人选择；本轮只能完善权利核对清单，不能替所有者作授权决定；
- branch required checks 是 GitHub 仓库管理设置；只有当前认证具备管理权限时才配置，不能用文档
  或一次成功 CI 冒充保护规则；
- 大型玩法、编辑器、完整多语言和平台安装包是合理产品工作，但必须有可验收实现才标记完成；
- import/replace-restore 都需要显式 `--apply`，执行前保留目标数据库和受影响 journal 的回滚副本。

## 关键设计决策

1. archive 的 `complete` 仅证明维护锁内已经持久化的数据；运行中尚在内存队列的动作不属于快照，
   CLI 会明确要求先关闭游戏。持久层扫描有任何 omission 时，默认导出失败。
2. import phase journal 是恢复协议，不是日志装饰。`PREPARED`、`DB_APPLIED`、`FILES_PUBLISHED`
   中任一未完成事务都会在下次维护操作前恢复数据库和受影响文件。
3. preview 和 apply 共用解析、语义验证与 pending 预演。独立 preview 仍只是某一时刻的报告；apply
   必须在独占锁内重新规划，不能盲信先前输出。
4. rollback 的 previous journal 必须始终有可扫描的持久副本；若文件恢复失败，后端将其保留在
   `_non_durable_state` 并暴露 recovery 状态。
5. 2048 当前采用“每档案一个 autosave、单实例所有权”策略；CAS 防止静默覆盖。多槽是可行的后续
   产品能力，但不能与并发安全缺陷混为一谈。

## 验收标准

- F01–F29 和 P0–P3 共 108 项均有成立性、处理状态及代码、测试或决策证据；
- 超大 attempt count、越界 revision/时间、损坏 pending 在 preview 阶段有界失败；
- 数据库提交后任一 pending/evidence 发布故障不会留下未知部分状态，重新运行可自动恢复；
- `.restore`/reject transaction 中断后 previous journal 会在下一次 outbox 初始化时恢复；
- 完整 export 不静默截断，不修改 active journal，manifest 能机器读取 omissions；
- 2048 连续操作仍会在最大脏状态期限内保存，且旧 Future 的失败不会丢失；
- 完成两轮独立复查，Ruff、compile、storage、gameplay、stress、release、wheel/sdist 和恢复演练通过；
- 推送后核验远端提交和本次 head 的 GitHub Actions 最终结果。
