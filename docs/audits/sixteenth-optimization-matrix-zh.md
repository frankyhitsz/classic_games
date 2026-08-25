# 第十六次优化任务矩阵

状态含义：完成表示代码或文档交付物已验证；部分表示合理子范围已完成但长期目标未全部落地；待办表示路线
合理但不属于本轮存储封板的安全闭环；外部表示需要仓库管理员、跨平台制品环境或权利人。

## P0

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG16-P0-01 | 完成 | score 改为 single-link replace 发布。 |
| CG16-P0-02 | 完成 | publish/scan/remove/quarantine/retry 统一 request lock。 |
| CG16-P0-03 | 完成 | 不再存在 link→unlink 调度窗口，回归断言 `os.link` 不可达。 |
| CG16-P0-04 | 完成 | writer/scanner/orphan/lock fault 用例覆盖不误隔离。 |
| CG16-P0-05 | 完成 | durable 返回前二次读取 canonical 并核对 hash。 |
| CG16-P0-06 | 完成 | resolver 按 set/merge method 分派。 |
| CG16-P0-07 | 完成 | set/set LWW 回归。 |
| CG16-P0-08 | 完成 | method matrix 与 baseline component 记入协议。 |
| CG16-P0-09 | 部分 | 五款 runtime 已优先使用 merge；兼容的公开 set API 保留。 |
| CG16-P0-10 | 完成 | legacy set 仍可解析并按 LWW 安全参与 resolver。 |
| CG16-P0-11 | 完成 | 新增 publication、method、orphan fault matrix。 |
| CG16-P0-12 | 完成 | storage v14 纳入现有 release/三平台 storage gate。 |

## P1

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG16-P1-01 | 完成 | lock timeout 保留 temp。 |
| CG16-P1-02 | 完成 | lock 前后稳定 fingerprint。 |
| CG16-P1-03 | 完成 | score 坏 canonical 先隔离。 |
| CG16-P1-04 | 完成 | state 坏 canonical 先隔离。 |
| CG16-P1-05 | 完成 | quarantine 失败禁止覆盖。 |
| CG16-P1-06 | 完成 | clock 64-byte no-follow reader。 |
| CG16-P1-07 | 完成 | 超过 24 小时的 future timestamp 拒绝。 |
| CG16-P1-08 | 完成 | event 加 operation ID/hash。 |
| CG16-P1-09 | 完成 | status cache 使用完整 order。 |
| CG16-P1-10 | 完成 | terminal root 不再算 active。 |
| CG16-P1-11 | 完成 | regular/reparse/junction root 拒绝。 |
| CG16-P1-12 | 完成 | 采用“不自动 raw rollback sidecar”的安全 policy。 |
| CG16-P1-13 | 完成 | sidecar 存在进入 manual recovery。 |
| CG16-P1-14 | 部分 | fresh staging 进入统一 inventory；尚未改 transaction journal 格式。 |
| CG16-P1-15 | 完成 | status/export/cleanup 可盘点 fresh staging。 |
| CG16-P1-16 | 完成 | import/replace staging 前空间 preflight。 |
| CG16-P1-17 | 部分 | 保留各副本独立生命周期；v4 才考虑复用 authenticated image。 |
| CG16-P1-18 | 完成 | 四类 reserved prefix 统一保护。 |
| CG16-P1-19 | 完成 | export 默认 snapshot-only。 |
| CG16-P1-20 | 完成 | export 返回 repair flag/recovered roots。 |
| CG16-P1-21 | 完成 | 新增 `inspect-archive`。 |
| CG16-P1-22 | 完成 | preview help 明示恢复副作用。 |
| CG16-P1-23 | 完成 | pending score 使用 bounded reader。 |
| CG16-P1-24 | 完成 | archive descriptor/post-fstat reader。 |
| CG16-P1-25 | 完成 | historical score pending evidence-only。 |
| CG16-P1-26 | 完成 | historical state pending evidence-only。 |
| CG16-P1-27 | 完成 | removed-game pending 不阻断其他恢复。 |
| CG16-P1-28 | 完成 | v3 单值限制写入 storage protocol。 |
| CG16-P1-29 | 完成 | v4 observed set 来源分类为 ADR-only 设计。 |
| CG16-P1-30 | 完成 | Store/backend canonical bootstrap。 |
| CG16-P1-31 | 完成 | Flask 使用 canonical Store path。 |
| CG16-P1-32 | 完成 | prebuilt Store mismatch 拒绝。 |
| CG16-P1-33 | 完成 | worker/session 构造失败统一清理。 |
| CG16-P1-34 | 完成 | slot intent 返回 resolution/winner。 |
| CG16-P1-35 | 完成 | 2048 superseded release 不误报 pending。 |
| CG16-P1-36 | 完成 | Sokoban per-key single-flight + newest queue。 |
| CG16-P1-37 | 完成 | campaign 棋盘通过 durable slot 恢复。 |
| CG16-P1-38 | 已有 | control file 早已拒绝 `nlink != 1`。 |
| CG16-P1-39 | 完成 | 非 loopback API 需 `GAMES_UNSAFE_EXPOSE=1`。 |
| CG16-P1-40 | 外部 | main branch protection 需 GitHub 管理员设置。 |
| CG16-P1-41 | 待办 | 三平台独立 dependency lock 尚未生成。 |
| CG16-P1-42 | 待办 | 完整 `--require-hashes` 安装尚未建立。 |
| CG16-P1-43 | 完成 | setuptools 83.0.0 在 build-system/constraints 精确固定。 |
| CG16-P1-44 | 完成 | Python 声明收窄为已测 3.11–3.13。 |
| CG16-P1-45 | 部分 | 新增 25 项定向测试；全项目 coverage 门槛暂未升到 90%。 |
| CG16-P1-46 | 待办 | pyright/mypy 与动态 JSON baseline 尚未引入。 |
| CG16-P1-47 | 完成 | set/merge/LWW/conflict 模型用例进入 storage suite。 |
| CG16-P1-48 | 部分 | lock/quarantine/replace 故障注入扩充；尚无统一 registry API。 |
| CG16-P1-49 | 完成 | 既有 release suite 覆盖 export→replace→replay。 |
| CG16-P1-50 | 部分 | event/transaction identity 可追踪；统一 structured logger 待办。 |
| CG16-P1-51 | 外部 | 权利核对需维护者和素材权利人。 |
| CG16-P1-52 | 外部 | LICENSE 需权利人选择。 |
| CG16-P1-53 | 完成 | storage protocol 记录冻结面与 v4 边界。 |

## P2

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG16-P2-01 | 部分 | resolver/lifecycle 边界收紧，完整拆分待办。 |
| CG16-P2-02 | 待办 | store repositories/migrations 拆分。 |
| CG16-P2-03 | 部分 | archive inspect/preflight 可独测，完整拆分待办。 |
| CG16-P2-04 | 待办 | DataManagementService。 |
| CG16-P2-05 | 待办 | 数据管理页。 |
| CG16-P2-06 | 待办 | transaction GUI；CLI 已可用。 |
| CG16-P2-07 | 部分 | launcher 已有新建/切换，完整档案页待办。 |
| CG16-P2-08 | 待办 | export-before-delete。 |
| CG16-P2-09 | 待办 | profile merge UI。 |
| CG16-P2-10 | 部分 | SaveState 已是 Enum，游戏状态仍有字符串。 |
| CG16-P2-11 | 部分 | BaseGame 已统一部分成绩保存。 |
| CG16-P2-12 | 部分 | slot/progress 边界收紧，统一 controller 待办。 |
| CG16-P2-13 | 待办 | InputManager。 |
| CG16-P2-14 | 部分 | 现有 IME 可输入，完整编辑控件待办。 |
| CG16-P2-15 | 待办 | 键位重映射。 |
| CG16-P2-16 | 待办 | 全键盘 focus model。 |
| CG16-P2-17 | 待办 | 手柄支持。 |
| CG16-P2-18 | 待办 | 音频系统。 |
| CG16-P2-19 | 待办 | 设置页。 |
| CG16-P2-20 | 待办 | scalable canvas。 |
| CG16-P2-21 | 待办 | 完整高 DPI 策略。 |
| CG16-P2-22 | 部分 | 系统 CJK fallback 已有，随包字体需许可。 |
| CG16-P2-23 | 部分 | Zuma/Tetris 有形状线索，全游戏审计待办。 |
| CG16-P2-24 | 待办 | 高对比/降低动态设置。 |
| CG16-P2-25 | 部分 | 2048、Snake、Tetris、Zuma RNG 可注入；统一 Clock 待办。 |
| CG16-P2-26 | 部分 | 多个纯规则模块可无 SDL 测试，五款未全抽取。 |
| CG16-P2-27 | 待办 | launcher app/state/render 拆分。 |
| CG16-P2-28 | 部分 | 首页已有 best/recent/profile，continue dashboard 待办。 |
| CG16-P2-29 | 部分 | 字体/overlay 有缓存，Surface 策略待 profiler 驱动。 |
| CG16-P2-30 | 完成 | benchmark/stress 输出 seed、延迟和资源。 |
| CG16-P2-31 | 部分 | 自动资源循环已有，30–60 分钟人工 soak 待办。 |
| CG16-P2-32 | 部分 | fresh/transaction/evidence 分类扩充，统一 model 待办。 |

## P3

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG16-P3-01 | 待办 | Tetris optional lock delay。 |
| CG16-P3-02 | 待办 | strict rotation 独立 ruleset。 |
| CG16-P3-03 | 部分 | hold 图形已有，完整规则页待办。 |
| CG16-P3-04 | 待办 | Snake 速度/穿墙/障碍模式。 |
| CG16-P3-05 | 待办 | Snake 双人同屏。 |
| CG16-P3-06 | 待办 | 2048 undo。 |
| CG16-P3-07 | 待办 | 2048 多存档槽。 |
| CG16-P3-08 | 待办 | 2048 棋盘尺寸模式。 |
| CG16-P3-09 | 部分 | practice/campaign 入口明确，列表 UI 待办。 |
| CG16-P3-10 | 部分 | best metrics 已持久化，星级 UI 待办。 |
| CG16-P3-11 | 待办 | Sokoban 死锁检测。 |
| CG16-P3-12 | 待办 | Sokoban 编辑器。 |
| CG16-P3-13 | 待办 | Zuma reaction FSM 抽取。 |
| CG16-P3-14 | 待办 | Zuma 训练/选关。 |
| CG16-P3-15 | 部分 | 当前球有颜色/高光，专用 symbols 待办。 |
| CG16-P3-16 | 部分 | 原创轨道已有，道具扩展待办。 |
| CG16-P3-17 | 待办 | Zuma 轨道编辑器。 |
| CG16-P3-18 | 待办 | 本机成就。 |
| CG16-P3-19 | 待办 | 离线每日挑战。 |
| CG16-P3-20 | 待办 | 本地 replay。 |
| CG16-P3-21 | 待办 | 完整中英文资源。 |
| CG16-P3-22 | 待办 | Windows 无 Python 桌面包。 |
| CG16-P3-23 | 待办 | macOS app bundle/Linux AppImage。 |
| CG16-P3-24 | 待办 | 自动签名发布。 |
