# 第十次审查修复记录

## 状态

- [x] 读取第十次任务书并核对 F01–F23；
- [x] 建立 P0–P3 共 118 项逐条处理矩阵；
- [x] 修复 progress aggregate/component 身份与 commit-before-unlink 丢失；
- [x] 完成 receipt/business hash、隔离失效、坏回执修复和权威结果返回；
- [x] 完成 schema 7 baseline、receipt 结构重建和 state clock high-water；
- [x] 修复 score `last_used`、HTTP launcher 身份和两款进度 HUD；
- [x] 完成 2048 终局存档、owner token、显式接管和旧实例拒写；
- [x] 增加数据检查、导出、导入预览和原子导入入口；
- [x] 增加 release profile、JUnit/JSON、CI 最小权限和发行约束；
- [x] 完成第一轮独立复查并重新验证；
- [x] 完成第二轮独立复查并重新验证；
- [x] 提交、推送并核验远端 CI。

## 初版验证

- 第十轮新增 18 项定向存储、迁移、身份、HUD、ownership 与数据归档用例；
- 真实完整 storage discover 已运行，修正旧用例的 journal schema 和终局策略预期；
- 第一轮验证另发现启动时仅因 pending 目录存在就重放的竞态，已改为只有启动快照中确有记录时
  才启动自动重放；该修复避免冲突请求测试中旧 spool 抢先落库。

## 第一轮复查

- 发现初始化先用 probe 创建 `pending/`，随后按“目录存在”启动重放，导致启动后新增的冲突
  spool 可能抢先落库。改为记录构造时的 pending 快照，运行期扫描能力保持不变；
- 更新旧测试对 journal schema 3、2048 terminal slot 和 active owner 的预期；普通恢复 fixture
  先写 released，另有独立用例验证未释放 owner 必须显式接管；
- Ruff、compile、124 项 storage、20,000 步 stress、240 次并发写、100 次资源循环和 107 项
  gameplay 全部通过；JUnit/JSON 编排结果正常生成。

## 第二轮复查

- 发现 v6 `state_merge_receipts.payload_hash` 是旧整包 hash，不能直接迁成 schema 3 component
  hash，否则旧 journal 会冲突。升级现在丢弃这类缓存回执，以 baseline 为权威并安全重放单调值；
- 发现坏 `result_json` 的 apply 路径最初会把原 operation winner 改成 baseline。现在只修结果缓存，
  保留 winner ID/revision，重复写不增加业务版本；
- profile receipt 的 hash 不再包含 score 会更新的 `last_used`，避免一次结算错误淘汰更早但有效的
  昵称 journal；查询结果仍从业务行刷新当前 last-used；
- deferred initialization 在重新打开数据库后刷新 state high-water；status/export/preview 使用
  `initialize=False`，不会以“查看数据”为名触发迁移；
- 上述边界新增 4 项测试，第十轮定向用例现为 22 项；完整第二轮验证通过。

## 最终验证

- `run_tests.sh`：107 项 gameplay、128 项 storage/迁移/生命周期、固定 seed 20,000 步全部通过；
- stress：240 次并发写入 `integrity_check=ok`，资源循环 100 次 FD 19→19；
- 渲染 p95：Tetris 2.481 ms、Snake 2.333 ms、2048 1.517 ms、Sokoban 1.676 ms、
  Zuma 4.445 ms；本机保存 p99 2.855 ms，持锁异步提交 p99 0.078 ms；
- Ruff、compileall、shell 语法、whitespace、118 行优化矩阵计数全部通过；
- `tests.release full` 成功生成 JUnit/JSON；pip-audit 未发现已知漏洞；wheel smoke 生成
  141,569 字节 wheel；
- 提交 `de04abc` 已推送到 `origin/main`；GitHub Actions CI #24 的 release-gate、core-only、
  Python 3.12/3.13、Ubuntu、macOS、Windows 共 7 个 job 全部成功。
