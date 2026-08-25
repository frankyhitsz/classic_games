# 第十六次代码审查答复

本轮按当前 `main` 的真实代码、文件系统行为和本机测试核对 26 条 Finding。两个 P0 均成立；恢复、导入、
状态事件和游戏控制器中的大部分高优先级意见也成立。涉及 Archive v3 改格式、仓库保护和许可证的建议需要
分别按冻结协议、GitHub 管理权限和权利人决定处理，不能通过修改本机代码假装完成。

| Finding | 判断 | 处理与证据 |
| --- | --- | --- |
| F01 score hard-link 发布与单链接读取冲突 | 成立 | 删除 `os.link` 发布；request lock 内 `os.replace`、fsync，再用 bounded/no-follow reader 二次确认。canonical 始终 `nlink=1`。 |
| F02 `set_progress` 被错误 component merge | 成立 | resolver 按 method 分派：set/set 与 merge/set 使用完整 order 的 LWW，set/merge 和 merge/merge 才做单调合并；set baseline 会生成有效 component。 |
| F03 orphan lock timeout 隔离活跃 temp | 成立 | score/state/clock 锁超时一律保留；取得锁后复查 dev/inode/size/mtime，变化或仍在 grace window 的文件不处理。 |
| F04 valid orphan 遇坏 canonical 被隔离 | 成立 | 先隔离 canonical，隔离成功才提升或合并 temp；score/state 均有故障测试。 |
| F05 state quarantine 失败仍覆盖 current | 成立 | quarantine 返回 false 时抛出 retryable `state_quarantine_failed`，原字节不变。 |
| F06 state clock 无界且跟随 symlink | 成立 | 改用 64-byte、single-link、no-follow descriptor reader；symlink 自身进入 quarantine。 |
| F07 state timestamp 上限过宽 | 成立 | operation 与 journal parser 同时拒绝超过本机时间 24 小时的未来时间，防止其长期压制正常写入。 |
| F08 LocalStateEvent 缺 winner identity | 成立 | event 增加 `operation_id`、`payload_hash`；缓存与重建按 `(logical_revision, operation_id)` 比较。 |
| F09 terminal transaction root 清理失败阻塞 | 成立 | active classifier 验证 journal phase；COMPLETED/ROLLED_BACK 残留不阻塞启动，后续恢复仍尝试清理。 |
| F10 import 普通文件/junction 恢复循环 | 成立 | preparation/import root 用 `lstat` 检查目录和 Windows reparse flag；普通文件、symlink、junction 明确 recovery-required。 |
| F11 raw rollback 未认证 sidecar | 成立 | 选择任务书允许的安全策略：raw fallback 发现 WAL/SHM/journal 就停止自动恢复并保留现场，不虚报完整 rollback；未擅自改 transaction v3 格式。 |
| F12 fresh staging 不在 recovery inventory | 成立 | `.fresh-replace-*` 纳入 status/export/cleanup inventory 和保留前缀；异常退出留下的完整 staging 可盘点。 |
| F13 import/replace 重复数据库副本 | 部分成立 | 多副本分别承担 rollback、fresh materialization 和用户 backup，不能直接删掉其中任意一份；新增精确的磁盘空间 preflight，在 staging 前拒绝空间不足。后续 v4 可复用 authenticated image 减少峰值。 |
| F14 reserved prefix 不完整 | 成立 | 无论路径是否已存在，`.import-*`、`.preparing-*`、`.fresh-replace-*`、`.replace-plan-*` 都不能成为 export target。 |
| F15 export 有 repair 副作用 | 成立 | export 默认 snapshot-only；`--repair-before-export` 才显式修复，结果返回 repair 选择和 recovered roots。 |
| F16 preview 名称暗示完全只读 | 成立 | 增加真正不打开目标库的 `inspect-archive`；`preview-import` 帮助文案明确会先恢复目标 transaction。 |
| F17 pending score 仍用 `read_text` | 成立 | planner 统一改为 bounded/no-follow target reader。 |
| F18 archive loader 非 descriptor snapshot | 成立 | archive 使用 no-follow descriptor、size bound、打开前后 inode 校验和 read 后 fstat 稳定性校验。 |
| F19 历史 active pending 无 preserve-only | 成立 | removed game/旧 ruleset score 与 state pending 写入 `historical-pending` evidence，不激活，也不阻断同档案其他数据。 |
| F20 v3 catalog 每游戏单 ruleset | 成立，但不能原地改 | v3 已发布且 hash/manifest 冻结，原地换成集合会破坏 reader。`storage-protocol.md` 已记录限制，并给出 v4 observed set 的来源分类设计。 |
| F21 Store/Flask lexical path | 成立 | `LocalGameStore` 构造即 canonicalize；LocalBackend 的 prebuilt Store 与显式 db path 不一致时直接拒绝，Flask 复用 canonical Store。 |
| F22 backend 后半段异常泄漏资源 | 成立 | workers 延后到所有易失败初始化之后创建；第二 worker 或首次 submit 失败会关闭已建 worker 与 application session。 |
| F23 `publish_slot_intent` 不返回 resolution | 成立 | 返回 `published`、`resolution`、winner/requested operation ID；2048 退出控制器不再把 superseded release 误报为 pending。 |
| F24 2048 RNG 不可注入 | 成立 | 构造器接受 RNG；默认仍用原模块随机源，旧 seed 行为兼容，测试可精确控制出生位置与 2/4 概率。 |
| F25 Sokoban 同 key Future 覆盖 | 成立 | 每个 key 只允许一个 in-flight Future，另保留一个 newest queued snapshot；已完成但未 poll 的 Future 也不会被覆盖。 |
| F26 practice campaign snapshot 仅内存 | 成立 | 进入练习时把棋盘、undo history、ledger 和 attempt identity 写入 `practice-return` durable slot；异常退出后恢复原 campaign，并在成功恢复/主动返回后关闭该现场。 |

## 审查中不应照单全收的结论

- F13 所列多个副本不是同一个无意义副本：transaction rollback、旁路 fresh DB 和用户可见 backup 的生命周期
  不同。本轮解决的是“空间不足仍开工”的数据风险；在没有 transaction v4 设计前直接共用文件反而会把短期
  rollback 与长期 evidence 绑在一起。
- F20 的问题描述正确，但建议若被理解为直接修改 Archive v3 就不合理。当前只冻结 v3、记录已知限制，并把
  v4 设计写清；没有让旧 archive 的 manifest 在相同版本号下改变含义。
- “lock file hard-link 未检查”与当前代码不符：`_open_control_file()` 已要求 `st_nlink == 1`，本轮保留既有
  回归，不重复实现。
- required checks、三平台 hash lock、LICENSE 与签名发行不是运行时 Bug。CI 工作流、精确 build-system pin、
  Python 3.11–3.13 声明和非 loopback 保护已在仓库内完成；GitHub branch protection 与许可证仍需管理员或
  权利人决定，状态没有伪装为完成。
