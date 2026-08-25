# 第十五次优化任务矩阵

状态：完成表示代码与测试闭环；部分表示只完成可证明子范围；待办表示建议合理但尚未实现；外部表示需要
仓库管理员、跨平台制品或权利人。产品功能没有因“独立”被判无效，但也不把路线图写成当前 Bug。

## P0：协议不变量

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG15-P0-01 | 完成 | transition gate 加 SH 下二次 transaction 扫描。 |
| CG15-P0-02 | 完成 | spawn 子进程真实等待者覆盖 handoff。 |
| CG15-P0-03 | 完成 | POSIX 用例进入 Linux/macOS 共同 storage suite。 |
| CG15-P0-04 | 完成 | `.preparing-*` 与 `.import-*` 分型。 |
| CG15-P0-05 | 完成 | missing journal 阻止启动并保留 rollback。 |
| CG15-P0-06 | 完成 | retryable 与 permanent journal error 分流。 |
| CG15-P0-07 | 完成 | same identity/different payload 不调用 Store。 |
| CG15-P0-08 | 完成 | planner/live outbox 共用 resolver。 |
| CG15-P0-09 | 完成 | preview 对 silent-drop conflict 回归。 |
| CG15-P0-10 | 完成 | duplicate/superseded/conflict/merge 契约一致。 |
| CG15-P0-11 | 完成 | handoff、missing journal、semantic conflict、planner fault matrix。 |
| CG15-P0-12 | 完成 | 新用例由现有 release 与三平台 matrix 执行。 |

## P1：恢复、兼容与发行基础

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG15-P1-01 | 完成 | 无 DB committed 证明时恢复 previous，不盲删 marker。 |
| CG15-P1-02 | 完成 | incoming/recover/ambiguous 三态分支。 |
| CG15-P1-03 | 完成 | reject false 发 SUPERSEDED 或 RECOVERY_REQUIRED。 |
| CG15-P1-04 | 完成 | recovery-required 停止当前 batch 并进入既有 backoff。 |
| CG15-P1-05 | 完成 | 陈旧完整 score temp 在 request lock 下提升。 |
| CG15-P1-06 | 完成 | state/clock temp 校验、resolver 合并或提升。 |
| CG15-P1-07 | 完成 | active inventory 与 replace 覆盖 temp/upgrade。 |
| CG15-P1-08 | 完成 | legacy pending lstat/no-follow/单链接。 |
| CG15-P1-09 | 完成 | 损坏 score target 隔离后可重发。 |
| CG15-P1-10 | 完成 | conflict/lock/corrupt StoreError 分流。 |
| CG15-P1-11 | 完成 | bootstrap 生成唯一 canonical DB identity。 |
| CG15-P1-12 | 完成 | DB symlink canonicalize，默认 pending 跟随真实 parent。 |
| CG15-P1-13 | 完成 | 文档明确 custom outbox 是测试/高级边界。 |
| CG15-P1-14 | 完成 | outbox/state 构造失败显式释放 lease。 |
| CG15-P1-15 | 部分 | 最终 root 与 DB identity 安全；任意祖先 mount/ACL 仍由 OS 权限控制。 |
| CG15-P1-16 | 完成 | Windows reparse root 策略沿用真机 CI。 |
| CG15-P1-17 | 完成 | control file `st_nlink != 1` 拒绝。 |
| CG15-P1-18 | 完成 | v3 由 archive version/format 固定分派。 |
| CG15-P1-19 | 部分 | reader 与 archive format 比较；独立 capability version 留给 v4。 |
| CG15-P1-20 | 完成 | 历史 catalog 子集有效。 |
| CG15-P1-21 | 完成 | export catalog 收录 removed game，导入 committed rows preserve-only。 |
| CG15-P1-22 | 部分 | current progress 严格 registry；未知历史 preserve-only，无虚构转换。 |
| CG15-P1-23 | 部分 | current slot 严格 registry；未知历史 preserve-only。 |
| CG15-P1-24 | 完成 | 文档固定 unknown historical preserve、不执行、不进当前榜。 |
| CG15-P1-25 | 完成 | fresh current-schema DB 可覆盖 corrupt target。 |
| CG15-P1-26 | 完成 | exclusive gate 内 `os.replace`，transaction rollback 可恢复。 |
| CG15-P1-27 | 完成 | CLI legacy v1 绑定 evidence 文件与 SHA-256。 |
| CG15-P1-28 | 部分 | transaction list/export/recover CLI 已有；state marker 独立 UI 待办。 |
| CG15-P1-29 | 完成 | claim ACK 绑定 revision/value hash。 |
| CG15-P1-30 | 完成 | 既有 pending/recovery/close 矩阵加 authoritative hash fixture。 |
| CG15-P1-31 | 完成 | 选择单档案单 autosave、单活动窗口并写入 README。 |
| CG15-P1-32 | 完成 | campaign board/session snapshot 与 practice 分离。 |
| CG15-P1-33 | 完成 | C 键与 overlay 返回原闯关。 |
| CG15-P1-34 | 完成 | moves、level、completed 或 total 任一累计状态均二次确认。 |
| CG15-P1-35 | 完成 | progress Future/message/status 按 key 分离。 |
| CG15-P1-36 | 完成 | practice schema 拒绝 unlocked_level。 |
| CG15-P1-37 | 完成 | practice overlay 主分数为练习成绩。 |
| CG15-P1-38 | 外部 | main branch protection 尚未设置。 |
| CG15-P1-39 | 部分 | release gate policy 在文档；本轮未获 tag/Release 授权。 |
| CG15-P1-40 | 待办 | 三平台独立 lock 尚未生成。 |
| CG15-P1-41 | 待办 | 完整 `--require-hashes` 闭包尚未生成。 |
| CG15-P1-42 | 完成 | CI pip 固定 26.2.1，build/setuptools 继续受 constraints/build-system 约束。 |
| CG15-P1-43 | 完成 | core/dev/compat matrix 全部使用 release constraints。 |
| CG15-P1-44 | 待办 | 静态类型工具与 baseline 尚未引入。 |
| CG15-P1-45 | 部分 | 新增 27 个 storage 方法；全项目阈值仍为 60%。 |
| CG15-P1-46 | 完成 | resolver、handoff、reject、raw rollback 模型用例。 |
| CG15-P1-47 | 完成 | export→fresh replace→pending/schema/rollback 进入 release suite。 |
| CG15-P1-48 | 部分 | 用户状态含 operation/reason；统一 structured logger 待办。 |
| CG15-P1-49 | 外部 | 权利核对需维护者与素材权利人。 |
| CG15-P1-50 | 外部 | LICENSE 需权利人选择。 |
| CG15-P1-51 | 完成 | NOTICE runtime asset gate 保留。 |
| CG15-P1-52 | 外部 | 名称/商标结论需权利人。 |
| CG15-P1-53 | 完成 | storage protocol 更新并冻结 v2/v3 边界。 |

## P2：维护性、桌面体验与可访问性

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG15-P2-01 | 部分 | resolver 边界抽出；完整 local_backend 拆分待办。 |
| CG15-P2-02 | 待办 | store repositories/migrations 拆分未完成。 |
| CG15-P2-03 | 部分 | fresh materializer 独立；archive/planner/executor 全拆待办。 |
| CG15-P2-04 | 待办 | DataManagementService 尚无 GUI API。 |
| CG15-P2-05 | 待办 | 数据管理页未实现。 |
| CG15-P2-06 | 待办 | transaction 恢复页未实现，CLI 可用。 |
| CG15-P2-07 | 部分 | launcher 有新建/切换；重命名与进度列表待办。 |
| CG15-P2-08 | 待办 | profile 删除和 export-before-delete 未实现。 |
| CG15-P2-09 | 待办 | profile merge UI 未实现。 |
| CG15-P2-10 | 部分 | SaveState 为 Enum；游戏 UI magic string 仍存在。 |
| CG15-P2-11 | 部分 | BaseGame 共用成绩控制；五款 controller 未完全抽取。 |
| CG15-P2-12 | 部分 | Sokoban per-key controller 收紧；统一类待办。 |
| CG15-P2-13 | 待办 | InputManager 未实现。 |
| CG15-P2-14 | 部分 | IME composition 可用；选择/光标编辑待办。 |
| CG15-P2-15 | 待办 | 键位重映射未实现。 |
| CG15-P2-16 | 待办 | 全键盘 focus 导航未实现。 |
| CG15-P2-17 | 待办 | 手柄未实现。 |
| CG15-P2-18 | 待办 | 音频系统未实现。 |
| CG15-P2-19 | 待办 | 设置页未实现。 |
| CG15-P2-20 | 待办 | scalable canvas 未实现。 |
| CG15-P2-21 | 待办 | 完整高 DPI 策略未实现。 |
| CG15-P2-22 | 部分 | 系统 CJK fallback 已有；随包字体需权利许可。 |
| CG15-P2-23 | 部分 | Zuma 标记与 Tetris 图形可辨；全项目审计待办。 |
| CG15-P2-24 | 待办 | 高对比/降低动态设置未实现。 |
| CG15-P2-25 | 部分 | Tetris、Snake、Zuma 可注入 RNG；统一 Clock 仍待办。 |
| CG15-P2-26 | 部分 | 多个纯 validator/solver 已独立；五款 engine 未全抽取。 |
| CG15-P2-27 | 待办 | launcher app/state/render 未拆分。 |
| CG15-P2-28 | 部分 | 首页已有档案/best/recent；continue dashboard 待办。 |
| CG15-P2-29 | 部分 | 字体/overlay 缓存已有；Surface profiler 优化待办。 |
| CG15-P2-30 | 完成 | stress benchmark 输出 seed/延迟/资源。 |
| CG15-P2-31 | 部分 | 自动 100 次资源循环；30–60 分钟人工 soak 待办。 |
| CG15-P2-32 | 完成 | taskbook 归档到 docs/audits，根目录旧副本删除。 |

## P3：玩法内容与三平台发行

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG15-P3-01 | 待办 | Tetris lock delay 未实现。 |
| CG15-P3-02 | 待办 | strict rotation 独立 ruleset 未实现。 |
| CG15-P3-03 | 部分 | hold 图形已实现；完整规则页待办。 |
| CG15-P3-04 | 待办 | Snake 速度/穿墙/障碍模式未实现。 |
| CG15-P3-05 | 待办 | Snake 双人同屏未实现。 |
| CG15-P3-06 | 待办 | 2048 撤销未实现。 |
| CG15-P3-07 | 待办 | 2048 多槽未实现；当前策略明确单槽。 |
| CG15-P3-08 | 待办 | 2048 棋盘尺寸模式未实现。 |
| CG15-P3-09 | 部分 | 快捷选关、practice/campaign 返回明确；列表 UI 待办。 |
| CG15-P3-10 | 部分 | best score/moves/pushes 已持久化；星级 UI 待办。 |
| CG15-P3-11 | 待办 | Sokoban 死锁提示未实现。 |
| CG15-P3-12 | 待办 | Sokoban 编辑器未实现。 |
| CG15-P3-13 | 待办 | Zuma reaction FSM 未独立抽取。 |
| CG15-P3-14 | 待办 | Zuma 训练/选关未实现。 |
| CG15-P3-15 | 部分 | 原创轨道已有；道具待办。 |
| CG15-P3-16 | 待办 | Zuma 轨道编辑器未实现。 |
| CG15-P3-17 | 待办 | 本机成就未实现。 |
| CG15-P3-18 | 待办 | 离线每日挑战未实现。 |
| CG15-P3-19 | 待办 | 本地 replay 未实现。 |
| CG15-P3-20 | 待办 | 完整中英文资源未实现。 |
| CG15-P3-21 | 待办 | Windows 无 Python 包未生成。 |
| CG15-P3-22 | 待办 | macOS app bundle 未生成。 |
| CG15-P3-23 | 待办 | Linux AppImage/等效包未生成。 |
| CG15-P3-24 | 待办 | 自动签名 Release 未实现。 |
