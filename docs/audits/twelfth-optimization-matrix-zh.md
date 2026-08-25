# 第十二次优化任务矩阵

状态只描述当前仓库：`完成` 表示已有实现和回归；`部分完成` 表示交付物的一部分可验证；`保留`
表示建议合理但尚无实现；`需权限/权利人` 表示不能由代码提交替代。

## P0

| ID | 状态 | 证据或结论 |
|---|---|---|
| CG12-P0-01 | 完成 | `ImportTransaction` phase journal；异常退出自动回滚 |
| CG12-P0-02 | 完成 | pending/evidence 与目标原件在 DB commit 前 staging |
| CG12-P0-03 | 完成 | post-commit 文件失败恢复 DB 与 journals |
| CG12-P0-04 | 完成 | `MAX_PENDING_ATTEMPTS=10000`，preview/apply 共用 parser |
| CG12-P0-05 | 完成 | `set_attempt_count_max()` 一次原子替换 |
| CG12-P0-06 | 完成 | schema 1/2/3 revision 共用 int64 边界 |
| CG12-P0-07 | 完成 | state/score 时间限于 0–1e20 |
| CG12-P0-08 | 完成 | planner 使用最终 parser、validator 和 scratch apply |
| CG12-P0-09 | 完成 | `.reject-*.txn` 记录 rejected/previous |
| CG12-P0-10 | 完成 | 启动扫描 transaction marker 和旧 `.restore` |
| CG12-P0-11 | 完成 | rollback 失败的 previous 进入 `_non_durable_state` |
| CG12-P0-12 | 部分完成 | 覆盖模拟 ENOSPC、阶段 crash、huge values；未在真实满盘卷运行 |

## P1

| ID | 状态 | 证据或结论 |
|---|---|---|
| CG12-P1-01 | 完成 | import 先拿双层独占锁，再初始化/migrate store |
| CG12-P1-02 | 完成 | backend 生命周期 application lease；CLI 要求关闭应用 |
| CG12-P1-03 | 完成 | manifest 有 complete/source/included/omitted/reasons |
| CG12-P1-04 | 完成 | 默认 full export 遇遗漏失败；仅 `--allow-partial` 放行 |
| CG12-P1-05 | 完成 | state 总量越界停止扫描，不 quarantine 当前文件 |
| CG12-P1-06 | 完成 | score read-only snapshot 返回一致计数和原因 |
| CG12-P1-07 | 完成 | `snapshot_envelopes/snapshot_entries` 不迁移、不隔离 |
| CG12-P1-08 | 完成 | pending score 在 scratch 调用 `record_mutation` |
| CG12-P1-09 | 完成 | pending state 在 scratch 调用 `apply_state_operation` |
| CG12-P1-10 | 完成 | state 按 profile 优先级预演，缺 profile 在 preview 失败 |
| CG12-P1-11 | 完成 | `restore-replace --apply`，完整 archive 才可使用 |
| CG12-P1-12 | 部分完成 | 返回 plan fingerprint，apply 锁内重规划；无外部 plan JSON |
| CG12-P1-13 | 完成 | 高于当前 store schema 的 v2 archive 默认拒绝 |
| CG12-P1-14 | 完成 | manifest 记录 application ID 与五个 ruleset |
| CG12-P1-15 | 部分完成 | 2048 game/import 共用完整 validator；其他游戏尚无 slot schema |
| CG12-P1-16 | 完成 | POSIX relative；拒绝 drive、ADS、保留名和危险分段 |
| CG12-P1-17 | 部分完成 | resolve containment、普通文件检查、原子 UUID temp；未用全链 `openat` |
| CG12-P1-18 | 完成 | preview/import/result 继续报告 recovery omission |
| CG12-P1-19 | 保留 | 当前 JSON export 仍一次持有各表；已有严格 128 MiB/表预算 |
| CG12-P1-20 | 保留 | import 仍一次解析 JSON；已有深度、节点和总字节预算 |
| CG12-P1-21 | 完成 | archive/table/string/node/pending/evidence 均有总资源配额 |
| CG12-P1-22 | 完成 | export 临时文件含 PID 与 UUID |
| CG12-P1-23 | 完成 | README/manifest 区分 evidence 与 active state |
| CG12-P1-24 | 完成 | schema 2/3 越界回归；schema 1 转换钳制到 int64 |
| CG12-P1-25 | 完成 | score `created_at` 与 store 时间范围一致 |
| CG12-P1-26 | 完成 | permanent reject 保留 quarantine evidence 与 previous marker |
| CG12-P1-27 | 完成 | maintenance timeout callback 进入 score non-durable recovery |
| CG12-P1-28 | 完成 | 普通锁等待 5 秒，close 预算 10 秒，不再等待 300 秒 |
| CG12-P1-29 | 完成 | 150 ms quiet + 1.5 s max dirty age |
| CG12-P1-30 | 完成 | 单 in-flight Future，dirty state 完成后 coalesce 提交 |
| CG12-P1-31 | 完成 | `spec.md` 明确每档案单 autosave/单 owner 策略 |
| CG12-P1-32 | 完成 | 只读 legacy inspector 返回迁移状态和缺表 |
| CG12-P1-33 | 完成 | 全部 protected ID 进入 SQLite temp table，无 1,000 上限 |
| CG12-P1-34 | 部分完成 | data CLI/import transaction 可作为 service；尚无数据管理 GUI |
| CG12-P1-35 | 需仓库权限 | 代码不能代替 main required checks 设置；推送后核验 |
| CG12-P1-36 | 完成 | 三平台 Python 3.11 job 都运行 wheel+sdist smoke |
| CG12-P1-37 | 保留 | 精确版本闭包已有；跨平台 `--require-hashes` 尚未生成 |
| CG12-P1-38 | 完成 | `release-installed-packages.json` 与 SBOM 同时归档 |
| CG12-P1-39 | 完成 | 只读 cwd，用户 DB 在 `GAMES_DATA_DIR`，安装目录外 |
| CG12-P1-40 | 完成 | 构建 sdist、隔离构建 wheel、安装并运行入口 |
| CG12-P1-41 | 需权利人 | `docs/release-governance.md` 有 owner checklist；未擅自授权 |
| CG12-P1-42 | 完成 | `NOTICE.md` 记录当前无外部素材和新增素材登记规则 |
| CG12-P1-43 | 完成 | 治理清单单列五个名称、图形、商店文案和商标复核 |
| CG12-P1-44 | 完成 | merge/replace archive、pending、阶段 crash 和 rollback 演练 |

## P2

| ID | 状态 | 证据或结论 |
|---|---|---|
| CG12-P2-01 | 保留 | `store.py` 仍大；需按 migrations/attempts/state 拆分且行为不变 |
| CG12-P2-02 | 保留 | backend 的 worker/spool/status 仍在同模块；本轮避免高风险搬迁 |
| CG12-P2-03 | 部分完成 | phase transaction 与 slot validator 已独立；planner/archive 仍在 data CLI |
| CG12-P2-04 | 部分完成 | ProfileController 已封装主要档案 Future；launcher 仍有部分组合逻辑 |
| CG12-P2-05 | 部分完成 | 已能新建、切换、改名和显示进度；尚无独立档案列表页 |
| CG12-P2-06 | 保留 | 当前没有面向用户的删除档案流程；新增时必须先 export |
| CG12-P2-07 | 保留 | merge planner 已有，档案冲突预览 UI 尚未实现 |
| CG12-P2-08 | 部分完成 | status/export/import/recover service 已齐；桌面数据管理页尚无 |
| CG12-P2-09 | 保留 | SaveState 已枚举保存状态；五款游戏自身状态仍为字符串 |
| CG12-P2-10 | 部分完成 | BaseGame 统一成绩提交；完整五游戏 AttemptSaveController 尚无 |
| CG12-P2-11 | 部分完成 | GameDataService 统一 setting/progress/slot；控制器层未完全抽取 |
| CG12-P2-12 | 部分完成 | 各游戏已有动作边沿和输入清理；尚无跨五游戏 InputManager |
| CG12-P2-13 | 部分完成 | 档案名支持 TEXTINPUT/IME；选择、组合区显示和完整编辑仍不足 |
| CG12-P2-14 | 保留 | 无键位重映射 UI、冲突检测和恢复默认 |
| CG12-P2-15 | 部分完成 | 游戏内主要操作可键盘完成；launcher 焦点模型不完整 |
| CG12-P2-16 | 保留 | 尚无 launcher/五游戏统一手柄层 |
| CG12-P2-17 | 保留 | 没有 BGM/SFX 系统；当前无音频设备也不会初始化音频内容 |
| CG12-P2-18 | 保留 | 仍以各游戏固定 canvas 为主，未完成全局逻辑分辨率 |
| CG12-P2-19 | 部分完成 | pygame 缩放与清晰字体已有基础，未做三平台 DPI 专项验证 |
| CG12-P2-20 | 部分完成 | 系统中文字体链已有；无权利明确的内置 fallback 字体 |
| CG12-P2-21 | 部分完成 | Zuma 球有形状符号；其他依赖颜色的状态仍需逐屏审查 |
| CG12-P2-22 | 保留 | 尚无高对比和降低动态设置 |
| CG12-P2-23 | 部分完成 | 固定 seed 测试可复现；运行时 Clock/RNG 尚未统一注入 |
| CG12-P2-24 | 部分完成 | 多个规则函数可 headless 测试；还不是五个独立纯 Engine |
| CG12-P2-25 | 保留 | launcher 仍同时承担 app/state/render/data 组合职责 |
| CG12-P2-26 | 部分完成 | 首页已有 best/recent/profile/progress，continue 信息仍可加强 |
| CG12-P2-27 | 保留 | 现有绘制满足预算；未凭无 profiler 证据增加缓存失效复杂度 |
| CG12-P2-28 | 保留 | Zuma 多 reaction 已正确；显式 FSM 与属性测试尚未抽取 |

## P3

| ID | 状态 | 证据或结论 |
|---|---|---|
| CG12-P3-01 | 保留 | 7-bag 会改变随机分布，需独立可选 ruleset |
| CG12-P3-02 | 保留 | ghost/hold/lock delay 是合理舒适功能，需规则与输入测试 |
| CG12-P3-03 | 保留 | Snake 模式需分离 ruleset 和最佳成绩维度 |
| CG12-P3-04 | 保留 | 双人同屏方向合理，需独立碰撞和结算设计 |
| CG12-P3-05 | 保留 | 2048 撤销需定义 attempt/score/autosave 语义 |
| CG12-P3-06 | 保留 | 当前单 autosave 策略明确；多槽需要查看、继续和删除 UI |
| CG12-P3-07 | 保留 | 棋盘尺寸会改变分数分布，需独立 mode/ruleset |
| CG12-P3-08 | 部分完成 | campaign/practice 数据已分开；正式关卡选择页尚无 |
| CG12-P3-09 | 保留 | 未定义星级和最佳推动口径 |
| CG12-P3-10 | 保留 | 死锁/提示需要地图分析和可关闭 UI |
| CG12-P3-11 | 保留 | XSB import/export、地图验证和编辑器尚无 |
| CG12-P3-12 | 保留 | Zuma 训练/选关需与 campaign 通关分离 |
| CG12-P3-13 | 完成 | Zuma 不只用颜色，球面形状标记已随球移动 |
| CG12-P3-14 | 保留 | 原创道具/轨道需要玩法设计和确定性测试 |
| CG12-P3-15 | 保留 | 轨道编辑器需版本化 path、边界和预览工具 |
| CG12-P3-16 | 保留 | 本机成就需持久 schema、触发幂等性和 UI |
| CG12-P3-17 | 保留 | 离线每日挑战需日期/时区 seed 与 ruleset 固定 |
| CG12-P3-18 | 保留 | replay 需确定输入日志、RNG 和兼容版本 |
| CG12-P3-19 | 保留 | 当前中文界面为主；完整 i18n 需资源抽取与长文本测试 |
| CG12-P3-20 | 保留 | Windows CI 通过，不等于已交付 installer/portable 包 |
| CG12-P3-21 | 保留 | macOS CI 通过，不等于已签名 app bundle |
| CG12-P3-22 | 保留 | Linux CI 通过，不等于已有 AppImage/桌面集成 |
| CG12-P3-23 | 保留 | 当前无 tag 自动发布 workflow，避免未授权发布 |
| CG12-P3-24 | 保留 | wheel/sdist 构建已测；公开下载校验和与签名尚无 |
