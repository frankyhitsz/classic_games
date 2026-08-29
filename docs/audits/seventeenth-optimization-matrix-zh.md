# 第十七次审查优化矩阵

状态只表示当前仓库证据，不把建议、设计或外部设置写成已实现。

## P0

| ID | 状态 | 结论/证据 |
| --- | --- | --- |
| CG17-P0-01 | 已完成 | aggregate membership 在 LWW 前处理。 |
| CG17-P0-02 | 已完成 | 同 component ID 异 hash 返回 conflict。 |
| CG17-P0-03 | 已完成 | Store 普通 set 查询 merge receipt。 |
| CG17-P0-04 | 已完成 | aggregate dominance 不依赖 ID 字符串前缀。 |
| CG17-P0-05 | 已完成 | component receipt 跟随 authoritative state；新 set baseline 才清旧 components。 |
| CG17-P0-06 | 已完成 | v1 upgrade 使用共享 resolver。 |
| CG17-P0-07 | 已完成 | v2/current parse 后均进入共享 live/import/orphan resolver。 |
| CG17-P0-08 | 已完成 | import file planner 调用相同 component resolver。 |
| CG17-P0-09 | 已完成 | orphan/component replay 定向回归。 |
| CG17-P0-10 | 部分完成 | 幂等、单调、hash conflict 和顺序定向测试已加；尚未引入 Hypothesis model suite。 |
| CG17-P0-11 | 已完成 | reject marker temp 带 UUID。 |
| CG17-P0-12 | 已完成 | digest lock、grace、dev/inode/size/mtime 复查。 |
| CG17-P0-13 | 已完成 | BUSY marker 原位保留。 |
| CG17-P0-14 | 已完成 | BUSY restore 原位保留。 |
| CG17-P0-15 | 已完成 | scanner 只在第二次持锁复验仍 invalid 后隔离。 |
| CG17-P0-16 | 已完成 | 私有 recovery 用稳定 error code/行为区分 BUSY/INVALID/ABSENT；未新增无调用方价值的公共枚举。 |
| CG17-P0-17 | 已完成 | writer/scanner 共享 digest lock，active temp barrier 有定向测试。 |
| CG17-P0-18 | 已完成 | writer/reader node/depth/string/byte 预算一致。 |
| CG17-P0-19 | 已完成 | publish 前 reader round-trip。 |
| CG17-P0-20 | 已完成 | 30,000 行 archive 在 node 超限时发布前安全失败。 |
| CG17-P0-21 | 已完成 | export 返回 nodes/depth/string/bytes 及 limits。 |
| CG17-P0-22 | 已完成 | terminal root 原子迁入 cleanup namespace。 |
| CG17-P0-23 | 已完成 | cleanup root 进入 status/evidence，不阻断 startup/export。 |
| CG17-P0-24 | 已完成 | rmtree 故障注入证明 active root 已消失。 |
| CG17-P0-25 | 已完成 | 2048 pre-move settled snapshot。 |
| CG17-P0-26 | 已完成 | close 保存 pre-move settled state 并清 queued directions。 |
| CG17-P0-27 | 已完成 | 左/右/上/下 merge 动画中关闭测试。 |
| CG17-P0-28 | 已完成 | slot validator/load 既有覆盖结合 v15 settled save 测试。 |
| CG17-P0-29 | 已完成 | `test_storage_v15.py` 自动进入 storage/release discovery。 |

## P1

| ID | 状态 | 结论/证据 |
| --- | --- | --- |
| CG17-P1-01 | 已完成 | score commit 后 cleanup best-effort。 |
| CG17-P1-02 | 已完成 | state commit 后 cleanup best-effort。 |
| CG17-P1-03 | 已完成 | committed result/event 可带 `cleanup_pending`。 |
| CG17-P1-04 | 已完成 | authoritative receipt 优先 status reconstruction。 |
| CG17-P1-05 | 已完成 | parser validity 不依赖当前时间。 |
| CG17-P1-06 | 部分完成 | creation 拒绝明显超前时间；冻结 schema 未增加 clock-adjust 元数据。 |
| CG17-P1-07 | 已完成 | v1/v2 migration 在不同时钟下确定。 |
| CG17-P1-08 | 已完成 | state event precedence reducer。 |
| CG17-P1-09 | 部分完成 | equal identity 多种终态/挂起顺序有定向测试，尚未引入属性测试库。 |
| CG17-P1-10 | 已完成 | clock 坏 current 先隔离再提升 temp。 |
| CG17-P1-11 | 已完成 | clock quarantine 返回 bool。 |
| CG17-P1-12 | 部分完成 | quarantine failure 与坏 bytes 已覆盖；只读/满盘由通用 storage fault suite 覆盖，Windows symlink 受平台限制。 |
| CG17-P1-13 | 已完成 | `BackendCloseResult` 返回 read/write/lease 状态。 |
| CG17-P1-14 | 已完成 | 后台 shutdown 线程持 lease 到最后 task。 |
| CG17-P1-15 | 已完成 | slow read + inactive maintenance barrier 测试。 |
| CG17-P1-16 | 已完成 | 256 striped locks。 |
| CG17-P1-17 | 已完成 | inactive-gated `cleanup-score-locks`。 |
| CG17-P1-18 | 已完成 | `status.score_locks` 报告 stripe/legacy/unsafe。 |
| CG17-P1-19 | 已完成 | inspect final symlink no-follow。 |
| CG17-P1-20 | 已完成 | `inspect-header` 命令。 |
| CG17-P1-21 | 已完成 | `verify-archive` 使用临时空 schema。 |
| CG17-P1-22 | 已完成 | `_semantic_row_check` 与 current/historical policy。 |
| CG17-P1-23 | 已完成 | 临时 schema foreign-key check。 |
| CG17-P1-24 | 已完成 | score/state classification-before-parse。 |
| CG17-P1-25 | 部分完成 | 当前策略要求显式兼容才可激活；尚无实际历史 score adapter。 |
| CG17-P1-26 | 部分完成 | 当前策略要求显式兼容才可激活；尚无实际历史 state adapter。 |
| CG17-P1-27 | 已完成 | import 结果按实际 historical file policy 与 state resolution 统计。 |
| CG17-P1-28 | 已完成 | 共享 transaction root classifier。 |
| CG17-P1-29 | 已完成 | valid terminal root 不影响 active report。 |
| CG17-P1-30 | 已完成 | Archive v4 completeness 六维模型。 |
| CG17-P1-31 | 已完成 | v1/v2/v3 reader 与 v3 replace 保持兼容。 |
| CG17-P1-32 | 已完成 | 大 recovery hash-only inventory。 |
| CG17-P1-33 | 已完成 | cleanup 流式 hash proof。 |
| CG17-P1-34 | 部分完成 | 已按 hash 防止重新嵌入和重复链；尚未把所有本机 evidence 迁成全局 CAS object store。 |
| CG17-P1-35 | 已完成 | archive hash、原 path、source retention class 保留来源。 |
| CG17-P1-36 | 已完成 | imported evidence 默认只记 hash，不嵌入内容。 |
| CG17-P1-37 | 已完成 | transaction shape/depth/node/string validator。 |
| CG17-P1-38 | 已完成 | NaN/Infinity 拒绝。 |
| CG17-P1-39 | 已完成 | open/read 完整 post-fstat。 |
| CG17-P1-40 | 已完成 | JSON/Unicode/Recursion/Memory 转 StoreError。 |
| CG17-P1-41 | 已完成 | journal encoded-size preflight。 |
| CG17-P1-42 | 已完成 | 10,000 operation 上限与错误码。 |
| CG17-P1-43 | 已完成 | 动态 SQLite 类型返回 table/row/column。 |
| CG17-P1-44 | 已完成 | export→decode→verify→preview 闭环。 |
| CG17-P1-45 | 已完成 | BaseGame `restore_attempt_identity`。 |
| CG17-P1-46 | 已完成 | outer ruleset 不匹配不激活。 |
| CG17-P1-47 | 已完成 | Sokoban bounds/ledger/identity/reachability validator。 |
| CG17-P1-48 | 已完成 | active checkpoint 保留到明确返回/正常关闭。 |
| CG17-P1-49 | 已完成 | sync receipt 或 async Future ACK 驱动 session 状态与提示。 |
| CG17-P1-50 | 已完成 | `durable_slot_intent` capability；Future 创建不算 durable。 |
| CG17-P1-51 | 已完成 | invalid current session 进入 slot quarantine；旧 ruleset 只 preserve。 |
| CG17-P1-52 | 已完成 | 2048 旧 slot 先按 outer ruleset 分类，再隔离保存并新开。 |
| CG17-P1-53 | 已完成 | 即使旧 state shape 已不受 current parser 支持，新开仍等待 quarantine ACK，避免旧 CAS 死锁。 |
| CG17-P1-54 | 已完成 | before-close structured exception 与 recovery notice。 |
| CG17-P1-55 | 已完成 | state recovery 的 orphan/marker/restore/count 共享 250 ms 总预算；普通 scanner 也有独立单轮预算。 |
| CG17-P1-56 | 已完成 | scandir 至多收集 10,000 个 marker，不再无界 glob materialize。 |
| CG17-P1-57 | 已完成 | preflight 返回 database/archive/rollback/staging/margin/free 明细。 |
| CG17-P1-58 | 已完成 | import 的长期 backup 复用 transaction 已认证 SQLite image，不再另做一次 SQLite backup。 |
| CG17-P1-59 | 已完成 | app factory 对非 loopback request 执行同一暴露策略。 |
| CG17-P1-60 | 已完成 | 远端调试自动生成或读取 bearer token。 |
| CG17-P1-61 | 外部未完成 | GitHub main required checks 需仓库设置权限。 |
| CG17-P1-62 | 待后续 | 尚未生成三平台 `--require-hashes` 锁。 |
| CG17-P1-63 | 待后续 | coverage 可生成，但尚未设 storage/archive 90% 与全仓 80% gate。 |
| CG17-P1-64 | 待后续 | 尚未引入 pyright/mypy CI。 |
| CG17-P1-65 | 外部未完成 | 权利来源需所有者确认。 |
| CG17-P1-66 | 外部未完成 | 未在权利确认前擅自选择 LICENSE。 |
| CG17-P1-67 | 已完成 | `storage-protocol.md` 记录 v3 compatibility 与 v4/slot v6/cleanup freeze 边界。 |

## P2

| ID | 状态 | 结论/证据 |
| --- | --- | --- |
| CG17-P2-01 | 待后续 | score outbox 尚在 `local_backend.py`。 |
| CG17-P2-02 | 待后续 | state outbox/resolver 尚未拆包。 |
| CG17-P2-03 | 待后续 | worker/status 尚未拆包，但 close contract 已收口。 |
| CG17-P2-04 | 待后续 | archive reader/writer 尚在 `data_cli.py`。 |
| CG17-P2-05 | 待后续 | planner/executor 尚未拆包。 |
| CG17-P2-06 | 部分完成 | manifest 有集中 validator，尚未 TypedDict/mypy gate。 |
| CG17-P2-07 | 部分完成 | operation/event 为明确 dict/dataclass，尚未完整 TypedDict。 |
| CG17-P2-08 | 待后续 | 尚无独立 DataManagementService。 |
| CG17-P2-09 | 部分完成 | CLI status 已有完整数据状态，尚无 pygame 管理页。 |
| CG17-P2-10 | 部分完成 | CLI verify 已完成，尚无 UI。 |
| CG17-P2-11 | 部分完成 | CLI preview 已完成，尚无 UI。 |
| CG17-P2-12 | 部分完成 | CLI transactions/export/recover 已完成，尚无 UI。 |
| CG17-P2-13 | 部分完成 | CLI cleanup plan/apply 已完成，尚无 UI。 |
| CG17-P2-14 | 部分完成 | launcher 支持列出、切换、新建和改显示名；尚无完整档案页。 |
| CG17-P2-15 | 待后续 | 尚无五游戏档案摘要 dashboard。 |
| CG17-P2-16 | 待后续 | 尚无档案删除流程。 |
| CG17-P2-17 | 待后续 | 尚无档案合并预览。 |
| CG17-P2-18 | 待后续 | 尚无统一 InputManager。 |
| CG17-P2-19 | 待后续 | 尚无键位重映射。 |
| CG17-P2-20 | 部分完成 | launcher 有键盘输入/快捷键，但卡片 focus 导航未完整。 |
| CG17-P2-21 | 待后续 | 尚无手柄支持。 |
| CG17-P2-22 | 已完成 | launcher 支持 pygame TEXTEDITING/TEXTINPUT composition。 |
| CG17-P2-23 | 待后续 | 尚无 AudioManager/BGM/SFX。 |
| CG17-P2-24 | 部分完成 | 无音频资源时游戏可运行；尚无 AudioManager 设备降级状态。 |
| CG17-P2-25 | 待后续 | 尚无统一设置页。 |
| CG17-P2-26 | 待后续 | 窗口仍以各游戏固定逻辑尺寸为主。 |
| CG17-P2-27 | 待后续 | 尚无显式高 DPI viewport。 |
| CG17-P2-28 | 已有基础 | UI font 有 CJK fallback/cache，仍需三平台字体打包验证。 |
| CG17-P2-29 | 外部未完成 | 素材/字体 license inventory 需权利核对。 |
| CG17-P2-30 | 待后续 | 尚无高对比模式。 |
| CG17-P2-31 | 待后续 | Snake/Zuma/2048 尚无统一色弱符号。 |
| CG17-P2-32 | 待后续 | 尚无全局降低动画设置。 |
| CG17-P2-33 | 部分完成 | UI 有 focus/尺寸适配基础，尚无大字号设置与统一 focus ring。 |
| CG17-P2-34 | 待后续 | wall/monotonic clock 尚未统一注入。 |
| CG17-P2-35 | 部分完成 | Tetris/Snake/2048/Zuma 可注入 RNG；Sokoban 无随机规则。 |
| CG17-P2-36 | 部分完成 | 2048 slot v6 已持久化 RNG；其余游戏无通用 replay slot。 |
| CG17-P2-37 | 部分完成 | 2048 规则方法可无渲染调用，但尚未拆成独立 engine package。 |
| CG17-P2-38 | 部分完成 | Tetris piece/rules 可测试，尚未脱离 pygame 模块。 |
| CG17-P2-39 | 部分完成 | Snake grid step 可测试，尚未拆 engine。 |
| CG17-P2-40 | 部分完成 | parse/board/history/solver primitives 已有，尚未独立模块。 |
| CG17-P2-41 | 部分完成 | Zuma staged reaction FSM 行为已有，尚未抽成纯逻辑 engine。 |
| CG17-P2-42 | 部分完成 | 2048 有 RNG state/move digest，尚无通用 replay 文件。 |
| CG17-P2-43 | 部分完成 | score/before-close 有 structured log，尚未统一所有 recovery event。 |
| CG17-P2-44 | 已有基础 | `tests.stress` 输出 OS/Python/seed 与耗时指标，尚未独立安装命令。 |
| CG17-P2-45 | 部分完成 | 自动 stress/100 cycle 已有，未执行 30–60 分钟 CI soak。 |
| CG17-P2-46 | 已完成 | README 树已补至 `test_storage_v15.py`。 |

## P3

| ID | 状态 | 结论/证据 |
| --- | --- | --- |
| CG17-P3-01 | 待后续 | Tetris 尚无 lock delay。 |
| CG17-P3-02 | 待后续 | 尚无 strict/assist preset 与分榜。 |
| CG17-P3-03 | 待后续 | 尚无 sprint/marathon 模式。 |
| CG17-P3-04 | 已完成 | hold、next 图形预览和 7-bag 均已实现。 |
| CG17-P3-05 | 部分完成 | Snake 有速度曲线；穿墙/障碍模式未实现。 |
| CG17-P3-06 | 待后续 | 尚无双人同屏。 |
| CG17-P3-07 | 待后续 | 2048 尚无 undo。 |
| CG17-P3-08 | 待后续 | 2048 仍是单 autosave slot。 |
| CG17-P3-09 | 待后续 | 仅 4×4。 |
| CG17-P3-10 | 部分完成 | 已解锁关卡支持键盘选关/练习，尚无独立图形选关页。 |
| CG17-P3-11 | 部分完成 | practice 保存 best moves/pushes，尚无星级 UI。 |
| CG17-P3-12 | 待后续 | 尚无可关闭死锁提示。 |
| CG17-P3-13 | 待后续 | 尚无 XSB/编辑器。 |
| CG17-P3-14 | 部分完成 | Zuma 有五关与 next flow，尚无训练/任意关选择页。 |
| CG17-P3-15 | 待后续 | 尚无色弱球符号。 |
| CG17-P3-16 | 待后续 | 尚无轨道编辑器。 |
| CG17-P3-17 | 待后续 | 尚无道具系统。 |
| CG17-P3-18 | 待后续 | 尚无本机成就。 |
| CG17-P3-19 | 待后续 | 尚无离线每日 seed 挑战。 |
| CG17-P3-20 | 待后续 | 尚无复盘浏览器。 |
| CG17-P3-21 | 部分完成 | 当前中文界面稳定，尚无中英文切换。 |
| CG17-P3-22 | 待后续 | 尚无 Windows portable/installer。 |
| CG17-P3-23 | 待后续 | 尚无签名 macOS app bundle。 |
| CG17-P3-24 | 待后续 | 尚无 Linux AppImage/包。 |
| CG17-P3-25 | 部分完成 | release profile/build/smoke 已自动化；尚未发布带签名和校验和的 Release。 |
