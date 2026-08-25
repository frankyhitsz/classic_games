# 第十三次审查修复记录

## 状态

- [x] 读取任务书并建立 F01–F29、P0–P3 共 122 项证据清单；
- [x] 修复启动恢复、归档边界和导入事务问题；
- [x] 修复 state outbox、2048 claim 与关闭释放；
- [x] 完成事务管理、恢复盘点、安全清理和发行证据检查；
- [x] 落实本轮可完整验收的 Tetris 与 Sokoban 玩法项；
- [x] 写入逐条审查答复和 122 项矩阵；
- [x] 第一轮复查：恢复、并发、配额和跨平台路径；
- [x] 第二轮复查：玩法、性能、打包和发行；
- [ ] 提交、推送并核验远端 CI。

## 核心修复

- 普通客户端和 Flask 在数据库初始化前恢复 import transaction；事务 v2 校验 rollback DB、staged、
  before 内容及发布前 target fingerprint，损坏时保留证据并停止启动；
- reserved policy 覆盖 SQLite sidecar、application/maintenance lock、legacy pending、active journal 和
  import roots；export/recovery/pending/transaction evidence 使用 no-follow reader；
- archive manifest format 2 严格校验 application、ruleset、counts、complete 和 evidence 元数据；旧 v2
  只允许 merge；replace 删除未知表和完整 active namespace；
- reject marker 采用 tmp+replace 和 marker hash；同 state identity 的不同 payload 返回冲突；
- 2048 claim ACK 前保持门禁，退出 release 先进入 durable journal。revision 分配与 journal 发布串行，
  旧的 in-flight active save 不能重新越过 release；
- CLI 增加 transaction list/export/recover 和 recovery cleanup；cleanup apply 必须用完整当前 archive 的
  逐文件 hash 证明；
- release profile 增加 installed manifest/SBOM 对照和 NOTICE runtime asset 检查；
- Tetris 增加可注入 RNG 的 7-bag、ghost、hold，规则版本升为 `tetris-assist-3`；Sokoban 增加只访问
  已解锁关的 practice selector。

## 第一轮复查

- 发现仅限制 FileOperation 目录仍允许人为写入 `pending_saves.json`、reject 或 restore；现这些控制
  artifact 只能删除，score/state 写入还必须是 canonical filename；
- 发现 `lstat` 后再按路径 `read_bytes()` 仍有替换窗口；现通过 `O_NOFOLLOW` descriptor、fstat 和
  inode/link count 复核读取，POSIX 与无 O_NOFOLLOW 平台均有二次检查；
- 发现 cleanup 只比较 archive 声明的 sha256 仍可接受伪造 metadata；现先解码并校验 archive 内容，
  删除前再读取目标核对实际 hash；
- 181 项 storage 验证通过，其中第十三轮新增 20 项，覆盖启动恢复、损坏事务、锁 symlink、输出竞争、
  target change、manifest、清理证明、Flask lease、claim gate、release race、7-bag/hold/ghost 和选关。

## 第二轮复查

- 107 项玩法检查全部通过，包括 Tetris 全位置旋转、Zuma 连续轨道/连锁/快速点击、2048 输入与保存、
  Sokoban 16 关求解和 launcher 返回响应；
- 固定输入 20,000 步、240 次并发写、100 次资源循环通过，FD 19→19，SQLite integrity check 为 ok；
  本机 Zuma render p95 4.839 ms，保存 p99 3.367 ms；
- archive v2 5,000 attempts 基准：2,344,194 B archive，0.600 s，tracemalloc peak 11,332,465 B
  （4.83×），据此保留 bounded v2 并冻结流式 v3 设计边界；
- Python 3.13 一次性 release 环境完成 Ruff、依赖审计、CycloneDX SBOM、compile、wheel+sdist、
  installed manifest 对照、181 项 storage、stress 和 107 项 gameplay；pip-audit 未发现已知漏洞。

## 未伪装完成的外部事项

- 当前环境没有 `gh`，尚不能核对或设置 main required checks；
- 三平台 hash lock、native installer/app bundle、签名和自动 Release 仍需要跨平台制品与仓库权限；
- LICENSE、名称/商标结论仍需代码和视觉内容权利人确认；NOTICE 新素材门禁已经加入。
