# 第十四次审查修复记录

## 状态

- [x] 建立 F01–F28 与 P0–P3 共 123 项证据矩阵；
- [x] 完成 recovery handoff、reject v3 和 2048 owner claim P0 修复；
- [x] 完成 archive v3、v2 upgrader、ruleset 与 transaction 兼容修复；
- [x] 完成 secure roots、legacy evidence、完整 schema replace 和 cleanup 加固；
- [x] 分离 Sokoban campaign/practice，并统一独立启动恢复页；
- [x] 写入逐条答复、优化矩阵、规格、协议、README 和 CHANGELOG；
- [x] 第一轮复查：storage、玩法状态机和旧回归语义；
- [x] 第二轮复查：stress、package/release、资源与跨平台边界；
- [x] 提交、推送并核验最终远端 CI。

## 已完成的验证

- Ruff 通过；client/game_service/server 完整 compileall 通过；
- 208 项 storage 通过，本轮新增 27 项；本机仅跳过 Windows junction 专用用例；
- 第一轮 gameplay 发现旧测试仍假设 N 可跳过未解锁关。产品规则已明确为 practice 也受 unlock 约束，
  测试改为先解锁再验证 N，而不是删除断言；单项复验通过；
- 第一轮代码复查发现 campaign/practice 共用 generation 会使交错的 campaign load 被误判过期；已拆成
  两个 generation，并加入 pending campaign load 与 practice write 交错测试。
- 第二轮完整 release 检查通过：Ruff、依赖漏洞扫描、SBOM、compileall、wheel/sdist 安装冒烟、
  发布清单、storage、stress 与 107 项 gameplay 均无失败；资源检查前后文件描述符均为 19。
- 第二轮差异复查发现 README 漏列 `test_storage_v12.py`，且新增用例统计仍为 26；已同步目录和实际的
  27 项统计，并重新执行文档差异检查。
- 首次远端 `core-only` 发现新增的 `server.init_db()` 用例未遵守 Flask 是可选依赖的测试契约；修正为
  API 依赖缺失时跳过，并分别验证了有 Flask 执行和无 Flask 跳过两条路径。
- 修复提交 `d11c242` 的 GitHub CI 7 个结果全部成功：release gate、core-only、Python 3.12/3.13，
  以及 Linux、macOS、Windows 全量矩阵；Windows junction 专用用例随 storage suite 通过。

## 核心不变量

- recovery 完成到 shared application session 之间没有 unlock/reacquire；
- previous pending 在 incoming 替换前已经进入可校验 marker；
- claim 的成功依据是权威 owner token/epoch，而不是 `ok=true`；
- format-less v2 不会因升级命令而获得无法证明的 replace 权限；
- historical ruleset 数据保留，但不冒充当前规则成绩；
- 多 transaction 不猜 rollback 顺序，v1 未认证 bytes 不自动使用；
- practice 不改变 campaign total/completed/unlock；
- replace 后 schema object 集合与当前空白数据库一致。

## 尚需外部决定

- GitHub main required checks/branch protection；
- 三平台 `--require-hashes` lock、native installer、签名和自动 Release；
- LICENSE、名称/商标和素材权利结论。
