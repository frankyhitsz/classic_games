# 第十四次优化任务矩阵

状态含义：**完成**为代码和自动化验证已闭环；**部分**为完成了可证明子范围；**待办**为仍需实现；
**外部**为需要仓库管理、跨平台制品或权利人决定。大型功能不会因“不属于产品”被拒绝，但也不把设计
说明写成已实现。

## P0：恢复与 ownership

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG14-P0-01 | 完成 | application exclusive lease 原子 handoff 为 shared session。 |
| CG14-P0-02 | 完成 | 原子 handoff 消除了二次扫描仍可能存在的窗口。 |
| CG14-P0-03 | 完成 | 测试禁止 recovery 后调用 shared reacquire，并验证 session 阻断维护。 |
| CG14-P0-04 | 完成 | reject v3 marker 在 canonical replace 前进入 prepared。 |
| CG14-P0-05 | 完成 | valid temp 提升；partial/conflict temp 隔离。 |
| CG14-P0-06 | 完成 | marker 是跨重启后备，运行时失败另保留 non-durable previous。 |
| CG14-P0-07 | 完成 | 覆盖 replace 前崩溃、temp、重启 reject、损坏 temp/restore。 |
| CG14-P0-08 | 完成 | claim 需要权威 owner token/epoch/active 回执。 |
| CG14-P0-09 | 完成 | SUPERSEDED/RECOVERY_REQUIRED 等 SaveState 有明确转移。 |
| CG14-P0-10 | 完成 | CAS 与 superseded 回归证明 loser 不能进入 ready。 |
| CG14-P0-11 | 完成 | loser 在 reload/conflict 前保持输入门禁。 |
| CG14-P0-12 | 完成 | 新 storage 用例进入现有 release gate 和三平台矩阵。 |

## P1：兼容性、恢复与发行基础

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG14-P1-01 | 完成 | `upgrade-archive` 支持严格 v2→v3；format-less v2 保持 merge-only。 |
| CG14-P1-02 | 完成 | 输出 `replace_eligible` 与 `unproven`。 |
| CG14-P1-03 | 完成 | manifest ruleset 不再与当前目录全等。 |
| CG14-P1-04 | 部分 | 历史/未知 ruleset 行按自身版本保留并默认分榜；尚无需要转换 payload 的 adapter。 |
| CG14-P1-05 | 完成 | v3 manifest 写 package version。 |
| CG14-P1-06 | 完成 | v3 写 reader min/max 与 capability。 |
| CG14-P1-07 | 完成 | storage protocol 明确 v2 冻结与 v3 边界。 |
| CG14-P1-08 | 完成 | v1 默认不自动恢复。 |
| CG14-P1-09 | 完成 | 先 export evidence，再显式 `--allow-legacy-v1`。 |
| CG14-P1-10 | 完成 | verified-byte reader 同 descriptor 返回使用 bytes。 |
| CG14-P1-11 | 完成 | rollback DB 从 verified bytes 写入临时副本后 quick_check/连接。 |
| CG14-P1-12 | 不采用 | 未新增 journal v3 lineage；选择多 transaction 默认拒绝。 |
| CG14-P1-13 | 完成 | 多 transaction recovery-required。 |
| CG14-P1-14 | 完成 | WinAPI reparse-aware control-file open。 |
| CG14-P1-15 | 完成 | Windows CI junction 回归；权限不足时明确 skip。 |
| CG14-P1-16 | 完成 | pending/state root 拒绝 symlink/reparse 和不安全 POSIX 权限。 |
| CG14-P1-17 | 完成 | quarantine/migration root 复用安全目录检查。 |
| CG14-P1-18 | 完成 | migrated legacy score 进入 inventory。 |
| CG14-P1-19 | 完成 | cleanup/replace 识别并删除已有证明的 migrated evidence。 |
| CG14-P1-20 | 完成 | invalid/conflicting legacy restore 隔离。 |
| CG14-P1-21 | 部分 | 未替换 DB 文件；在事务内实现完整对象重建和 fingerprint，满足结果语义。 |
| CG14-P1-22 | 完成 | restore 后 schema 与空白当前库精确比较。 |
| CG14-P1-23 | 完成 | 操作前完整 DB backup 和 transaction rollback image 保留旧结构。 |
| CG14-P1-24 | 完成 | claim durable-pending 覆盖全部 SaveState。 |
| CG14-P1-25 | 完成 | close 发布 superseding release。 |
| CG14-P1-26 | 完成 | loser 重载后进入已有 conflict/takeover UI。 |
| CG14-P1-27 | 完成 | campaign/practice 内存和持久 ledger 分离。 |
| CG14-P1-28 | 完成 | practice 不改变 campaign unlock。 |
| CG14-P1-29 | 完成 | selector 仅 playing 生效。 |
| CG14-P1-30 | 完成 | level-1 practice 不重置 score session/ledger；有移动时二次确认。 |
| CG14-P1-31 | 完成 | 选择 practice 也受 campaign unlock 约束，N 与 selector 一致。 |
| CG14-P1-32 | 完成 | practice progress 在启动时读取，记录 best score/moves/pushes。 |
| CG14-P1-33 | 完成 | BaseGame 与 launcher 共用 recovery UI。 |
| CG14-P1-34 | 完成 | `init_db` 持有 recovered application lease。 |
| CG14-P1-35 | 完成 | `docs/storage-protocol.md` 记录正式写入边界。 |
| CG14-P1-36 | 完成 | package version 0.7.0。 |
| CG14-P1-37 | 完成 | archive application.version=0.7.0。 |
| CG14-P1-38 | 外部 | 尚未设置 GitHub branch protection。 |
| CG14-P1-39 | 部分 | 文档要求 release 来自通过 gate 的 commit；本轮未获授权创建 tag/Release。 |
| CG14-P1-40 | 待办 | 尚无 Linux/macOS/Windows 独立 lock。 |
| CG14-P1-41 | 待办 | 尚无完整 `--require-hashes` 安装闭包。 |
| CG14-P1-42 | 部分 | workflow action 与 constraints 固定；pip bootstrap 尚未 hash lock。 |
| CG14-P1-43 | 完成 | release manifest 与 CycloneDX SBOM 对照已在上一轮进入 gate。 |
| CG14-P1-44 | 待办 | 未引入 pyright/mypy；本轮继续 Ruff 与运行时语义测试。 |
| CG14-P1-45 | 部分 | 新增 27 项 storage；全项目门槛仍为 60%，未虚报 90%。 |
| CG14-P1-46 | 完成 | handoff/reject/claim 状态不变量有 fault/race 模型测试。 |
| CG14-P1-47 | 完成 | cleanup 最终重列目录、比较完整文件集合并逐文件 no-follow 删除。 |
| CG14-P1-48 | 完成 | hard-link 失败时使用 O_EXCL+fsync fallback，不覆盖现有文件。 |
| CG14-P1-49 | 待办 | 尚无统一 structured recovery logger。 |
| CG14-P1-50 | 完成 | 测试覆盖 export→upgrade/replace→schema/pending 验证和 rollback。 |
| CG14-P1-51 | 外部 | 权利核对需要仓库与素材权利人。 |
| CG14-P1-52 | 外部 | LICENSE 需权利人选择。 |
| CG14-P1-53 | 完成 | NOTICE runtime asset gate 已存在。 |
| CG14-P1-54 | 外部 | 名称/商标结论需权利人。 |
| CG14-P1-55 | 完成 | 存储协议文档冻结已发布格式，只接受安全/数据正确性修复。 |

## P2：维护性、桌面体验与可访问性

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG14-P2-01 | 待办 | store 拆分需独立无行为变化重构。 |
| CG14-P2-02 | 待办 | local_backend 拆分需独立重构。 |
| CG14-P2-03 | 部分 | archive/import 职责已有模块边界；data_cli 尚大。 |
| CG14-P2-04 | 待办 | 尚无 GUI DataManagementService。 |
| CG14-P2-05 | 待办 | 尚无数据管理页面。 |
| CG14-P2-06 | 待办 | transaction 目前为 CLI。 |
| CG14-P2-07 | 部分 | launcher 已支持档案选择/创建；重命名和完整进度页待做。 |
| CG14-P2-08 | 待办 | 删除档案与 export-before-delete 尚未实现。 |
| CG14-P2-09 | 待办 | profile merge 尚未实现。 |
| CG14-P2-10 | 部分 | SaveState 已为 Enum；各游戏 UI state 尚有字符串。 |
| CG14-P2-11 | 部分 | BaseGame 统一成绩保存；五款完整 controller 尚未抽取。 |
| CG14-P2-12 | 部分 | GameDataService/LocalStateEvent 已统一后端，UI controller 待抽取。 |
| CG14-P2-13 | 待办 | 尚无统一 InputManager。 |
| CG14-P2-14 | 部分 | launcher 支持 IME composition，选择/光标编辑仍有限。 |
| CG14-P2-15 | 待办 | 键位重映射尚未实现。 |
| CG14-P2-16 | 待办 | 菜单仍未全键盘 focus 导航。 |
| CG14-P2-17 | 待办 | 手柄未实现。 |
| CG14-P2-18 | 待办 | 音频系统未实现。 |
| CG14-P2-19 | 待办 | 设置页面未实现。 |
| CG14-P2-20 | 待办 | scalable canvas 未实现。 |
| CG14-P2-21 | 待办 | 高 DPI 策略未完整实现。 |
| CG14-P2-22 | 部分 | 已有 PingFang/Hiragino/微软雅黑等系统 fallback；随包授权字体待权利确认。 |
| CG14-P2-23 | 部分 | Zuma 球有形状标记；其余依赖颜色的 UI 仍需审计。 |
| CG14-P2-24 | 待办 | 高对比/降低动态设置未实现。 |
| CG14-P2-25 | 部分 | Tetris 可注入 RNG；五款统一 Clock/RNG 待做。 |
| CG14-P2-26 | 部分 | Sokoban solver/部分规则可脱离渲染；完整 engines 待抽取。 |
| CG14-P2-27 | 待办 | launcher 尚未按 app/state/render 拆分。 |
| CG14-P2-28 | 部分 | 首页已有 best/recent/profile；continue/progress dashboard 待做。 |
| CG14-P2-29 | 部分 | 字体和 overlay 数据已有缓存；Surface 缓存需 profiler 驱动。 |
| CG14-P2-30 | 完成 | `tests.stress`/benchmark 输出 seed、延迟和资源数据。 |
| CG14-P2-31 | 部分 | 自动资源循环为短时 100 次；30–60 分钟 soak 待发行机执行。 |
| CG14-P2-32 | 部分 | v3 compatibility contract 已实现；真正流式 archive 另需新格式。 |

## P3：玩法与桌面发行

| ID | 状态 | 结果 |
| --- | --- | --- |
| CG14-P3-01 | 待办 | Tetris lock delay 尚未实现。 |
| CG14-P3-02 | 待办 | strict rotation 独立 ruleset 尚未实现。 |
| CG14-P3-03 | 待办 | 规则页尚未实现。 |
| CG14-P3-04 | 待办 | Snake 模式设置尚未实现。 |
| CG14-P3-05 | 待办 | 本地双人尚未实现。 |
| CG14-P3-06 | 待办 | 2048 撤销尚未实现。 |
| CG14-P3-07 | 待办 | 2048 多槽 UI 尚未实现。 |
| CG14-P3-08 | 待办 | 2048 棋盘尺寸模式尚未实现。 |
| CG14-P3-09 | 部分 | 已解锁练习选关和状态隔离已实现；正式列表 UI 待做。 |
| CG14-P3-10 | 部分 | 已记录练习 best score/moves/pushes；星级 UI 待做。 |
| CG14-P3-11 | 待办 | 死锁提示尚未实现。 |
| CG14-P3-12 | 待办 | Sokoban 编辑器未实现。 |
| CG14-P3-13 | 待办 | Zuma reaction FSM 尚未独立抽取。 |
| CG14-P3-14 | 待办 | Zuma 训练/选关未实现。 |
| CG14-P3-15 | 完成 | Zuma 球内形状标记使颜色不是唯一信息。 |
| CG14-P3-16 | 部分 | 五条原创轨道已存在；道具待做。 |
| CG14-P3-17 | 待办 | Zuma 轨道编辑器未实现。 |
| CG14-P3-18 | 待办 | 本机成就未实现。 |
| CG14-P3-19 | 待办 | 离线日期挑战未实现。 |
| CG14-P3-20 | 待办 | 本地 replay 未实现。 |
| CG14-P3-21 | 待办 | 完整中英文资源未实现。 |
| CG14-P3-22 | 待办 | Windows 无 Python 包未生成。 |
| CG14-P3-23 | 待办 | macOS app/Linux AppImage 未生成。 |
| CG14-P3-24 | 待办 | 未创建签名自动 Release；现有 wheel/sdist smoke 保留。 |
