# 第十五次审查修复记录

## 状态

- [x] 完整读取并核对 F01–F31 与 P0–P3 共 121 项；
- [x] 关闭 handoff、transaction root、state journal 与 import planner 四项 P0；
- [x] 完成 reject/temp/path/archive/坏库 replace 等关键恢复加固；
- [x] 完成 2048 claim revision/hash 与 Sokoban PracticeSession 修复；
- [x] 增加玩法与工程小项：Snake/Zuma RNG、Tetris hold 预览、固定 CI constraints；
- [x] 第一轮完整 storage/gameplay 验证与问题修复；
- [x] 第二轮独立复查、stress、资源和打包验证；
- [ ] 提交、推送并核验最终 GitHub CI。

## 第一轮发现

- 初版 orphan temp 恢复会扫描其他进程仍在写入的文件，32 进程 score spool 用例出现 31/32。现加入
  2 秒 grace；未到期 temp 保持原位并由 complete export 报告，陈旧完整 temp 才在 key lock 下提升。
- practice schema 收紧后，旧 profile collision fixture 仍期待 practice `unlocked_level`。当前规则改为隔离该
  旧字段，不恢复成活动解锁；测试改为验证其不会进入 progress。
- 2048 claim fixture 只返回 owner 字段，没有 authoritative value hash。测试 fake 现按真实 Store hash 计算，
  不通过放宽生产检查规避新不变量。
- fresh-DB replace 不能直接复用只接受健康 SQLite 的 v2 rollback。ImportTransaction 在坏库灾难恢复时可写
  authenticated raw rollback v3；正常 import 继续使用 v2 SQLite image。

## 第二轮发现

- fresh replace 在 transaction 发布后、数据库替换前写永久备份；原异常范围没有覆盖备份发布与 sidecar
  迁移。现在整个发布阶段都受同一 rollback 保护，备份发布故障注入验证目标字节与 transaction root 均恢复。
- v3 catalog 允许历史子集是正确的，但记录所属游戏也必须在该 archive 的 catalog 中声明。当前 import 对
  attempts/progress/save slot 统一核对 catalog；export 会把 committed 历史游戏补入 catalog。空记录的旧
  catalog 仍兼容，夹带未声明记录会在 preview 失败。

## 已完成的本地验证

- Ruff 与 compileall 定向检查通过；
- 235 项 storage 通过，本机仅跳过 Windows junction 专用用例；本轮新增 27 项测试；
- 107 项 gameplay 回归通过；
- POSIX 测试使用 spawn 子进程和真实 advisory lock，不用 mock 代替等待者；
- 坏库 replace、missing journal、state conflict、planner silent drop、orphan temp、DB symlink、hard link、
  evidence-bound v1、Archive future/catalog、Sokoban campaign return 均有定向覆盖。
- release profile 全部通过：dependency audit/SBOM 无已知漏洞，wheel 与 sdist 以 0.8.0 安装冒烟通过；
- stress 完成 20,000 个确定性步骤、240 次并发写，SQLite integrity check 通过，100 次资源循环 FD 19→19；
  五款游戏 render p95 均低于 5 ms，locked-submit p99 为 0.027 ms。
- 功能提交 `8338ea3` 的 GitHub CI #41 在 4 分 5 秒内成功：release gate、core-only、Python
  3.12/3.13，以及 Linux、macOS、Windows 三平台矩阵共 7 个任务全部通过。
- 纯文档提交触发的 CI #42 在 Windows 暴露启动器悬停用例的休眠竞态：慢机器会在 driver 发完移动与退出
  事件后才进入首帧。用例改为等待首个 leaderboard draw，再等待目标标题 draw 后退出，不再依赖固定休眠。

## 尚需外部决定

- GitHub main required checks/branch protection；
- Linux、macOS、Windows 独立 `--require-hashes` lock 与签名安装包；
- LICENSE、名称/商标、字体、图形和音效权利结论。
