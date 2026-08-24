# 第十次优化任务处理矩阵

状态含义：`完成` 表示代码或文档已经落地并有相应验证；`部分` 表示本轮关闭了可交付子项但仍有
明确余项；`接受` 表示建议合理并保留，不能写成当前已有能力；`不采用` 表示建议与已验证约束
冲突；`外部` 表示需要仓库所有者或权利人授权。

## P0

| ID | 状态 | 处理结果 |
| --- | --- | --- |
| CG10-P0-01 | 完成 | aggregate 使用 `aggregate-<component digest>`，不复用两个贡献 ID。 |
| CG10-P0-02 | 完成 | journal schema 3 与 merge receipt 逐 component 记录 ID/hash。 |
| CG10-P0-03 | 完成 | `test_committed_component_and_late_merge_are_both_preserved` 覆盖提交后未 unlink。 |
| CG10-P0-04 | 完成 | schema 7 receipt 保存业务引用与 `value_hash`，duplicate 前核对权威行。 |
| CG10-P0-05 | 完成 | getter/显式 slot quarantine 同事务失效普通和 merge receipt。 |
| CG10-P0-06 | 完成 | 损坏 progress 原值先隔离，incoming merge 从空值安全恢复。 |
| CG10-P0-07 | 完成 | setting 缺行、slot 隔离及 journal 重建有故障用例。 |
| CG10-P0-08 | 完成 | v6→v7 升级为既有 profile/setting/progress/slot 生成 synthetic baseline。 |
| CG10-P0-09 | 完成 | backend 启动读取 DB state high-water，新 revision 不低于基线。 |
| CG10-P0-10 | 完成 | fixture 验证旧 profile、setting、slot operation 均被 baseline 淘汰。 |
| CG10-P0-11 | 完成 | 第十轮定向套件覆盖 commit-before-unlink、隔离、坏回执和升级。 |
| CG10-P0-12 | 完成 | CI 新增独立 `release-gate`，执行 release profile。 |

## P1

| ID | 状态 | 处理结果 |
| --- | --- | --- |
| CG10-P1-01 | 完成 | verifier 检查 receipt 列、PK、CHECK 和维护索引。 |
| CG10-P1-02 | 完成 | 畸形 v7 receipt 表先备份数据库，再显式重建并播种 baseline。 |
| CG10-P1-03 | 完成 | 坏 `result_json` 从权威业务行重建。 |
| CG10-P1-04 | 完成 | late merge 更新 winner value hash/result，duplicate 返回当前值。 |
| CG10-P1-05 | 完成 | 状态重建检查所有 merge component receipt。 |
| CG10-P1-06 | 完成 | 新增 `(semantic_key, applied_at)` 索引。 |
| CG10-P1-07 | 完成 | 365 天、每批 500 条清理，pending component 受保护。 |
| CG10-P1-08 | 完成 | 丢失、坏值、负数和超大 clock 以 DB high-water 恢复。 |
| CG10-P1-09 | 完成 | 坏 clock 保存在 `pending-state-quarantine` 并进入 recovery notice。 |
| CG10-P1-10 | 完成 | `docs/architecture/state-ordering.md` 记录 commit-order/user-time 语义。 |
| CG10-P1-11 | 完成 | 2048 owner CAS 阻止旧活动实例覆盖新 owner。 |
| CG10-P1-12 | 完成 | direct store 写保留兼容 API，但同事务生成 baseline，不再绕过 receipt。 |
| CG10-P1-13 | 完成 | duplicate、receipt rebuild、stale/no-op score 不改 `last_used`。 |
| CG10-P1-14 | 完成 | 新 attempt 和有效 update 使用 `occurred_at` 更新档案。 |
| CG10-P1-15 | 完成 | 无 `profiles` capability 时档案按钮禁用且游戏接收 `profile_id=None`。 |
| CG10-P1-16 | 完成 | helper 测试显式本机 ID 与 HTTP 省略路径；既有 API 测试保留按名派生。 |
| CG10-P1-17 | 完成 | Sokoban/Zuma 兼容裸值和 `result.value`。 |
| CG10-P1-18 | 完成 | 定向测试验证写回的较高关卡/最高分即时进入 HUD。 |
| CG10-P1-19 | 完成 | ADR 明确终局存档可查看，重开才覆盖。 |
| CG10-P1-20 | 完成 | 终局恢复原棋盘、分数和 gameover overlay。 |
| CG10-P1-21 | 完成 | autosave schema 4 包含 owner token、active/released、takeover。 |
| CG10-P1-22 | 完成 | 冲突遮罩提供 K 接管和 Esc 返回；未接管时写入门禁。 |
| CG10-P1-23 | 部分 | `data_cli status` 显示 quarantine/backup 路径、数量和大小；未加启动器状态页。 |
| CG10-P1-24 | 完成 | `export --include-recovery` 可把有限大小的隔离原文写入 base64 归档。 |
| CG10-P1-25 | 部分 | status 列出 migration backup，import 自动建备份；任意旧备份原地恢复命令未加。 |
| CG10-P1-26 | 部分 | invalid rows 可查看并随 archive 导出；破坏性批量清理未加。 |
| CG10-P1-27 | 完成 | archive 覆盖 attempts/profile/progress/slot/settings 与 invalid rows。 |
| CG10-P1-28 | 完成 | preview 统计 new/conflict/invalid；`--apply` 原子插入、现有行优先、操作前备份。 |
| CG10-P1-29 | 接受 | 当前仍以终端错误和 recovery notice 为主；旋转结构化日志尚未实现。 |
| CG10-P1-30 | 待推送 | 本地验证完成后推送并记录本次 head 的实际 CI 结果。 |
| CG10-P1-31 | 外部 | branch protection 需要仓库设置授权，本轮未修改。 |
| CG10-P1-32 | 完成 | workflow 顶层权限收紧为 `contents: read`。 |
| CG10-P1-33 | 接受 | Actions 使用明确 major 版本但未锁 commit SHA；需单独维护可信 SHA 清单。 |
| CG10-P1-34 | 完成 | `tests.release` 提供 fast/full/release 三个统一入口。 |
| CG10-P1-35 | 完成 | orchestrator 生成 JUnit XML 和 JSON，CI 始终上传。 |
| CG10-P1-36 | 完成 | 原有 gameplay coverage 汇总和 60% 总门槛保留。 |
| CG10-P1-37 | 接受 | 90% core/80% 全项目是阶段目标，当前没有虚报达到。 |
| CG10-P1-38 | 完成 | 评估结论是仅对 merge/CAS/棋盘不变量使用属性测试，不替换现有 runner。 |
| CG10-P1-39 | 部分 | 增加 component/CAS/idempotency 确定性故障用例；尚未引入属性测试依赖。 |
| CG10-P1-40 | 部分 | `constraints-release.txt` 固定顶层发行依赖；传递依赖 hash lock 未生成。 |
| CG10-P1-41 | 完成 | release profile 使用 pip-audit 检查固定依赖，失败会阻止 release-gate。 |
| CG10-P1-42 | 完成 | README 区分 pyproject 范围、发行 constraints 与 Conda 用途。 |
| CG10-P1-43 | 完成 | release governance 提供代码作者、贡献授权和许可证选择清单。 |
| CG10-P1-44 | 完成 | 记录当前无打包外部图片/音频/字体，并规定新增素材字段和 NOTICE 生成条件。 |
| CG10-P1-45 | 完成 | 清单要求核对游戏名称、图形、商店文案和平台商标。 |
| CG10-P1-46 | 完成 | 文档明确 SemVer、schema、journal 和 ruleset 的递增规则。 |
| CG10-P1-47 | 接受 | 现有压力测试不等同 startup 历史量 benchmark，任务保留。 |
| CG10-P1-48 | 部分 | 数据 archive 在新库预览、导入和读取演练通过；DB backup+journal 整体灾备演练待补。 |

## P2

| ID | 状态 | 处理结果 |
| --- | --- | --- |
| CG10-P2-01 | 部分 | ProfileController 已管理 generation/queued launch；launcher 仍保留部分 Future 编排。 |
| CG10-P2-02 | 部分 | 已支持新建、切换、重命名；没有独立档案列表与进度页。 |
| CG10-P2-03 | 接受 | 删除档案前导出合理；当前没有档案删除入口。 |
| CG10-P2-04 | 接受 | 同名档案区分仍需 UUID 后缀或创建时间 UI。 |
| CG10-P2-05 | 接受 | 游戏状态仍使用兼容字符串，尚未统一 Enum。 |
| CG10-P2-06 | 接受 | score save 状态共享在 BaseGame，未抽成独立 controller。 |
| CG10-P2-07 | 接受 | setting/progress/slot 尚未统一为一个 LocalStateController。 |
| CG10-P2-08 | 部分 | Sokoban/Zuma 有 generation 与 result 解包，未抽公共 ProgressController。 |
| CG10-P2-09 | 部分 | 2048 已有 load/retry/new/conflict 状态机，未抽公共 SaveSlotController。 |
| CG10-P2-10 | 接受 | 五款游戏输入映射仍分散。 |
| CG10-P2-11 | 部分 | 启动器支持 TEXTEDITING/TEXTINPUT 组合文本；选择、光标移动等完整控件未实现。 |
| CG10-P2-12 | 接受 | 键位重映射、冲突检查和恢复默认尚未实现。 |
| CG10-P2-13 | 接受 | 游戏键盘可玩，但启动器菜单没有完整焦点导航。 |
| CG10-P2-14 | 接受 | 手柄层尚未实现。 |
| CG10-P2-15 | 接受 | 音频系统尚未实现。 |
| CG10-P2-16 | 接受 | 仍使用各游戏固定逻辑窗口，没有统一 scalable canvas。 |
| CG10-P2-17 | 部分 | 矢量图形随窗口重绘；高 DPI 缩放策略和真机测试未完成。 |
| CG10-P2-18 | 部分 | 有系统 CJK fallback；未打包已确认许可证的 fallback 字体。 |
| CG10-P2-19 | 接受 | 颜色仍是部分玩法的主要区分方式，需图案/形状辅助。 |
| CG10-P2-20 | 接受 | 高对比与降低动态设置尚未实现。 |
| CG10-P2-21 | 部分 | stress 使用固定 seed；Clock/RNG 尚未作为服务注入所有游戏。 |
| CG10-P2-22 | 部分 | 若干规则已有纯函数测试，五款 Engine 尚未系统抽离 SDL。 |
| CG10-P2-23 | 接受 | launcher 仍是单文件主循环，尚未按 state/render/data 拆分。 |
| CG10-P2-24 | 部分 | 首页已有 best/recent；progress/continue 摘要未加入。 |
| CG10-P2-25 | 接受 | 没有 profiler 证据前未盲目加入 Surface 缓存。 |
| CG10-P2-26 | 接受 | 2048 每次有效移动仍保存，未实现 debounce；退出会写 released。 |
| CG10-P2-27 | 接受 | Zuma reaction 尚未重构为显式 FSM。 |
| CG10-P2-28 | 部分 | stress 输出渲染/保存数据；尚未形成带 OS/版本参数的独立 benchmark CLI。 |
| CG10-P2-29 | 部分 | 有资源循环和 20,000 步压力检查；没有 30–60 分钟 soak。 |
| CG10-P2-30 | 接受 | 游戏内规则/版本页尚未实现。 |
| CG10-P2-31 | 接受 | 启动失败有 launcher 提示，游戏内崩溃恢复页和日志入口尚未实现。 |
| CG10-P2-32 | 接受 | 独立设置页尚未实现。 |

## P3

| ID | 状态 | 处理结果 |
| --- | --- | --- |
| CG10-P3-01 | 接受 | 7-bag 应作为新 comfort ruleset，当前未加入。 |
| CG10-P3-02 | 接受 | ghost/hold/lock delay 需要配套规则与测试，当前未加入。 |
| CG10-P3-03 | 接受 | Snake 速度、穿墙和障碍模式尚未加入。 |
| CG10-P3-04 | 接受 | 双人同屏尚未加入。 |
| CG10-P3-05 | 接受 | 2048 撤销与 attempt/slot 语义尚未设计完成。 |
| CG10-P3-06 | 接受 | 当前只有受 owner 保护的 autosave，多槽 UI 尚未加入。 |
| CG10-P3-07 | 接受 | 其他棋盘尺寸应分 ruleset，当前未加入。 |
| CG10-P3-08 | 部分 | Sokoban 可前往已解锁关和练习跳关；正式选关页未加入。 |
| CG10-P3-09 | 接受 | 星级和最佳推动指标尚未加入。 |
| CG10-P3-10 | 接受 | 死锁检测/提示尚未加入。 |
| CG10-P3-11 | 接受 | XSB 编辑器、验证和预览尚未加入。 |
| CG10-P3-12 | 部分 | Zuma 关卡进度已持久化；训练/选关 UI 未加入。 |
| CG10-P3-13 | 接受 | Zuma 球仍需颜色外符号辅助。 |
| CG10-P3-14 | 接受 | 原创道具与新轨道需确定规则和测试，当前未加入。 |
| CG10-P3-15 | 接受 | 轨道编辑器尚未加入。 |
| CG10-P3-16 | 接受 | 本机成就系统尚未加入。 |
| CG10-P3-17 | 接受 | 离线每日挑战尚未加入。 |
| CG10-P3-18 | 接受 | 本地 command-log replay 尚未加入。 |
| CG10-P3-19 | 接受 | 当前界面为中文，完整中英本地化尚未加入。 |
| CG10-P3-20 | 接受 | Windows installer/portable 仍需发行流水线。 |
| CG10-P3-21 | 接受 | macOS app bundle、数据目录和关闭 smoke 尚未交付。 |
| CG10-P3-22 | 接受 | Linux AppImage/等效包尚未交付。 |
| CG10-P3-23 | 接受 | 已有 release-gate，但 tagged build/release 尚未自动化。 |
| CG10-P3-24 | 接受 | 签名、校验和和密钥管理尚未加入。 |
| CG10-P3-25 | 接受 | README 截图/GIF 和项目主页尚未制作。 |
| CG10-P3-26 | 完成 | 本轮没有盲目新增游戏；新增内容继续要求 catalog、规则、数据、输入和测试同时交付。 |
