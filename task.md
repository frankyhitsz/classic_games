# 第十二次审查修复记录

## 状态

- [x] 读取任务书并建立 F01–F29、P0–P3 证据清单；
- [x] 修复 import/pending/rollback 的 P0 数据风险；
- [x] 完成 archive 完整性、planner、legacy status 和 recovery 路径加固；
- [x] 完成 2048 autosave 与 release smoke 的成立项；
- [x] 写入逐条审查答复和 108 项矩阵；
- [x] 第一轮独立复查：故障注入、配额、并发与恢复；
- [x] 第二轮独立复查：兼容性、性能和完整 release 验证；
- [x] 提交、推送并核验远端 CI。

## 当前发现

- F01 成立：当前数据库先 commit，随后才逐个写 pending/evidence，文件失败会产生部分导入；
- F02/F03 成立：score 次数无上限且按次数 fsync，state schema 3 没有复用 store 的 int64/时间边界；
- F04/F18 成立：`.restore` 不在启动扫描范围，rollback 失败后 previous 也没有进入 non-durable 状态；
- F05 部分成立：exclusive lock 能冻结协作的持久层写入，但不能包含 worker 队列中尚未发布的内存动作；
- F06–F10 成立：store 初始化早于锁、pending 扫描可截断/修改源文件、planner 没有完整预演目标业务冲突；
- F16 需修正措辞：store 已校验 2048 owner schema，但尚未复用游戏端棋盘、终局和 attempt 语义；
- F21/F22 成立：当前 150 ms 只有尾沿且一个 Future 引用会覆盖前一个；
- F23 是策略选择而非数据安全 bug；当前实现是单 autosave + owner CAS，不等于支持多长局；
- F26/F29 属于仓库管理权限和权利人决定，不能用代码伪造完成状态。

## 初版验证

- 新增 `test_storage_v10.py`，覆盖 attempt/time/revision 边界、state 总配额、reject crash marker、
  rollback non-durable fallback、post-commit ENOSPC、阶段事务恢复、committed receipt planner、只读
  partial export、schema compatibility、legacy status、application lease、evidence 路径、2048 最大
  dirty age/单 Future、score maintenance timeout、replace restore 和 1,005 个 merge components；
- storage 从 143 项增加到 159 项，全部通过；Ruff、compileall 和独立 wheel+sdist smoke 通过；
- package smoke 在隔离 venv 先后安装 wheel/sdist，从只读 cwd 在 `GAMES_DATA_DIR` 首次创建数据库，
  并运行 `classic-games-data --help`。

## 第一轮复查

- 发现仅在 import 时回滚中断事务会让下一次 export/preview 读取部分状态；现三种维护操作都会先
  自动恢复，`status` 只读显示 unfinished transaction 数量；
- 发现路径校验在 macOS `/var → /private/var` 上把系统祖先别名误判为 journal symlink；事务改为
  发布到已 resolve 且仍位于数据库目录内的 candidate，同时 preview 调用同一 target validator；
- 发现原计划只有 merge import，不能从旧完整备份真正回滚；新增 `restore-replace`，恢复 committed
  表、active pending 和 baseline，删除 archive 中不存在的旧 active journal，并保留导入前备份；
- 发现 sdist smoke 使用 `pip download --no-binary` 对本地目录仍生成 wheel；改为调用 PEP 517
  backend 构建 sdist，再由隔离 venv 安装以验证实际 source distribution；
- 发现 macOS 默认用户目录受当前沙箱限制；smoke 使用产品已有 `GAMES_DATA_DIR` 配置测试真实用户
  数据路径，没有改写 HOME，也没有回退到仓库 cwd。

## 第二轮复查

- 发现 transaction journal 只有结构校验，若内容被截断或篡改可能按错误 phase/target 恢复；现 journal
  自带 canonical hash，operations 限制 basename/类型，回滚前对 SQLite 镜像执行 `quick_check`；
- 发现 import planner 读取目标已有 pending/evidence 时没有先检查文件大小，恶意超大目标仍会制造
  内存峰值；现 score/state 64 KiB、evidence 8 MiB，整个事务 staging+before 128 MiB；
- 发现 evidence symlink parent 在 preview 时可能直到 prepare 才失败；preview 和 apply 现共用
  `validate_file_operations()`，并在读取任何已有 target 前先做 resolve containment；
- 发现中断数据库被外部删除时，`database.exists()` 分支会跳过可用回滚镜像；import/replace 现无条件
  扫描阶段事务，可从镜像重建目标；
- clean venv 没有 setuptools，直接调用 backend 的 sdist smoke 失败；新增 `build==1.5.0` 和
  `pyproject_hooks==1.2.0` 发行约束，改用标准隔离 `python -m build --sdist`，完整 release 重跑通过。

## 最终验证

- 161 项 storage、107 项 gameplay 全部通过；
- stress 固定 seed 20,000 步、240 次并发写、100 次资源循环通过，FD 19→19，
  `integrity_check=ok`；Zuma 渲染 p95 4.932 ms，本机保存 p99 4.984 ms；
- release 八阶段全部通过：Ruff、dependency audit、CycloneDX SBOM、compile、wheel+sdist smoke、
  storage、stress、gameplay；pip-audit 未发现已知漏洞；
- wheel/sdist 在隔离 Python 3.13 venv 均能安装；从只读 cwd 创建外部 user-data 数据库并运行
  `classic-games-data --help`；
- 29 条 finding 与 P0 12、P1 44、P2 28、P3 24 共 108 项矩阵均已核对。

## 远端验证

- 功能提交 `10363ca` 推送后触发
  [CI #30](https://github.com/frankyhitsz/classic_games/actions/runs/32807435102)。六个 job 通过，
  Windows storage 在回滚镜像 `rb` descriptor 上调用 `fsync` 返回 `EBADF`；Windows 自身的
  wheel+sdist smoke 已先通过，故障与 archive 内容无关；
- 修复提交 `05a0b54` 将镜像以不改内容的 `rb+` descriptor 同步。对应
  [CI #31](https://github.com/frankyhitsz/classic_games/actions/runs/32807665713) 完整成功：
  release-gate、core-only、Python 3.12/3.13、Ubuntu、macOS、Windows 共 7 个 job 全部通过；
- 远端 `main` 已核对为 `05a0b5457aa87b3c0fc169486f6bae66413682f2`。当前环境没有 `gh`，
  GitHub 匿名 API 又达到 rate limit，不能把 branch protection 写成已配置；P1-35 保持“需仓库权限”。
