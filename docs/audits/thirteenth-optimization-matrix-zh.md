# 第十三次审查任务矩阵

状态只反映当前仓库：完成表示已有实现和验证；部分表示交付物只覆盖了其中一段；未完成表示建议
合理但本轮没有足够实现证据；需所有者表示必须由仓库权限或权利人决定。

## P0（16 项）

| ID | 状态 | 证据 |
| --- | --- | --- |
| CG13-P0-01 | 完成 | 普通 LocalBackend/Flask 均在 DB 初始化前进入 recovery gate。 |
| CG13-P0-02 | 完成 | 可验证事务自动 rollback；corrupt txn 阻断并进入 launcher 恢复页。 |
| CG13-P0-03 | 完成 | DB、sidecar、两类 lock、legacy pending 和 import roots 纳入 reserved policy。 |
| CG13-P0-04 | 完成 | 无 force 使用 hard-link no-clobber；竞争创建测试保留 winner。 |
| CG13-P0-05 | 完成 | FileOperation 只允许 pending、pending-state、legacy 删除和 imported evidence。 |
| CG13-P0-06 | 完成 | 词法 containment、逐级 lstat/no-symlink、最终 resolve 三层校验。 |
| CG13-P0-07 | 完成 | recovery/pending/transaction evidence 全部 no-follow。 |
| CG13-P0-08 | 完成 | exporter 使用 POSIX relative path；Windows 形式继续拒绝。 |
| CG13-P0-09 | 完成 | inventory 识别 reject txn/tmp、restore、legacy pending。 |
| CG13-P0-10 | 完成 | marker 先恢复；残留 active artifact 使 complete export 失败。 |
| CG13-P0-11 | 完成 | replace 删除完整 active namespace 中 archive 未包含的项目。 |
| CG13-P0-12 | 完成 | reject marker 以唯一 tmp+replace+directory fsync 发布。 |
| CG13-P0-13 | 完成 | marker v2 hash；损坏/partial marker 隔离并显示 notice。 |
| CG13-P0-14 | 完成 | 2048 `claiming` 在 CAS ACK/COMMITTED 前屏蔽输入。 |
| CG13-P0-15 | 完成 | `test_storage_v11.py` 覆盖 crash、symlink、no-clobber、POSIX path 和 active inventory。 |
| CG13-P0-16 | 完成 | storage discovery 属于 release profile 首要 gate，失败即停止后续发布检查。 |

## P1（52 项）

| ID | 状态 | 证据 |
| --- | --- | --- |
| CG13-P1-01 | 完成 | staged size/sha256 写入 txn v2，publish 前复核。 |
| CG13-P1-02 | 完成 | before image size/sha256 在 rollback 前复核。 |
| CG13-P1-03 | 完成 | rollback DB 同时校验 size/hash 与 SQLite `quick_check`。 |
| CG13-P1-04 | 完成 | exact duplicate target 去重，conflicting bytes 拒绝。 |
| CG13-P1-05 | 完成 | evidence 同 path 同内容去重，不同内容/omission 冲突拒绝。 |
| CG13-P1-06 | 完成 | manifest application ID 必须为 `classic-games-hub`。 |
| CG13-P1-07 | 完成 | tables/pending/recovery count 与实际 array/row 数严格一致。 |
| CG13-P1-08 | 完成 | component complete 与 top-level complete 做双向一致性校验。 |
| CG13-P1-09 | 完成 | 当前 manifest 固定 catalog ruleset map；旧 v2 仅允许 merge。 |
| CG13-P1-10 | 完成 | schema version 和 tables 来自同一 read transaction。 |
| CG13-P1-11 | 完成 | format 2 evidence 强制 path/size/hash/base64 精确字段。 |
| CG13-P1-12 | 完成 | CLI 可 list、no-follow export、显式 retry rollback。 |
| CG13-P1-13 | 完成 | launcher 恢复页配合 CLI，无需手工删除目录。 |
| CG13-P1-14 | 完成 | production Flask 持 lifetime lease；维护阻断测试通过。 |
| CG13-P1-15 | 完成 | launcher maintenance/recovery 错误显示 retry/exit，不冒 traceback。 |
| CG13-P1-16 | 完成 | lock opener 使用 O_NOFOLLOW/fstat，并验证 owner/权限。 |
| CG13-P1-17 | 完成 | identical order 不同 payload 稳定 `state_operation_conflict` 409。 |
| CG13-P1-18 | 完成 | `publish_slot_intent` 先同步写 release journal，再排队 DB replay。 |
| CG13-P1-19 | 完成 | 阻塞旧 active write 时 release 仍胜出的确定性测试。 |
| CG13-P1-20 | 完成（替代实现） | 未替换 DB 文件；在可回滚事务中 drop unknown tables，验收结果相同且避免 WAL/file-handle 风险。 |
| CG13-P1-21 | 完成 | replace 前 SQLite backup 保留完整 legacy tables；status 可盘点并可随 recovery evidence 导出。 |
| CG13-P1-22 | 完成 | `docs/storage-protocol.md` 区分 active replace 与 evidence preserve。 |
| CG13-P1-23 | 完成 | status 的目录统计不跟随 symlink。 |
| CG13-P1-24 | 完成 | symlink、hardlink、FIFO/socket/device 均不读取，只报告 omission。 |
| CG13-P1-25 | 完成 | archive/transaction evidence 在最终 publish 前再次检查 reserved target。 |
| CG13-P1-26 | 完成 | before target fingerprint 在 publish 前复核；消失、出现或改变均拒绝。 |
| CG13-P1-27 | 完成 | export→replace、pending replay、unknown table/active marker 清理均有演练。 |
| CG13-P1-28 | 完成 | status 输出 recovery source、kind、size、modified_at；transactions 输出 phase/version/count。 |
| CG13-P1-29 | 完成 | cleanup 默认 preview；apply 需要完整当前 archive 且每个候选文件 hash 相同，输出 removed 审计结果。 |
| CG13-P1-30 | 完成 | storage protocol 文档说明 merge 拒绝冲突、replace 清空 active data。 |
| CG13-P1-31 | 完成 | 文档冻结 v2；v3 只在流式 hash/index 需求成立后设计。 |
| CG13-P1-32 | 完成 | 5,000 rows archive 基准记录 OS/Python/bytes/time/peak，见 `docs/benchmarks.md`。 |
| CG13-P1-33 | 完成 | 文档明确正式运行走 GameDataService/LocalBackend，store 是底层实现。 |
| CG13-P1-34 | 完成 | LocalBackend、Flask、data CLI 的 lease/maintenance 阻断路径都有测试。 |
| CG13-P1-35 | 未完成（仓库设置） | workflow 已覆盖 gate；当前无 `gh`，不能确认或修改 main required checks。 |
| CG13-P1-36 | 部分 | release governance 要求通过 release profile 后打 tag；GitHub 侧仍未强制。 |
| CG13-P1-37 | 未完成 | 尚无 Linux/macOS/Windows 分开的完整 lock 文件。 |
| CG13-P1-38 | 未完成 | 精确 constraints 已有，但没有三平台 wheel hash，不能安全启用 `--require-hashes`。 |
| CG13-P1-39 | 完成 | constraints 固定 pip/build/pyproject-hooks 及完整 release closure。 |
| CG13-P1-40 | 完成 | `tests.release_manifest` 对照安装清单与 CycloneDX 组件/version。 |
| CG13-P1-41 | 未完成 | Ruff/compile 已门禁，尚未建立 pyright/mypy 可通过的边界基线。 |
| CG13-P1-42 | 部分 | fsync/replace/lock/backup 故障注入用例齐全，尚未抽成统一 registry。 |
| CG13-P1-43 | 部分 | phase/hash/target/rollback 不变量有确定性用例，尚非生成式 model test。 |
| CG13-P1-44 | 部分 | CAS/merge/reject/identical-order 有回归，尚未建立完整状态机 model suite。 |
| CG13-P1-45 | 部分 | CI 合并 storage/stress/gameplay coverage，当前总门槛 60%，未达到建议路线的 80/90%。 |
| CG13-P1-46 | 部分 | transaction root/operation ID 可追踪，尚无统一 structured logging schema。 |
| CG13-P1-47 | 完成 | status、transactions、recovery notice 均为仅本机 JSON/界面信息，无上传。 |
| CG13-P1-48 | 需所有者 | 权利清单已写，但作者/雇佣/贡献授权复选项仍需权利人确认。 |
| CG13-P1-49 | 需所有者 | 根目录 LICENSE 仍未选择。 |
| CG13-P1-50 | 完成 | release manifest gate 扫描 runtime 图片/音频/字体；未在 NOTICE 登记则失败。 |
| CG13-P1-51 | 需所有者 | 名称、商标、图标和商店文案检查表已列，结论需发行者确认。 |
| CG13-P1-52 | 完成 | storage protocol 冻结 SQLite/spool/state/import/archive 五类版本面。 |

## P2（30 项）

| ID | 状态 | 证据或剩余工作 |
| --- | --- | --- |
| CG13-P2-01 | 未完成 | `store.py` 尚未拆成 repositories/migrations；建议合理，不能在无等价覆盖证明时机械切文件。 |
| CG13-P2-02 | 未完成 | `local_backend.py` 尚未拆成 worker/spool/status 模块。 |
| CG13-P2-03 | 部分 | archive/planner/transaction 已有独立函数和测试，文件本身仍较大。 |
| CG13-P2-04 | 部分 | CLI 已提供 status/export/import/recovery service；尚无供桌面页调用的完整 typed management API。 |
| CG13-P2-05 | 未完成 | 没有桌面数据管理页面。 |
| CG13-P2-06 | 部分 | launcher 可列出、新建、切换并改显示名；没有独立档案详情/进度页。 |
| CG13-P2-07 | 未完成 | 当前没有档案删除入口；未来删除仍需接入强制 export flow。 |
| CG13-P2-08 | 未完成 | 没有 profile merge 冲突预览工具。 |
| CG13-P2-09 | 未完成 | 各游戏仍使用字符串状态，没有统一 Enum。 |
| CG13-P2-10 | 未完成 | 五款 attempt/save 控制尚未抽成一个 controller。 |
| CG13-P2-11 | 部分 | backend 统一 state event/outbox；各游戏仍自行管理 progress/slot UI 状态。 |
| CG13-P2-12 | 部分 | BaseGame 统一全局键和 overlay guard；玩法 action map 尚未统一。 |
| CG13-P2-13 | 部分 | launcher 支持 IME composition 和长度限制，缺少光标移动与选择。 |
| CG13-P2-14 | 未完成 | 没有键位重映射与冲突检测。 |
| CG13-P2-15 | 部分 | 游戏结果页和主要动作有快捷键；launcher 卡片没有完整 focus navigation。 |
| CG13-P2-16 | 未完成 | 没有手柄映射。 |
| CG13-P2-17 | 未完成 | 当前没有音频系统，因此也没有音量/无设备降级验收。 |
| CG13-P2-18 | 未完成 | 没有统一设置页。 |
| CG13-P2-19 | 部分 | Sokoban 按地图调整窗口，其余仍为固定 canvas；没有全局逻辑分辨率。 |
| CG13-P2-20 | 部分 | 图形为矢量绘制且 Zuma 3× supersampling；未实现明确 DPI scale policy。 |
| CG13-P2-21 | 部分 | 有 PingFang/Hiragino/微软雅黑/system fallback；缺系统 CJK 字体时没有随包 licensed font。 |
| CG13-P2-22 | 部分 | Zuma 五色有不同形状标记；其余依赖颜色的界面尚未全盘复核。 |
| CG13-P2-23 | 未完成 | 没有全局高对比和降低动态设置。 |
| CG13-P2-24 | 部分 | Tetris 新增 RNG 注入和 seed 测试；其余游戏仍直接使用 random/time。 |
| CG13-P2-25 | 部分 | Tetris line/rotation、Sokoban parser/solver、Zuma path 等可脱离 loop 测试，未抽成五个完整 engine。 |
| CG13-P2-26 | 未完成 | launcher `main` 仍同时承担 state/render/input。 |
| CG13-P2-27 | 部分 | 首页已有 best leaderboard、recent、档案切换；没有继续存档和关卡进度 dashboard。 |
| CG13-P2-28 | 部分 | font 与 Zuma track 已缓存；没有 profiler 驱动的全局静态 Surface 缓存策略。 |
| CG13-P2-29 | 部分 | `tests.stress` 固定 seed 并输出性能，archive 基准记录完整环境；尚无统一 benchmark 子命令。 |
| CG13-P2-30 | 部分 | stress 覆盖 FD/线程/资源循环，但默认是短门禁，不是 30–60 分钟 soak。 |

## P3（24 项）

| ID | 状态 | 证据或剩余工作 |
| --- | --- | --- |
| CG13-P3-01 | 完成 | Tetris 使用随机打乱七块且每 bag 恰含全套；RNG 可注入。 |
| CG13-P3-02 | 部分 | 已有 ghost 与每块一次 hold；lock delay 尚未加入。 |
| CG13-P3-03 | 未完成 | Snake 尚无可选速度/穿墙/障碍 mode 与独立榜。 |
| CG13-P3-04 | 未完成 | 没有双人同屏 Snake。 |
| CG13-P3-05 | 未完成 | 2048 没有 attempt/slot 一致的撤销模型。 |
| CG13-P3-06 | 未完成 | 2048 仍是一个 CAS-protected autosave slot。 |
| CG13-P3-07 | 未完成 | 2048 仍固定 4×4。 |
| CG13-P3-08 | 完成 | Sokoban 可通过 `[/]`、PageUp/PageDown、1–9 选择已解锁关，选关强制 practice。 |
| CG13-P3-09 | 部分 | 每关 moves/pushes 和 level score 已显示/提交；没有持久化 best-push 星级表。 |
| CG13-P3-10 | 未完成 | 没有运行时死锁提示/求解提示。 |
| CG13-P3-11 | 未完成 | 没有 XSB 编辑器与 import/export。 |
| CG13-P3-12 | 部分 | 多 pending reaction、collapse delay 和递归 match 已测试；尚未改成显式 FSM/model property suite。 |
| CG13-P3-13 | 未完成 | Zuma 尚无不计 campaign 的训练/选关入口。 |
| CG13-P3-14 | 完成 | 五种球在颜色外绘制点、横线、竖线、叉、环。 |
| CG13-P3-15 | 未完成 | 没有新增原创道具/轨道内容。 |
| CG13-P3-16 | 未完成 | 没有轨道编辑器。 |
| CG13-P3-17 | 未完成 | 没有独立本机成就模型。 |
| CG13-P3-18 | 未完成 | 没有日期 seed 的离线每日挑战。 |
| CG13-P3-19 | 未完成 | 没有持久 command log/replay。 |
| CG13-P3-20 | 未完成 | 界面仍以中文为主，没有完整中英文资源与长文本布局测试。 |
| CG13-P3-21 | 未完成 | wheel/sdist smoke 已有，但不是无需 Python 的 Windows installer/portable。 |
| CG13-P3-22 | 未完成 | 没有签名的 macOS app bundle。 |
| CG13-P3-23 | 未完成 | 没有 AppImage/等效 Linux 原生包；XDG 数据路径已正确。 |
| CG13-P3-24 | 部分 | CI 有 release gate、wheel/sdist smoke、SBOM；自动 tag/release、签名和制品 checksum 尚未授权配置。 |
