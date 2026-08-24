# 第七次审查修复记录

## 状态

- [x] 读取任务书并逐项核对 F01–F22；
- [x] 替换跨平台 request lock，加入 schema 1 pending 升级；
- [x] 完成 SQLite schema v5 状态表迁移、外键和损坏值隔离；
- [x] 收敛 profile 身份，修复 standalone/import orphan 和改名显示；
- [x] 增加 profile 启动门禁与本机档案新建、切换、改名入口；
- [x] 增加单调 progress merge、ruleset/practice 维度和进度 HUD；
- [x] 增加 keyed local-state journal；
- [x] 完成 2048 load gate、attempt 恢复、终局策略和存档校验；
- [x] 修正 schema repair、clock correction 与保存状态重建；
- [x] 补充第七轮升级、锁、档案、进度、故障注入和 2048 用例；
- [x] 完成第一轮独立复查并重新验证；
- [x] 完成第二轮独立复查并重新验证；
- [x] 更新逐条审查结论并执行最终检查；
- [x] 创建提交并推送到 `origin/main`。

## 已确认的取舍

- HTTP 保持成绩 API 调试适配器；README 明确其不含本机档案、进度和存档能力。
- 档案删除与合并会影响历史归属，当前没有导出备份和确认流程，因此本轮提供新建、切换、
  改名，不开放破坏性按钮。
- LICENSE 需要仓库所有者确认代码和素材权利，不由维护改动代选。
- 任务书中的音效、手柄、安装器、更多关卡和玩法提示属于可选发行工作；没有把它们冒充为
  本轮数据兼容缺陷，也没有为赶清单而改变现有 ruleset。

## 初版验证

- 新增的 schema 1 pending、schema v2 数据库、Windows lock mock、profile FK/rename、进度持久
  日志、损坏 slot、clock correction 和 2048 slot 用例通过；
- 初版存储用例 74 项通过；
- 完整检查初次运行发现并修复两个兼容问题：档案未就绪时的一次点击需要排队启动，以及
  `.json` 形式 outbox 路径必须继续映射到同名目录。

## 第一轮复查

- 发现 2048 从胜利界面继续后，若尚未再移动就退出，旧 slot 仍会恢复胜利界面；继续操作和
  成绩确认回调现在都会刷新 schema 2 slot。
- 发现 profile 与依赖它的 slot 同时处于 journal 时，按文件 hash 顺序重放可能先遇到 orphan；
  重放现在按 profile、setting、progress、slot 排序，profile 待处理期间不会误删子状态。
- 发现 schema v2 状态表在 profile 身份转换阶段仍可能先走旧的 `UPDATE OR IGNORE`；非 v5 状态
  表现在保留原 profile 值，交给显式迁移器转换，旧表也继续保留作为冲突恢复证据。
- 针对 latest-value 和 profile-before-slot 新增故障注入用例，Ruff 与第七轮用例重新通过。

## 第二轮复查

- 发现 `ensure_profile` 仍使用仓储自己的显示名校验，可能接受其他入口拒绝的 Unicode 控制
  字符；现统一使用 `ProfileIdentity.normalize_display_name`。
- 发现单独启动的 Sokoban/Zuma 在首次中途过关时可能还没有 profile，导致 progress 被当成
  永久 orphan；BaseGame 现在异步 ensure profile，失败时 profile journal 会先于子状态重放。
- 发现本机状态写入在 journal 校验前遇到过深或不可序列化值时可能让 Future 抛出非结构化
  异常；现转换为稳定 StoreError，并继续限制 64 KiB、深度和节点数。
- state journal 的 schema repair 也改为 60 秒恢复退避；2048 补齐 won/won_announced 交叉校验。
- 完整检查通过：107 项功能检查、76 项存储/迁移用例和固定 seed 20,000 步压力检查。

## 最终验证

- 功能检查 107 项通过；存储、升级和生命周期用例 76 项通过；
- 固定 seed `20260824` 执行 20,000 步；240 次并发写入后 `integrity_check=ok`；
- 渲染 p95：Tetris 3.031 ms、Snake 2.833 ms、2048 2.166 ms、Sokoban 2.059 ms、
  Zuma 5.269 ms；
- SQLite 持锁时异步提交 p99 0.043 ms；100 次客户端创建/关闭后 FD 19→19；
- storage + stress 分支覆盖率 62%，CI 门槛设为 60%；
- Ruff、Python 编译、shell 语法、whitespace 和 0.6.0 wheel 构建通过；`run_tests.sh` 确认
  没有修改默认成绩数据库。
