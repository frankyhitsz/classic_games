# 第十四次代码审查答复

本轮按当前仓库、自动化测试和平台 CI 逐条核对 28 条 finding。结论没有照单全收：恢复交接、reject
previous、2048 claim、ruleset 兼容和 Sokoban practice 等控制流判断成立；“format-less v2 可恢复为
完整 replace archive”不具备足够证据，不能通过补字段实现；fresh-DB replacement 采用等价的完整 schema
重建与 fingerprint 验证；仓库保护、三平台 hash lock 和许可证仍属于外部交付条件。

## Finding 结论

| Finding | 判断 | 处理与证据 |
| --- | --- | --- |
| F01 recovery→shared lease 交接竞态 | 成立 | exclusive application lease 在 recovery 后原子降级为 shared session；不再调用 `ApplicationSession.acquire()` 重新开锁。POSIX 用同一 flock 描述符转换，Windows 用 byte-0 transition gate。 |
| F02 rejected previous 在 marker 前丢失 | 成立 | reject v3 在 canonical replace 前写 `prepared(previous,incoming)`；永久拒绝先写 `rejected` phase，再隔离 incoming、恢复 previous。 |
| F03 valid reject temp 被隔离 | 成立 | `.tmp` 先做结构/hash 校验；无 final 时提升，有 prepared final 而 temp 为 rejected 时覆盖提升；半写或冲突 temp 才隔离。 |
| F04 superseded claim 被当成功 | 成立 | claiming 只在回执 `value.owner_token/owner_epoch/owner_status` 与当前窗口匹配时进入 ready；superseded 重新读取并保持输入门禁。 |
| F05 claim SaveState 不完整 | 成立 | COMMITTED、DURABLE_PENDING、NON_DURABLE_PENDING、SUPERSEDED、RECOVERY_REQUIRED、PERMANENT_FAILURE、QUARANTINED 均有明确转移和提示。 |
| F06 上一版 v2 不能 replace | 部分成立，原要求需收窄 | `upgrade-archive` 可把严格 format 2 v2 升为可 replace v3；format-less v2 从未盘点 reject/restore artifact，归档自身无法证明 complete，只能升级为 merge-only 并报告缺失证据。 |
| F07 manifest ruleset 全等 | 成立 | manifest 保存导出时 ruleset 目录但不与当前目录全等；行按自身 ruleset 校验和恢复，默认排行榜仍按当前 ruleset 过滤。 |
| F08 v1 transaction 自动使用未认证 bytes | 成立 | 自动启动拒绝 v1；先 `export-transaction`，人工核对后才可加 `--allow-legacy-v1`。 |
| F09 hash verify 后重新读路径 | 成立 | staged、before、rollback DB 由同一 descriptor 读取、fstat、hash，返回的同一 bytes 直接发布或写入已验证临时 DB。 |
| F10 多 transaction 无 lineage | 成立 | 默认只自动处理一份未完成 transaction；多份时 recovery-required，不按文件名猜 rollback 顺序。 |
| F11 Windows reparse no-follow | 成立 | control file 使用 `CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)` 并检查 `FileAttributeTagInfo`；CI 包含 junction 用例。Windows ACL 精细策略仍列为发行加固项。 |
| F12 outbox root 可为 alias | 成立 | pending/state/quarantine/migration root 在使用前检查 directory、symlink/reparse、POSIX owner 与 group/world write；alias root 直接拒绝。 |
| F13 migrated legacy evidence 漏 inventory | 成立 | `pending_saves.json.migrated-*` 纳入 status/export/cleanup/replace，来源标为 `legacy_score_migration`。 |
| F14 无效 legacy restore 静默保留 | 成立 | 无效 restore 和与当前 winner 冲突的 restore 移入 state quarantine 并更新恢复提示。 |
| F15 replace 未清理全部 schema object | 成立，采用等价实现 | 删除未知 table/view/trigger/index，清空 sequence，重建当前显式对象并比较空白当前库 schema fingerprint；旧库整体 backup 和 import rollback image 保留。 |
| F16 archive 无 app/reader 版本 | 成立 | archive/manifest 升到 v3，写入包版本 0.7.0、reader min/max 和 required capabilities；v2 冻结为只读兼容输入。 |
| F17 practice clear 提高 unlock | 成立 | practice 使用独立 completed/score/move/push ledger，不再写或修改 campaign unlocked level。 |
| F18 paused/won 可选关 | 成立 | selector 仅在 playing 接受；win 的 N 走明确 advance，paused/won 的选关键不直接载入关卡。 |
| F19 练习第 1 关重置 ranked run | 成立 | `load_level(..., practice=True, new_campaign=False)` 不调用 `begin_score_session()`，不清空 campaign ledger；有移动时首次选关只显示二次确认。 |
| F20 N 与 selector 规则冲突 | 成立 | 选择“练习也受 campaign unlock 约束”；N 与 `[/]`、PageUp/PageDown、数字键使用同一范围。 |
| F21 claim event 状态集合 | 与 F05 重复且成立 | 共用显式 SaveState 分支；恢复状态不挂起也不误开放。 |
| F22 pending claim 关闭留下 active owner | 成立 | `before_close()` 在 claiming/ready 都同步发布更高 revision 的 released intent；旧 claim 无法重新越过。 |
| F23 standalone 无恢复页 | 成立 | recovery UI 下沉到 `client.common.ui`；launcher 和 BaseGame 无 backend 的单游戏入口复用。 |
| F24 `server.init_db()` 绕过 lease | 成立 | helper 先 recovery，再把 lifetime application session 绑定到返回 store 并用 finalizer 释放。 |
| F25 项目版本仍为 0.6.0 | 成立 | pyproject/package 升为 0.7.0，CHANGELOG 分版，archive 写 package version；本轮不擅自创建 Release tag。 |
| F26 main required checks | 成立，外部设置未完成 | workflow 已含 release/core/三系统/Python 3.11–3.13；branch protection 是仓库管理设置，不能用 CI 成功冒充已开启。 |
| F27 无 hash/per-platform lock | 成立，未完成 | 精确 constraints、依赖审计、SBOM 与 installed-manifest gate 保留；尚无三平台 `--require-hashes` 锁，不能用单平台下载结果冒充。 |
| F28 LICENSE | 成立，需权利人 | NOTICE 素材门禁继续生效；许可证授予、名称和素材权利不能由代码修改代替。 |

## 本轮额外验证

- 发现 practice 写入曾共用 campaign generation，可能使尚未返回的 campaign load 被当成过期；已改为
  campaign/practice 独立 generation，并增加交错回归。
- 玩法回归原先断言 N 可跳过未解锁关；按本轮明确选择的产品规则，测试改为先解锁第 2 关，再验证 N
  进入 practice。未通过删测试隐藏行为变化。
- format-less v2 的限制是信息论边界：未写入 archive 的文件是否存在无法从 archive 推导，因而没有把
  `complete=false` 改成 `true` 来满足表面验收。
