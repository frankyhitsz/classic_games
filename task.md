# 第十一次审查修复记录

## 状态

- [x] 读取任务书并逐条核对 F01–F31；
- [x] 建立 P0–P3 共 129 项处理矩阵；
- [x] 完成 archive v2、路径保护、active pending、manifest、配额和一致快照；
- [x] 完成严格导入 planner、attempt ID 重映射、alternate unique 冲突和 missing-only baseline；
- [x] 完成 baseline 单调性、pending high-water、纯语义校验、旧 journal 回滚和拒绝证据隔离；
- [x] 完成 2048 schema 5 CAS、owner epoch、重新读取接管、终局补分和 v4 milestone 恢复；
- [x] 完成 2048 autosave 合并和俄罗斯方块同义键逻辑边沿；
- [x] 完成 release timeout、隔离 venv wheel smoke、SBOM、Actions SHA 和完整依赖约束；
- [x] 完成第一轮独立复查并重新验证；
- [x] 完成第二轮独立复查与完整验证；
- [ ] 提交、推送并核验远端 CI。

## 初版验证

- 新增 `test_storage_v9.py`，覆盖危险导出路径、已有文件确认、pending 往返、attempt ID 重映射、
  alternate unique 冲突、archive hash/深度/NaN、baseline 截止、journal 回滚、健康 receipt 读锁、
  后台 outbox 启动、2048 v4/终局、autosave 合并、Tetris 同义键和 release timeout/wheel stage；
- storage 从 128 项增加到 143 项，全部通过；Ruff 与 compileall 通过；
- 固定 seed 20,000 步、240 次并发写和 100 次资源循环通过，FD 19→19；保存 p99 2.721 ms。

## 第一轮复查

- 发现 recovery evidence 原本在数据库提交后才解码，坏 base64 或越界路径可能造成部分导入。
  现已将路径、base64、单文件/总量和 hash 校验前移到共享 planner；apply 只处理已验证内容；
- 发现 1 MiB JSON 字符串限制会拒绝本工具导出的较大 base64 evidence，已按 8 MiB 文件上限将
  JSON 字符串边界调整为 12 MiB，仍保留总 archive 和总 evidence 配额；
- 发现 2048 CAS 在重新读取后仍可能因最后一刻竞争失败。失败现在立即恢复 load gate、重新读取
  当前 slot，并清除旧 expectation，避免玩家继续操作一个无法保存的棋盘；
- wheel smoke 改为无 system-site-packages 的新 venv，按发行约束安装 wheel 及依赖；release 新增
  CycloneDX JSON SBOM 阶段并纳入 CI artifact；
- 修复后 Ruff、compileall、142 项 storage 和完整 stress 再次通过。

## 第二轮复查

- v2 archive 在同一目标重复导入：第二次所有表插入数均为 0；v1 archive 仍能 preview/apply；
  单独进程持 shared application lock 时，export 在 2 秒内返回可重试 `maintenance_busy`；
- 发现 export guard 只在 pending 目录已存在时才拦截路径，可能把 archive 创建成第一个 pending
  文件。现对约定 active 目录无条件做路径包含判断，并加入“目录尚不存在”用例；
- 发现 status 为统计 pending 而构造 outbox 会顺带迁移 legacy spool，违背查看操作预期。现改用有界
  raw directory count，status 不解析、不升级、不隔离文件；
- 发现 target 已有同 request ID、不同 payload 的 pending score 时，原 planner 会在数据库提交后
  才失败。preview 现在提前读取目标 envelope 并报告冲突；state pending 也预演 progress merge；
- v1 recovery 的历史绝对路径在兼容转换时只保留 basename；evidence 的各级父目录在写后 fsync；
  v4 writer 不能覆盖已升级为 v5 的同-token slot；桌面写等待 maintenance 的 worker timeout 扩为
  300 秒，避免长导入把尚未 journal 的操作变成 Future 异常；
- 复核 Windows dependency resolution，与 macOS 的完整约束闭包一致；129 行矩阵计数为
  P0 20、P1 48、P2 34、P3 27。

## 最终验证

- `run_tests.sh`：107 项 gameplay、143 项 storage/迁移/生命周期、固定 seed 20,000 步全部通过；
- stress：240 次并发写 `integrity_check=ok`，资源循环 100 次 FD 19→19；渲染 p95 为 Tetris
  2.255 ms、Snake 2.146 ms、2048 1.453 ms、Sokoban 1.569 ms、Zuma 4.254 ms；本机保存
  p99 2.426 ms，持锁异步提交 p99 0.040 ms；
- 独立 release venv 完整通过 8 个阶段：Ruff、dependency audit、CycloneDX SBOM、compile、
  wheel smoke、storage、stress、gameplay；pip-audit 未发现已知漏洞；
- wheel smoke 生成 153,384 字节 wheel，在无 system-site-packages 的新 venv 按约束安装 pygame，
  成功导入 launcher/data/store 并运行 `classic-games-data --help`；
- `git diff --check`、compileall、Actions SHA 检查、archive v1/v2/重复导入演练和维护锁跨进程演练
  均通过。

## 远端验证

- CI #27 的跨平台矩阵已启动，但 release-gate 在统一 release 命令中退出 1；公开 job 页面没有展开
  内部 stage 日志。本机同一隔离 release profile 八阶段均通过；复核 GitHub clean runner 差异后，
  wheel build 改回 PEP 517 默认 build isolation，不再依赖 runner 主环境恰好预装足够新的 setuptools。
