# 第十八次审查修复记录

## 状态

- [x] 完整读取 1919 行任务书；基线 `984cd84`，当前分支 `main`。
- [x] 确认初始工作区只有用户新提供的任务书未跟踪。
- [x] 核对 F01–F30，实施本轮修复；未完成边界单独列在答复和优化矩阵。
- [x] 新增定向测试，完成首轮实现验证。
- [x] 第一轮独立复查、修复和复验。
- [x] 第二轮独立复查、修复和复验。
- [x] 更新 30 条答复、162 项优化矩阵与协议文档。
- [x] 提交、推送和远端 CI（实现提交 `03ea7e8`，CI #53 七个 job 通过）。

## 已核实

- component resolver 把 raw merge 的整体 hash 与 component semantic hash 比较，定义不一致。
- Store 的旧 merge 分支未区分前一操作是 set 还是 merge；outbox 也缺 set/旧 merge 分支。
- final reject marker 在锁外读取，存在使用旧快照的窗口。
- 存档隔离接口未携带已加载身份，直接删除执行时的当前行。
- F05、F20 的部分描述已被第十七轮末次修复覆盖，需保留原有定向测试作为反证。

## 验证记录

初版检查：318 项 storage 通过（1 项 Windows 专用跳过），107 项游戏/API 检查通过，Ruff 通过。

最终本机检查：

- storage 共 333 项，331 通过、2 项 Windows 专用跳过；v16 新增 39 项定向检查。
- Ruff（包含新 B012/B018）与三个核心契约/工具模块 mypy 通过。
- Windows/Linux wheel 下载均通过 require-hashes 校验；macOS 在隔离环境强制重装全部 hash-locked 依赖成功。
- 20,000 步 gameplay stress、100 次资源循环（FD 18→18）、240 次并发写入及 integrity check 通过。
- 最后一次 release 的同步 local-save p99 约 2.88 ms；锁竞争下提交入队 p99 约 0.045 ms。它们不是同一种计时，
  不用同步写入数据冒充 enqueue 指标。
- storage+stress 的 Store/Archive/Transaction 分支覆盖率合计约 79%，尚未达到 90%。
- 初次最终 release 因新测试比较 macOS /var 与 /private/var 路径别名失败；路径断言已改为 canonical path，
  storage 复跑通过，完整 release 十个阶段全部通过。
- 最终 107 项 gameplay/API 通过；合并 coverage 后全仓约 78%。CI 全仓下限由 60% 提高到 75%，
  Store/Archive/Transaction 另设 75% 下限；没有修改测试排除项。
- 包版本 0.10.0 的 wheel/sdist 安装、只读用户数据 smoke、SBOM/manifest、依赖漏洞审计均通过。
- Windows 文件修正后，本机完整 release 十一个阶段再次全部通过；机器可读结果保存在
  `/private/tmp/classic-games-eighteenth-verified.json` 与同名前缀的 JUnit XML。

## GitHub 交付

- 实现提交 `7d82db6` 已推送到 origin/main。CI #50 在解析 YAML 时失败，尚未创建测试 job：
  单行 run 中 `--only-binary=:all:` 后的冒号被当作 YAML 分隔符。
- 已把四处安装命令改为 YAML 多行字符串，并新增本地 workflow-config 解析门禁及 hash-locked PyYAML；
  本地配置检查通过，修复提交 `fd18ee6` 已推送。完整本机 release 十一个阶段全部通过。
- CI #51 的 Windows mypy 报 POSIX 分支局部名未定义；运行时提前返回没有问题，但原 os.name 判断
  未被静态分析识别。已将系统调用及结果检查放在各自平台分支内，并增加三目标平台的本地类型预检。
  CI #51 其余六个 job 通过；Windows 继续验证修正后的结果。
- CI #52 的 Windows 类型检查通过，storage 揭示两个真实平台差异：底层 `os.read` 需要二进制标志，
  否则 CRLF/0x1a 会改变读取字节；`DirEntry.stat()` 在 Windows 返回的链接数为 0，不能用于安全类型判断。
  已为文件读取显式指定 O_BINARY，目录项检查改为新鲜 lstat，保留拒绝硬链接与 reparse point 的约束。
  新增全字节二进制往返/摘要与无缓存链接数测试。行为依据见
  [Python 3.11 os 文档](https://docs.python.org/3.11/library/os.html#os.open)及其 DirEntry.stat 说明。
- 实现提交 `03ea7e8` 的 [CI #53](https://github.com/frankyhitsz/classic_games/actions/runs/34081282128)
  七个 job 全部通过：Linux/macOS/Windows、Python 3.12/3.13、最小依赖和完整 release gate。
  Windows 专用 junction 测试实际执行通过；平台 job 的 storage、stress、gameplay 与覆盖率门禁均通过。
  本机无 gh 可执行文件，推送使用已配置 SSH git；远端 CI 通过已有 GitHub 连接器只读核验。

## 第一轮复查

- 异步练习保存抛异常会留下 pending transition，已改为失败结果也执行状态收尾。
- 同步存档不能仅凭 backend capability 宣称成功，现要求明确的持久化收据。
- Store 的 reset barrier 必须在 component receipt 查重/冲突之后检查，已调整顺序。
- 老测试的故障注入点与收据更新为新协议；原有数据保护断言保留，slot replay 改为验证不复活。

## 第二轮复查

- 删除屏障原本只约束运行中的数据库；导出旧 pending 后在空库恢复仍可复活存档。
  已按持久屏障过滤不再活跃的 journal，并在 manifest 单独记录退休数量；原文件不删除。
- merge import 不再插入时间早于本机删除屏障的 committed slot；显式覆盖恢复仍可恢复所选备份。
- 新增完整发布前后进程中断、legacy/new 锁互斥、恢复后的移动/撤销、失败 Future 与取消排队测试。
- 新增 API 测试一度把 `DB_PATH` 写成 `DATABASE`，在默认目录误建空库；核对七张业务/隔离表全为 0 行后，
  将文件移到 `/private/tmp/classic-games-empty-test-db-SgdkjE/games.db` 保留。已修正配置并断言实际 DB 路径，
  后续完整测试统一设置临时 `GAMES_DB` 兜底，不接触默认数据位置。
