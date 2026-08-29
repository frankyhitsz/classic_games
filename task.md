# 第十七次审查修复记录

## 状态

- [x] 完整读取 2014 行任务书并锁定当前基线 `4e723c7`；
- [x] 首轮核验五个 P0 族及 F01–F30 当前控制流；
- [x] 完成全部 F01–F30 合理修复和实现方式修正；
- [x] 建立逐 Finding 答复与 167 项 P0–P3 优化矩阵；
- [x] 第一轮定向、storage、gameplay 验证；
- [x] 第二轮独立复查、stress、release 和打包验证；
- [ ] 提交、推送并确认 GitHub CI。

## 首轮核验结论

- aggregate 确实没有在 LWW 前识别自身 component；普通 `set_progress` replay 也不查询
  `state_merge_receipts`，可确定性回退已合并进度；legacy v1 upgrade 另走 merge helper；
- `list_entries` 首次 lock timeout 后会二次取锁并盲目隔离，reject marker/restore timeout 也落入 invalid；
  reject writer 使用固定 temp，scanner 缺少同一 key lock；
- exporter 只检查 128 MiB byte limit，而 reader 对整个 JSON 限制 250,000 nodes，成功发布后可自拒绝；
- terminal transaction 在 active namespace 内 `rmtree(ignore_errors=True)`，部分删除能留下无 journal 的
  `.import-*`；export 和 startup 对 terminal root 的判断不一致；
- 2048 在 `_move` 中先加分、移动 grid，动画结束才 double tile 和 spawn，`before_close` 会保存半结算状态；
- Store commit 后 score/state cleanup 的新锁异常未被完整捕获；event cache 同 identity 无终态优先级；
  worker timeout 后仍会提前释放 application lease；score request lock 文件无界累积；
- historical pending 确实 parse-first；`inspect-archive` 先 resolve final symlink；transaction JSON reader/writer
  缺 shape、post-fstat、operation/encoded-size 对称门禁；
- Sokoban 确实只恢复 `attempt_context`，忽略 BaseGame 私有提交 identity；outer ruleset 未检查，恢复后立刻
  tombstone；validator 未验证 ledger 总和、exact int 和逐步可达性；
- 推箱子的同步 `publish_slot_intent` 已是 durable，这一点比任务书表述更强；真正缺口是异步 adapter
  把 Future 创建等同 durable，以及恢复点的后续生命周期。

## 已实施

- progress aggregate/component dominance、hash conflict、Store set receipt 检查和 v1 共享 resolver；
- merge component receipt 与 authoritative state 同生命周期，不再按 365 天任意失效；
- reject unique temp、digest lock、grace/fingerprint，scanner/marker/restore 对 BUSY 保留；
- state parser 去除 wall-clock 依赖，clock quarantine 失败 fail closed，event reducer 终态优先；
- archive node 预算在发布前检查并完成内存 reader round-trip；增加 `verify-archive`，修正 final symlink 与
  historical classify-first，SQLite 动态类型返回结构化 source error；
- terminal root 原子迁移到 transaction-cleanup namespace；共享 transaction classifier；transaction reader/
  writer 增加 shape、constant、post-fstat、operation count 和 encoded-size 门禁；
- 2048 保留 pre-move settled snapshot，动画中退出不再保存半结算 board；旧 ruleset slot 先隔离再新开；
- commit/cleanup 分离；score lock 改为 256 stripes并增加 lock inventory/cleanup 命令；lease 等 worker 真正结束
  后再释放；
- BaseGame 增加统一 attempt identity restore 和 before-close structured warning；Sokoban 加固 ruleset、ledger、
  reachability、attempt、tombstone 与 async durability。

## 两轮复查发现

- 第一轮完整运行发现 event queue 为防同 identity 回退而过度丢弃“较旧 operation 的 superseded 通知”，
  已改为只抑制同 identity 回退，保留不同 operation 的结果事件；
- 第二轮逐文件复查发现 historical score planner 仍是 parse-first、hash-only cleanup apply 仍回到 8 MiB
  reader、Archive v4 CLI 文案残留、2048 v6 takeover 的 owner epoch 被归零、旧 ruleset 仍在 current parser
  之后分类，以及 attempt revision 未同步 63-bit 上限；均已修复并增加定向检查；
- state recovery 原实现只有各子扫描单独 250 ms，不能保证首帧总预算；现由 orphan、marker、restore 和 count
  共享同一个 250 ms deadline，达到预算后保留未处理文件并显示恢复提示。

## 最终验证

- Ruff 与 compileall 已通过当前修改文件；
- `test_storage_v15.py` 新增 34 项定向检查；完整 storage 294 项通过、1 项 Windows junction 专用跳过；
- `run_tests.sh`：gameplay/API 107 项通过；storage 294 项通过、1 项平台跳过；20,000 步 stress、
  100 次资源循环、240 次并发写入与 SQLite integrity check 通过；
- release profile 全阶段通过：Ruff、依赖漏洞审计、CycloneDX SBOM、compile、wheel/sdist 安装、
  只读用户数据冒烟、release manifest、storage、stress、gameplay；
- Archive v4、slot v6 与包版本同步到 0.9.0；README、CHANGELOG 和 storage protocol 已更新；
- 远端 CI 在推送后核对。
