# 第七次代码审查核对结果

本次核对以当前仓库、可执行测试和故障注入为准。任务书对升级兼容和本机状态可靠性的判断
大多成立，但部分工程与发行建议不是当前缺陷，或者需要仓库所有者作产品、法律或平台决策。
完整 P0–P3 清单逐项状态见 `seventh-optimization-matrix-zh.md`。

## F01–F22

| 项目 | 判断 | 处理结果 |
| --- | --- | --- |
| F01 Windows `os.kill(pid, 0)` | 成立，且在 Windows 上风险严重 | 删除 PID 探测。Windows 使用 `msvcrt.locking`，POSIX 使用 `flock`；测试确认 Windows 分支不调用 `os.kill`。 |
| F02 旧 per-request pending 被隔离 | 成立 | pending schema 升至 2。schema 1 先按旧 hash 校验，显示名 profile 转稳定 UUID，重算 hash；原文件进入 migration-backup。 |
| F03 schema v2 不能直升 | 成立 | SQLite schema 升至 v5。旧状态表先改名，再显式转换；迁移前备份，转换和版本写入同一事务，重复初始化不会再次迁移。含 attempts 的 v2 fixture 已覆盖。 |
| F04 profile 未解析即可启动 | 成立 | 档案未确认时卡片显示准备中；点击会排队，只有 ensure 成功才启动。保存失败保持门禁，下一次点击会重试。 |
| F05 profile 规则重复 | 成立 | 增加 `ProfileIdentity`，mutation、standalone attempt、旧 pending 和数据库迁移共用 UUID/Unicode 规则。 |
| F06 standalone/import 产生 orphan | 成立 | `record_mutation` 和 legacy import 在写 attempt 前插入 profile；状态子表通过外键拒绝 orphan。 |
| F07 改名后榜单仍显示旧名 | 成立，但需先定语义 | 采用“当前档案名展示、attempt 保留结算时名字”的策略，榜单和最近记录 join profiles；见 `docs/adr/profile-display-name.md`。 |
| F08 progress 可回退 | 成立 | 增加单调 merge：数值取最大、布尔取或、关卡集合取并集、各关分数逐项取最大；Sokoban 练习与 campaign 分键，progress 按 ruleset 分区。 |
| F09 本机状态写入不持久 | 成立 | 增加 keyed latest-value journal。profile/settings/progress/save-slot 先原子写日志，成功落库后按 hash 删除；锁库测试验证解锁后可补写。 |
| F10 2048 载入前接受输入 | 成立 | slot 状态为 loading 时只允许退出，移动、重开、暂停和鼠标滑动不会改棋盘，并显示载入提示。 |
| F11 2048 未恢复 attempt 身份 | 成立 | slot schema 2 保存并恢复 attempt UUID、revision、submission id 和已确认分数。 |
| F12 2048 终局 slot | 成立 | win/gameover 转态后再写一次 slot；gameover slot 仅作终局证据，启动时生成新棋盘并覆盖，不作为 playing 恢复。 |
| F13 slot 校验不足 | 成立 | 校验版本、ruleset、4×4 形状、非空单元数量、二次幂和上限、分数范围、won/最大块一致性、状态和 attempt 元数据。坏 JSON 在 store 层隔离。 |
| F14 metadata 前崩溃锁死 | 成立，与 F01 同根 | 新协议不写 PID metadata。进程退出时内核自动释放锁；残留或畸形锁文件可重复加锁。 |
| F15 shared pending 非全成功提交 | 成立 | 只有每项已迁移或已成功隔离才重命名源文件；瞬时写入/隔离失败保留原文件，下次再试。 |
| F16 schema repair 状态和节奏 | 成立 | schema repair 使用 `recovery_required`，pending 保留；自动重试采用 60 秒恢复退避，不再按普通 durable pending 每两秒碰库。 |
| F17 时钟回拨 | 成立 | 合法 pending 的未来完成时间被规范为当前时间，并以 `clock_adjusted` 披露；不会因此删除 pending。 |
| F18 状态外键和坏值恢复 | 成立 | 三张子表增加 profile 外键和 cascade、结构版本；progress 增加 ruleset；坏 JSON 原文进入 `invalid_local_state` 后回默认值。 |
| F19 本机档案 UI | 部分成立 | 本轮完成新建、轮换、改名和关卡进度 HUD。删除与合并会改变历史归属，尚无导出备份与二次确认，因此没有放一个容易误删数据的按钮；这不是用“独立产品”回避，而是缺少安全前置条件。 |
| F20 HTTP 能力不等价 | 事实成立，要求等价则不合理 | Flask 明确是成绩 API 调试适配器，不是第二套本机状态后端。README 已列明降级范围，默认桌面功能不受 optional HTTP 牵制。 |
| F21 CI 未闭环 | 部分成立 | 增加 Python 3.12/3.13 兼容任务；coverage 纳入 storage 和 stress，依据本地实测 62% 设置 60% 门槛。远端完成状态和 branch protection 属于 GitHub 仓库设置，不能从代码假定已开启。 |
| F22 根目录审查材料 | 成立 | 第七轮任务书和结果移至 `docs/audits/`；用户已删除的旧轮次根目录材料保持删除。 |

## 其他建议

以下建议合理，但不应伪装成本轮已经完成：档案导出后删除/合并、恢复页面、结构化旋转日志、
pytest 全量迁移、类型检查、属性测试、依赖锁、安装器、签名公证、手柄与音效。它们分别需要
数据恢复设计、维护预算或真实平台凭据。

任务书建议直接补 LICENSE，但仓库现有代码和视觉素材的权利来源没有足够证据。许可证是权利
声明，不是普通技术默认值；由仓库所有者确认后再加入才可靠。更多关卡、提示系统和玩法改动
也不是已复现 bug，若实施必须同步新的 ruleset 和验收用例，否则会污染现有排行榜语义。

## 验证

- schema 1 pending 原件、hash 和身份转换；
- schema v2 状态表及带 attempt 的升级、备份和幂等；
- Windows 锁分支、坏锁文件、shared pending 瞬时失败；
- profile 自动创建、改名显示、外键拒绝 orphan；
- progress 单调合并、ruleset 隔离、锁库后 durable replay；
- 2048 load gate、attempt 恢复、terminal policy 和坏 slot 隔离；
- schema repair 状态/退避、clock correction、缓存淘汰后的 pending 状态重建；
- 原有玩法、渲染、网络边界、迁移和并发压力检查。

最终命令与性能数据在 `task.md` 记录，避免在审查结论中复制一长段容易过期的终端输出。
