# 第十一次审查优化任务矩阵

状态说明：`完成` 表示本轮已有实现和验证；`部分` 表示有效风险已关闭但完整 UI/路线图未交付；
`保留` 表示建议合理但会改变玩法、交互或发行形态，不能作为本轮缺陷修复；`需授权` 表示需要
权利人或 GitHub 仓库设置权限。每个原任务 ID 只出现一次。

## P0

| ID | 状态 | 结果 |
|---|---|---|
| CG11-P0-01 | 完成 | export path guard 覆盖 DB 及全部 SQLite sidecar。 |
| CG11-P0-02 | 完成 | 普通现有文件默认拒绝，支持显式 `--force`。 |
| CG11-P0-03 | 完成 | archive v2 往返 score/state active pending。 |
| CG11-P0-04 | 完成 | exclusive maintenance lock 冻结正常桌面 durable writes，manifest 分列 committed/pending。 |
| CG11-P0-05 | 完成 | 显式 SQLite read transaction 形成跨表快照。 |
| CG11-P0-06 | 完成 | attempts 导入省略 surrogate ID，由目标库重新分配。 |
| CG11-P0-07 | 完成 | preview 检查 request ID/source key/attempt UUID。 |
| CG11-P0-08 | 完成 | importer 使用严格 INSERT；无 `INSERT OR IGNORE`。 |
| CG11-P0-09 | 完成 | import 只 seed 缺失 baseline。 |
| CG11-P0-10 | 完成 | baseline revision 不低于既有 winner + 1/legacy high-water。 |
| CG11-P0-11 | 完成 | baseline 按 occurred-at 截止旧 latest-value journal。 |
| CG11-P0-12 | 完成 | migration 保留 receipt high-water，启动后台扫描 pending，写前读取同 key。 |
| CG11-P0-13 | 完成 | state outbox 支持 rejected winner 隔离与 previous restore。 |
| CG11-P0-14 | 完成 | `validate_state_operation()` 在 journal replacement 前做纯语义校验。 |
| CG11-P0-15 | 完成 | 2048 takeover 使用 owner/epoch/revision/hash CAS。 |
| CG11-P0-16 | 完成 | slot schema 5 引入 owner epoch。 |
| CG11-P0-17 | 完成 | terminal missing/lower confirmed score 自动补交。 |
| CG11-P0-18 | 完成 | CLI exclusive lock 与桌面 shared application lock 已接通。 |
| CG11-P0-19 | 完成 | v9 fixtures 覆盖 path、pending、ID、baseline、rollback、CAS 与 terminal。 |
| CG11-P0-20 | 完成 | release-gate 运行 storage/stress/gameplay；任一失败即退出。 |

## P1

| ID | 状态 | 结果 |
|---|---|---|
| CG11-P1-01 | 完成 | parser 在读取前检查 128 MiB 总大小。 |
| CG11-P1-02 | 完成 | 限制深度 32、节点 250,000、字符串 12 MiB。 |
| CG11-P1-03 | 完成 | `parse_constant` 拒绝 NaN/Infinity。 |
| CG11-P1-04 | 完成 | v2 manifest hash 覆盖除 hash 字段外的完整 payload。 |
| CG11-P1-05 | 完成 | 只接受 v1/v2；future version 拒绝；v1 以 evidence-only 兼容。 |
| CG11-P1-06 | 完成 | 每表校验 columns、UUID、时间、JSON，并在 memory clone 试插入。 |
| CG11-P1-07 | 完成 | progress 调用游戏 policy，slot 校验 game 和 state-version metadata。 |
| CG11-P1-08 | 完成 | manifest 和文档区分 active pending 与 recovery evidence。 |
| CG11-P1-09 | 完成 | evidence 只恢复到 archive-ID 隔离目录，校验路径/base64/hash/配额。 |
| CG11-P1-10 | 部分 | 未引入流式 JSON encoder；每表和总 archive 已硬性封顶，消除无界内存。 |
| CG11-P1-11 | 完成 | recovery 单文件 8 MiB、总量 64 MiB、2,000 文件。 |
| CG11-P1-12 | 完成 | archive rename 后 fsync 父目录。 |
| CG11-P1-13 | 完成 | manifest 列出昵称、历史、设置、slot、evidence 隐私内容且不写绝对恢复路径。 |
| CG11-P1-14 | 完成 | 机器结果分别统计 new/exact duplicates/conflicts/invalid。 |
| CG11-P1-15 | 完成 | preview/apply 共用 `_plan_import`，apply 在锁内重新规划。 |
| CG11-P1-16 | 完成 | apply 前创建 SQLite backup；共享 planner 在提交前验证全部行和 evidence。 |
| CG11-P1-17 | 部分 | 正常桌面写只走 operation；direct API 为迁移、服务端和兼容测试保留并写 monotonic baseline。 |
| CG11-P1-18 | 完成 | slot value hash 包含 state/state version/ruleset。 |
| CG11-P1-19 | 完成 | pending high-water 后台扫描，同 key 在 clock 分配前再读取。 |
| CG11-P1-20 | 完成 | receipt rebuild 读取 legacy max revision 作为 floor。 |
| CG11-P1-21 | 完成 | permanent failure 文件移入 state quarantine，不再无证据删除。 |
| CG11-P1-22 | 完成 | receipt 健康路径只读，repair 二次事务。 |
| CG11-P1-23 | 完成 | score startup 仅廉价 `scandir`；解析/隔离/升级在 worker。 |
| CG11-P1-24 | 部分 | CLI status/export 可查看并带走 quarantine；交互式清理仍未提供，避免误删证据。 |
| CG11-P1-25 | 完成 | maintenance 保护全部 active journal component ID。 |
| CG11-P1-26 | 完成 | 既有 v8 用例覆盖 365 天清理和 active component 保护。 |
| CG11-P1-27 | 完成 | v4 读取保存的 `won_announced`，crash milestone 恢复 overlay。 |
| CG11-P1-28 | 完成 | K 只触发重新读取；不再把 cached snapshot 塞入 Future。 |
| CG11-P1-29 | 完成 | released claim 必须引用 previous epoch/revision/hash 并增加 epoch。 |
| CG11-P1-30 | 部分 | terminal 恢复为只读结果页，可明确 R 新开或 Esc 返回；删除入口未开放。 |
| CG11-P1-31 | 部分 | 单槽保持，但 CAS/epoch 阻止静默共享；多槽需独立选择与删除流程。 |
| CG11-P1-32 | 完成 | settled move 使用 150 ms debounce，close/terminal/ownership 立即 flush。 |
| CG11-P1-33 | 部分 | 已覆盖未宣布 win、未确认 score、stale CAS、release/claim；未做每条语句 crash 注入。 |
| CG11-P1-34 | 完成 | 每阶段 timeout；超时 exit 124 进入 JUnit/JSON。 |
| CG11-P1-35 | 完成 | wheel 在隔离 venv 安装依赖，检查模块和 `classic-games-data`。 |
| CG11-P1-36 | 完成待远端证据 | workflow 每次 push 运行；最终记录当前 run URL/编号。 |
| CG11-P1-37 | 需授权 | branch protection 是仓库设置；本次 push 授权不包含修改 required checks。 |
| CG11-P1-38 | 完成 | 三个 Actions 均固定 v7 当前 commit SHA。 |
| CG11-P1-39 | 完成 | constraints 固定 direct 和 transitive dependency closure。 |
| CG11-P1-40 | 完成 | release 生成 CycloneDX JSON SBOM 并上传 artifact。 |
| CG11-P1-41 | 完成 | `pip-audit` 仍为 release 阻断阶段。 |
| CG11-P1-42 | 需授权 | 权利核对清单已存在；许可证选择必须由所有者确认。 |
| CG11-P1-43 | 完成 | 素材清单确认当前无打包图片/音频/字体，系统字体不复制。 |
| CG11-P1-44 | 完成 | release governance 明列五款名称、商标、图标、截图检查。 |
| CG11-P1-45 | 完成 | 文档定义 SemVer、schema、journal schema 与 ruleset 递增条件。 |
| CG11-P1-46 | 完成 | release JUnit/JSON 统一编排 storage/stress/gameplay 和新发行阶段。 |
| CG11-P1-47 | 完成 | 评估结论：只对 merge/CAS/import 等不变量采用 model/property style，不强制迁移 pytest。 |
| CG11-P1-48 | 部分 | v6–v9 已有跨实例和 model fixtures；未增加第三方 property-testing 依赖。 |

## P2

| ID | 状态 | 结果 |
|---|---|---|
| CG11-P2-01 | 部分 | 安全 data CLI 已覆盖 status/export/preview/import/recovery；桌面 GUI 保留。 |
| CG11-P2-02 | 部分 | launcher 已支持档案新建、切换、改名与当前进度；独立列表页保留。 |
| CG11-P2-03 | 保留 | 当前没有删除档案入口，因而不存在未备份删除路径；开放删除前必须接 archive flow。 |
| CG11-P2-04 | 保留 | merge 需要逐表冲突 UX 与回滚模型；strict importer 是安全基础。 |
| CG11-P2-05 | 部分 | UUID 已区分同名身份；列表仍需短 ID/创建时间等视觉区分。 |
| CG11-P2-06 | 部分 | 已有 ProfileLoadController 和 typed async result；launcher 尚未完全拆分。 |
| CG11-P2-07 | 保留 | 字符串状态当前受集中分支和测试约束；全量 Enum 迁移收益不足以抵消本轮回归面。 |
| CG11-P2-08 | 部分 | BaseGame 已集中 attempt/revision/queued score；2048 有额外 milestone 控制。 |
| CG11-P2-09 | 部分 | LocalBackendClient 已集中 state event/status/durable policy；未新增 UI controller class。 |
| CG11-P2-10 | 部分 | Sokoban/Zuma 使用 generation、merge 和 typed result；可继续抽公共 controller。 |
| CG11-P2-11 | 部分 | 2048 已有 load/retry/quarantine/ownership gate；尚未抽成跨游戏组件。 |
| CG11-P2-12 | 部分 | 各游戏 action map 明确，Tetris 逻辑边沿已修；统一 InputManager 保留。 |
| CG11-P2-13 | 完成 | launcher 已处理系统 IME composition、光标和提交事件。 |
| CG11-P2-14 | 保留 | 需设置 schema、冲突检测和五游戏 action registry，不应只改常量。 |
| CG11-P2-15 | 部分 | 游戏 overlay 有快捷键，launcher 仍以鼠标卡片为主；完整 focus model 保留。 |
| CG11-P2-16 | 保留 | 手柄需 dead-zone、设备热插拔和映射 UI，当前无半成品声明。 |
| CG11-P2-17 | 保留 | 当前不打包音频且无设备依赖，避免引入来源不明素材；系统需先定义授权素材。 |
| CG11-P2-18 | 部分 | 每款游戏固定逻辑窗口避免裁切；可缩放 canvas 需要全套输入坐标变换。 |
| CG11-P2-19 | 部分 | Zuma 轨道使用 3× 超采样；整个 launcher 的 DPI policy 保留。 |
| CG11-P2-20 | 部分 | 使用 PingFang/Hiragino/微软雅黑等系统 fallback；未捆绑未经许可字体。 |
| CG11-P2-21 | 完成 | Zuma 五种球色增加点、横线、竖线、叉和圆环标记。 |
| CG11-P2-22 | 保留 | 需要全局 accessibility settings 与动画审计，不能用单个布尔常量冒充完成。 |
| CG11-P2-23 | 部分 | regression/stress 可固定 seed；生产 Game 构造器的 RNG/clock 注入保留。 |
| CG11-P2-24 | 部分 | progress/mutation/track math 已为纯模块；五款 engine 全量抽取是架构演进。 |
| CG11-P2-25 | 部分 | profile/data/service 已拆层；launcher render/state 仍在单文件。 |
| CG11-P2-26 | 部分 | 首页已有 best/recent/progress/continue 信息；完整 dashboard 保留。 |
| CG11-P2-27 | 部分 | 字体和 Zuma 轨道已有缓存；其他 Surface 需 profiler 证明后再缓存。 |
| CG11-P2-28 | 保留 | 当前 reaction queue 行为已测试；FSM 重写需先冻结重叠反应模型。 |
| CG11-P2-29 | 部分 | `tests.stress` 输出固定步骤渲染/保存指标；尚未输出完整 OS 元数据。 |
| CG11-P2-30 | 部分 | 有 20,000 步、100 资源循环和并发写；CI 不承担 60 分钟 soak。 |
| CG11-P2-31 | 部分 | README 和 HUD 显示控制/关卡；完整游戏内规则页保留。 |
| CG11-P2-32 | 部分 | 数据损坏/存档失败有恢复门禁与提示；进程级 crash page 保留。 |
| CG11-P2-33 | 部分 | setting persistence 存在；综合窗口/按键/辅助设置页保留。 |
| CG11-P2-34 | 部分 | legacy ruleset 与当前排行分开；独立历史浏览页保留。 |

## P3

| ID | 状态 | 结果 |
|---|---|---|
| CG11-P3-01 | 完成 | Left/A、Right/D、Down/S 按逻辑 action edge 只立即执行一次。 |
| CG11-P3-02 | 保留 | 7-bag 改变随机分布，必须作为新 comfort mode/ruleset。 |
| CG11-P3-03 | 保留 | ghost/hold/lock delay 需一起定义计分、锁定和新 ruleset。 |
| CG11-P3-04 | 保留 | Snake 速度/穿墙/障碍需 mode 维度和独立最佳分。 |
| CG11-P3-05 | 保留 | 双人碰撞、输入和胜负规则未定义，不能并入 classic。 |
| CG11-P3-06 | 保留 | 2048 undo 会改变 attempt/score/slot 语义，需要新规则。 |
| CG11-P3-07 | 部分 | 数据层支持任意 slot ID；缺少浏览/选择/删除 UI。 |
| CG11-P3-08 | 保留 | 棋盘尺寸改变规则与成绩维度，应新增 mode/ruleset。 |
| CG11-P3-09 | 部分 | 当前可跳至已解锁关并标为 practice；正式选择网格保留。 |
| CG11-P3-10 | 保留 | 推动数/撤销/星级公式需先冻结计分合同。 |
| CG11-P3-11 | 保留 | 静态/动态死锁与提示是求解器功能，需独立性能和正确性验收。 |
| CG11-P3-12 | 保留 | 编辑器/XSB 需要地图闭合、字符集、版本和导入安全定义。 |
| CG11-P3-13 | 部分 | 现有关卡跳转会隔离 practice score；独立训练/选关页保留。 |
| CG11-P3-14 | 完成 | Zuma 球增加非颜色形状编码。 |
| CG11-P3-15 | 保留 | 道具和新轨道属于内容设计，需原创素材与确定性规则测试。 |
| CG11-P3-16 | 保留 | 轨道编辑器需要路径合法性、版本和预览合同。 |
| CG11-P3-17 | 保留 | 本机成就需成就 schema、幂等迁移和展示入口。 |
| CG11-P3-18 | 保留 | 每日挑战需 date/seed/timezone/ruleset 规范。 |
| CG11-P3-19 | 保留 | replay 需 action schema、RNG/clock 注入和兼容版本。 |
| CG11-P3-20 | 保留 | 本地化需资源提取、fallback 和布局基准，不适合只翻译标题。 |
| CG11-P3-21 | 保留 | Windows installer 是独立发行管线；wheel smoke 先保证 Python 包。 |
| CG11-P3-22 | 保留 | macOS app bundle 需签名、entitlement、数据目录和最低系统策略。 |
| CG11-P3-23 | 保留 | Linux 包型需确定 AppImage/Flatpak 与 pygame/SDL 依赖策略。 |
| CG11-P3-24 | 保留 | 自动发布需 owner 确认 LICENSE、标签和 release 权限后启用。 |
| CG11-P3-25 | 部分 | archive 有 hash、Actions 固定 SHA；发行文件签名需密钥托管授权。 |
| CG11-P3-26 | 保留 | 截图/GIF 需确认商标和发布素材，本轮不加入生成资产。 |
| CG11-P3-27 | 保留 | 新游戏必须同时定义 catalog/ruleset/data/input/test，本轮不扩游戏数。 |

汇总：完成 62 项、待补远端证据 1 项、部分完成 35 项、保留 29 项、需额外授权 2 项。汇总按
交付性质统计，不把“保留”包装为缺陷修复；最终以代码和测试证据为准。
