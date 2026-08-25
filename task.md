# 第十六次审查修复记录

## 状态

- [x] 完整读取并核对 F01–F26 与 P0–P3 共 121 项；
- [x] 修复 score single-link 发布和 progress method matrix 两项 P0；
- [x] 完成 orphan、clock、event、transaction、archive 与路径生命周期关键加固；
- [x] 完成 2048 RNG/slot resolution 与 Sokoban single-flight/durable campaign 修复；
- [x] 增加第十六次定向回归并完成第一轮 storage 验证；
- [x] 第二轮独立复查、gameplay、stress、release 与打包验证；
- [x] 提交、推送并核验 GitHub CI。

## 第一轮发现与修复

- score `os.link(temp, target)` 在 unlink temp 前确实让 canonical 短暂 `nlink=2`，与 reader 的 single-link
  不变量直接冲突。改为 request lock 内 replace 后，scanner、remove、quarantine 和 retry 也统一进入锁。
- resolver 只看 `kind=progress`，两个合法 `set_progress` 会进入只接受 components 的 merge。当前 method
  matrix 让 set/set 和 merge/set 走 LWW；set/merge 把 set winner 变成带 hash 的 baseline component。
- orphan 初版只在 lock 前看一次 mtime，且 broad `StoreError` catch 会把 lock timeout 当坏文件。现在 lock
  后复查完整 fingerprint；timeout 保留，坏 canonical 先隔离，隔离失败不覆盖。
- state current 的 quarantine 返回值此前被忽略；故障注入证明可覆盖唯一坏字节。当前返回 false 立即停止。
- export 构造 outbox 时会恢复 orphan，preview 也会回滚 transaction，却都被描述成只读。export 默认改为
  snapshot-only，并新增完全不打开目标库的 `inspect-archive`。
- SQLite 打开坏库时可能处理既有 sidecar，因此 sidecar 存在性必须在尝试连接前记录；随后 raw fallback
  才能可靠停止自动恢复。
- Sokoban 的 per-key Future 不仅会覆盖运行中的写，也会覆盖已完成但尚未 poll 的结果。当前只要槽非空就
  queue newest，消费旧结果后再提交；练习前 campaign 另写 durable `practice-return` slot。

## 第一轮验证

- 新增 `tests/test_storage_v14.py` 25 项，覆盖 hard-link 不可达、两种 orphan timeout、坏 canonical、
  quarantine failure、clock symlink、progress method matrix、event order、transaction root/raw sidecar、
  snapshot export、pure archive inspect、historical pending、space preflight、constructor cleanup、API 暴露、
  2048 RNG、Sokoban coalescing 和 durable campaign restore；
- storage 初跑 257 项时发现旧 misnamed spool 恢复被过度收紧；增加稳定顺序的 multi-request lock，保留旧
  canonical rename 行为且不重新引入扫描竞态；
- 修正测试环境：系统 Python 3.13 未安装 pygame，不把依赖缺失误判为产品回归；正式验证统一使用项目
  `games_env` 的 Python 3.11/pygame 2.6.1。

## 第二轮发现与修复

- worker 创建的 cleanup 最初只包住第二个 worker；第一个 worker 构造自身失败时 session 仍可能泄漏。
  当前两个 worker 都在同一异常范围内，probe 也把 unsafe directory 转成不可写状态而非越过 cleanup。
- reserved prefix 初版只拒绝数据库同目录下的直接文件名，尚可把 archive 写到不存在的
  `.fresh-replace-*/child`。当前按相对路径第一段判断，目录不存在时同样拒绝。
- historical pending 已正确转为 evidence，但 import 结果仍把它计入 `pending_restored`。当前只统计真正激活
  的 score/state，并在非零时另报 `historical_evidence_only`，同时保持普通 archive 的旧返回结构。
- campaign durable slot 的恢复数据需要与 archive 一样视为不可信输入。当前限制 level、坐标、箱子数、
  history 长度、ledger 和 attempt identity；session load 未结束前不接收棋盘操作。
- 精确固定的 setuptools 80.9.0 被 2026-08 的依赖审计识别为 `PYSEC-2026-3447`。build-system 与
  constraints 同步更新为 83.0.0，重新审计为零已知漏洞。
- GitHub CI #45 的 core-only 环境没有可选 Flask 依赖；新增 API 暴露测试却无条件导入 server adapter，
  导致产品核心依赖模型被测试破坏。用例现与其他 Flask 边界测试一致，在 optional dependency 缺失时跳过；
  全新仅安装 `-e .` 的 Python 3.13 环境已验证 260 项通过、6 项可选依赖用例跳过。

## 完整验证

- Ruff、compileall 与 `git diff --check` 通过；
- 260 项 storage 通过，本机仅跳过 Windows junction 专用用例；本轮新增 25 项；
- 107 项 gameplay 回归通过；
- stress 完成 20,000 个确定性步骤、240 次并发写和 100 次资源循环，FD 19→19，SQLite
  `integrity_check=ok`，五款游戏 render p95 均低于 5 ms；
- release profile 在 Python 3.13 隔离环境通过：依赖审计与 SBOM 均为零已知漏洞，wheel/sdist 使用
  setuptools 83.0.0 构建并从只读工作目录完成用户数据冒烟，随后重复通过 storage/stress/gameplay。
- 功能提交 `f02d3e5` 的 GitHub CI #45 首次暴露 core-only 可选依赖问题；修正提交 `ac4c531` 的 CI #46
  在 4 分 54 秒内成功，release gate、core-only、Python 3.12/3.13 和 Linux/macOS/Windows 三平台共
  7 个任务全部通过。

## 尚需外部决定

- GitHub main required checks/branch protection；
- Linux、macOS、Windows 独立 `--require-hashes` lock 与签名安装包；
- LICENSE、名称/商标、字体、图形和音效权利结论。
