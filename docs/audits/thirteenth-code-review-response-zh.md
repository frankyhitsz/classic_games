# 第十三次代码审查答复

本轮按当前仓库和实际运行结果复核了 29 条 finding。结论不是照单全收：其中 25 条是可以在当前
实现中复现的缺口，F22/F26 属于合理的容量或 API 边界问题而非已发生的数据错误，F27–F29 还包含
仓库权限、跨平台制品和权利人决定，不能靠改一段 Python 伪装完成。

## Finding 结论

| Finding | 判断 | 处理与证据 |
| --- | --- | --- |
| F01 普通启动不恢复 import txn | 成立 | `recovered_application_session()` 在 application lease 和 DB 初始化前取得双锁并回滚；有效事务自动恢复，hash 损坏时保留目录并拒绝启动。 |
| F02 export 可覆盖锁和 legacy control | 成立 | reserved files 增加 application/maintenance lock、`pending_saves.json`、SQLite sidecar 和 import root 前缀；发布前再次校验。 |
| F03 recovery export 跟随外部 symlink | 成立 | recovery、pending snapshot 和事务证据改用 `lstat`、`O_NOFOLLOW`、inode/fstat 复核；symlink、hardlink 和特殊文件只记 omission。 |
| F04 同目录 symlink alias 可改写 active/control | 成立，但原建议的“先 resolve”不安全 | target 先做词法 containment、allowlist 和逐级 no-symlink，再核对最终解析位置；macOS `/var → /private/var` 只转换数据库父级别名，不解析数据目录内部组件。 |
| F05 complete archive 漏 active marker/legacy pending | 成立 | export 在独占锁内先迁移 legacy score、恢复 state marker，再盘点 reject tmp/txn、restore 和 legacy file；未解决项使完整导出失败。未选择 recovery 时也不再冒充 complete。 |
| F06 replace 保留旧 marker | 成立 | replace planner 删除 archive 外的 score/state JSON、reject txn/tmp、legacy restore 和 `pending_saves.json`。 |
| F07 reject marker 非原子 | 成立 | marker v2 先写唯一 `.tmp`、fsync 后 `os.replace`；marker 有 canonical hash，损坏或残留 tmp 被隔离并提示，不执行其中 previous。 |
| F08 Windows evidence 路径不自洽 | 成立 | archive 相对路径由 `PurePosixPath.as_posix()` 生成，导入继续拒绝反斜杠、drive、ADS 和保留名。 |
| F09 txn 内容不受 journal hash 保护 | 成立 | import transaction v2 为 staged、before、rollback DB 保存 size/sha256；publish/rollback 先验证全部输入，DB 同时执行 `quick_check`。 |
| F10 重复 transaction target | 成立 | 相同 target+bytes 去重；相同 target 不同 bytes 返回 `duplicate_import_target`；打开 journal 时也拒绝重复记录。 |
| F11 manifest 内部一致性不严格 | 成立 | manifest format 2 校验 application ID、当前 ruleset map、table/pending/recovery counts、component arithmetic 和 top-level complete。旧 v2 只能 merge，不能 replace。 |
| F12 Flask 不持 application lease | 成立 | production `create_app()` 先恢复再持 lifetime lease，初始化异常和 server 退出均释放；测试可显式关闭或禁用。 |
| F13 launcher 构造阶段 traceback | 成立 | launcher 捕获 maintenance/recovery 错误，显示“重试/退出”页；损坏事务不会继续打开 DB。 |
| F14 同 state identity 不同 payload 静默忽略 | 成立 | 非 progress 状态在相同 `(logical_revision, operation_id)` 但 hash 不同时稳定返回 `state_operation_conflict` 409；完全相同仍幂等。 |
| F15 2048 claim ACK 前开放 | 成立 | 新增 `claiming` 状态；CAS 成功或 durable journal 后端最终报告 COMMITTED 前，移动、重开和普通 autosave 均保持门禁。 |
| F16 close release 未真正提交 | 成立 | 后端增加同步 durable `publish_slot_intent()`；退出先把 release 写 journal，再排队 SQLite replay。revision 分配与 journal publish 共用临界区，较旧 in-flight 保存不能反超。 |
| F17 replace 保留 unknown tables | 成立，未采用“换 DB 文件”实现 | exclusive transaction 内删除当前产品 schema 外的表及旧 meta，再写 archive；rollback DB image 可恢复失败。结果满足隐私语义，同时避免替换打开中的 SQLite/WAL 文件。 |
| F18 status/export 跟随 symlink | 成立 | status 只统计 no-follow regular single-link files；symlink/special 显示类型或 omission，不递归外部目录。 |
| F19 schema version 不在 snapshot | 成立 | `schema_meta.version` 与 export tables 在同一个 SQLite read transaction 中读取，不再调用事务外 `store.schema_version()`。 |
| F20 no-force output TOCTOU | 成立 | temp fsync 后再次执行 reserved 校验；无 force 用 hard-link no-clobber 发布，竞争者先创建时保留对方文件并返回 409。 |
| F21 evidence size/hash 可选 | 成立 | 当前 manifest format 2 的 included item 必须精确包含 path/size/sha256/base64；omission 使用独立结构；exact duplicate 去重，冲突 duplicate 拒绝。 |
| F22 archive 多份内存 JSON | 合理容量问题，不是无界漏洞 | v2 仍有 128 MiB 总上限和表/节点/字符串配额。5,000 attempts 实测 2.34 MiB archive、11.33 MiB tracemalloc peak（4.83×）。冻结 v2，流式 v3 在协议需求明确后另行设计。 |
| F23 缺事务管理入口 | 成立 | CLI 增加 `transactions`、`export-transaction`、`recover-transactions --apply`；证据导出同样 no-follow/no-clobber。 |
| F24 无统一保留策略 | 成立 | status 增加 source/type/size/mtime；`cleanup-recovery` 默认只预览，apply 必须提供包含逐文件相同 hash 的完整当前 archive，且不直接删除 import txn。 |
| F25 lock path 可为 symlink | 成立 | application、maintenance、score request 和 state key lock 使用 no-follow control-file opener，并验证 regular file、owner 和 group/world write bits。 |
| F26 direct store 可绕过协议 | 部分成立 | `LocalGameStore` 是低层实现，无法禁止测试和迁移直接构造；正式运行入口 LocalBackend、Flask、data CLI 已全部参加 lease，边界写入协议文档并有阻断测试。 |
| F27 main 未启用 required checks | 成立，仓库设置未完成 | CI 已把新增 storage 用例纳入三平台和 release gate；required checks 是 GitHub 设置，当前环境没有 `gh`，不能用 workflow 文件冒充已保护。 |
| F28 无 hash/platform lock | 成立，部分完成 | 已有精确 release closure、固定 build/pip 版本、依赖审计、SBOM；新增 installed-manifest/SBOM 对照。跨三平台 wheel hash lock 仍未生成，不能拿 macOS hash 伪装通用 lock。 |
| F29 LICENSE 需权利人 | 成立，不能代决策 | 权利、素材、商标清单保留；release gate 新增 NOTICE 素材登记检查。许可证仍需代码和视觉内容权利人选择。 |

## 额外落实的玩法项

- Tetris 改为可注入 RNG 的 7-bag，加入落点影子和每块一次 hold；规则版本升为
  `tetris-assist-3`，避免与旧规则成绩混排。
- Sokoban 增加已解锁关卡选择：`[/]`、PageUp/PageDown 和数字 1–9；从选关进入时明确标为
  practice，不计入 ranked campaign。
- Zuma 原有五种球内形状标记已经满足“颜色不是唯一信息”，没有重复实现第二套符号系统。

其余大型玩法、原生安装包和全局可访问性建议是合理 backlog，不被写成“不属于产品所以拒绝”；
逐项状态见 `thirteenth-optimization-matrix-zh.md`。未实现项保持“未完成/部分”，不以文档声明充数。
