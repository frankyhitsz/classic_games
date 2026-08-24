# 第六次审查修复记录

## 状态

- [x] 逐项核对 F01–F22 和完整优化清单；
- [x] 修复 P0 存储错误分类与旧 pending 启动边界；
- [x] 完成保存事件、异步重试、迁移去重、真实结算时间和 schema v4 约束；
- [x] 完成本机档案、昵称、IME、进度、设置和存档槽的实际入口；
- [x] 合并 BaseGame/2048 保存控制流，修复零分、并列奖牌和最近记录语义；
- [x] 补充第六轮故障注入、迁移、UI 和存档用例；
- [x] 完成第一轮独立复查并重新验证；
- [x] 完成第二轮独立复查并重新验证；
- [x] 更新逐条审查结论并执行最终完整检查；
- [ ] 创建提交并推送远端。

## 实现摘要

- `StorageErrorKind` 按 SQLite 基础错误码识别 busy/full/read-only/I/O/cant-open/
  corrupt/constraint/schema/interrupted；只有 mutation 语义错误允许移除 pending。
- legacy pending 使用严格 JSON 和有限数规则，限制 4 MiB、10,000 项、32 层、100,000
  节点和 64 KiB 字符串；隔离预览有不依赖原对象可序列化的降级路径。
- `SaveEvent` 保存每个 request 的最终状态；BaseGame 和 2048 在 durable pending 后会读取
  commit/quarantine 状态。手动重试返回 Future，目录发现和 quarantine 计数在 read worker。
- pending envelope 的时间写入 attempt；重试退避避免持续撞锁。request lock 记录 PID，只有
  owner 进程已不存在时才回收，不再依据墙钟的固定 30 秒。
- schema v4 增加失效行隔离表、profiles/settings/progress/save_slots、回执过期索引、分批维护
  和 attempts 写入触发器。旧 transport ID 和显示名 profile 在迁移中修复。
- legacy extra 先检查 64 KiB 原始大小，再受限解析；旧库按内容去重，并用路径 marker 避免
  每次启动重新 hash 未变化文件。
- 启动器后台打开记录服务，使用独立 UUID 档案并持久化昵称；系统输入法通过组合文本事件输入。
- 2048 每次有效移动后写入版本化自动存档；推箱子和祖玛写入 campaign 进度。
- Flask stats 拒绝 `limit`，404/405/500 使用 JSON，StoreError details 得到保留；HTTP 失败项
  按 attempt UUID、mode、ruleset 清理。
- 版本升至 0.5.0，增加三平台 GitHub Actions、coverage XML、CHANGELOG、CONTRIBUTING 和
  SECURITY；pygame 运行依赖固定为 2.6.1。

## 第一轮复查

- 发现首次 pending 扫描改异步后，`drain()` 可能在 scan 把 replay 交给 writer 前返回；现在
  先等待 scan hand-off，再等待必要写入，关闭顺序也保证 scan 不能向已关闭 writer 提交。
- 发现惰性启动后 profile/progress/save-slot 包装器仍可能在 pygame 调用线程同步重开数据库；
  现统一把重开和具体读写放入对应 worker。
- 发现启动时不再同步统计 quarantine 会丢失旧隔离提示；增加后台有界统计并刷新恢复通知。
- 发现启动档案读取晚到会覆盖玩家已经输入的昵称；用户开始编辑后锁定本次档案选择。
- 发现 2048 重置时旧 load Future 可能在新棋盘生成后回灌；重置会取消应用旧结果并立即覆盖
  自动存档。

## 第二轮复查

- 发现旧 32 字符非十六进制 profile 会绕过仅按长度判断的迁移；现按完整 UUID 形状检查，旧
  显示名和自定义旧 ID 都映射为确定性 UUID。
- 发现 imported legacy attempt 仍可能带显示名 profile；mutation 默认身份也改为确定性 UUID，
  schema 迁移在行隔离前先修 profile。
- attempts 约束补齐 transport ID 字符/长度和时间顺序；迁移器先修旧短 ID，避免把可恢复历史
  当坏行删除；外部旧库的 updated time 不早于 created time。
- trigger 快路径不再只看名称，还核对约束 SQL 的关键内容；坏行隔离改为每批 500 条。
- 修复 IME 组合文本在光标闪烁时消失、组合期间退格误删已提交文本的问题。

## 验证结果

- 功能检查 107 项通过；
- 存储、迁移和生命周期用例 60 项通过，其中第六轮新增 20 项；
- 固定 seed `20260824` 执行 20,000 步；
- 渲染 p95：Tetris 2.405 ms、Snake 2.241 ms、2048 1.530 ms、Sokoban
  1.109 ms、Zuma 4.441 ms；
- 本地保存 p95 2.112 ms、p99 2.487 ms；SQLite 持锁时异步调用 p99 0.034 ms；
- 100 次客户端创建/关闭后 FD 19→19；240 次并发 SQLite 写入完成，
  `integrity_check=ok`；
- 16 个直接 SQLite writer 的压力分支使用 2 秒 busy budget；桌面路径仍使用单 writer 和
  250 ms budget，不以放宽延迟门槛掩盖 pygame 卡顿；
- Ruff、Python 编译、shell 语法、wheel 构建、whitespace 检查通过；
- `data/scores.db` SHA-256 仍为
  `e0ae24d4f1361b98e009c7d158f060beff01a8e6b41bed9fa3b2c4c539ec42ca`。

详细判断见 `classic_games_sixth_code_review_response_zh.md`。
