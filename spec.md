# 第六次审查修复规格

## 目标

逐项核对第六次审查任务书，先关闭本地成绩丢失和坏旧数据阻断启动的风险，再完善
保存状态回写、迁移约束、本机档案和工程门禁。默认桌面路径继续保持离线、本机优先；
Flask 只作为显式开启的调试适配器。

## 范围

- SQLite 错误优先按 error code 分类；磁盘满、只读、I/O、锁、结构和损坏库均不得被
  当作请求语义错误而删除最后一份 mutation；
- 旧 `pending_saves.json` 和逐 request spool 使用严格 JSON、大小、深度、节点数和
  有限数限制；单个坏文件、隔离失败或无法生成预览不阻断其他记录和游戏启动；
- 每个 request 保存 `durable_pending`、`committed`、`recovery_required`、
  `quarantined` 或 `permanent_failure` 状态，游戏界面可在后台重试后更新结果；
- 手动重试、首次 pending 扫描、quarantine 计数和启动器的数据库初始化不在 pygame
  线程执行；重试和重开使用退避；
- 待保存重放沿用最初结算时间，外部旧库导入与 schema 快路径解耦，同内容换路径不重复；
- schema v4 检查行级不变量，并用触发器约束后续直接写入；无效行在备份后隔离；
- 本机档案使用 UUID，显示名、最后使用档案、设置、进度和存档槽有实际读写入口；
- 启动器使用 `TEXTINPUT/TEXTEDITING`，推箱子和祖玛保存进度，2048 自动保存和恢复；
- 2048 使用 BaseGame 的统一保存状态机，只保留同 attempt 新 revision 的排队策略；
- optional Flask 对查询参数和 JSON 错误保持一致，HTTP 重试按 attempt 维度清理；
- 增加三平台 CI、coverage 产物、版本记录和社区维护文档。

## 非目标

- 不增加账号、云端存储、公网排行榜、匹配、反作弊、强制联网或遥测；
- 不在没有独立规则版本和验收用例时改变五款游戏的核心计分与随机规则；
- 不由代码提交替仓库所有者选择 LICENSE；
- 不声称本机注入测试等同于 Windows、Linux 和 macOS 的真实桌面打包验收；CI 提供的是
  headless smoke，而不是安装器证明。

## 关键决策

1. 只有 mutation 本身永久非法时才可清除 pending；所有存储基础设施错误默认保留。
2. `StorageErrorKind` 与 `SaveState` 使用枚举，catalog 的 score policy 也使用枚举并在
   import 时验证。
3. pending envelope 的 `created_at` 是结算事实时间，重放写入 attempt 的 finished/achieved
   时间时继续使用该值。
4. external legacy 内容 hash 负责跨路径去重，路径状态 marker 负责同一路径快速启动；失败
   marker 有重试时间，不能触发反复 schema backup。
5. schema v4 在迁移事务内修复旧 transport/profile 身份、隔离坏行、创建约束触发器和
   `expires_at` 索引；回执每批最多删除 500 条。
6. 显示名不再承担身份语义。旧显示名 profile 映射为确定性 UUID，新档案生成随机 UUID。
7. 启动器允许记录服务在后台初始化；同步 LocalGameStore API仍保留给脚本、测试和 Flask。
8. 2048 自动存档只接受当前 ruleset、版本 1、4×4 且值为 0 或不小于 2 的二次幂棋盘。

## 验收标准

- F01–F22 均有代码、测试或具体不采纳理由；
- FULL 与 outbox ENOSPC 同时发生时 mutation 仍留在内存并可在释放空间后提交；
- NaN、Infinity、深嵌套、超大和逐文件 OSError 不逃出 outbox 构造/扫描；
- pending 最终 commit/quarantine 可观察，BaseGame 和 2048 都能回写界面；
- 手动 retry 调用和异步 submit 调用不阻塞 pygame 线程；
- 旧库换路径不重复，重放不改变完成时间，当前坏行在备份后进入 `invalid_attempts`；
- 零分推箱子完整通关仍提交，并列第一显示相同奖牌，最近记录不显示竞技奖牌；
- 档案、设置、进度和 2048 存档完成真实数据库往返，昵称与 IME 有界面路径；
- 全部功能、存储、固定输入、渲染、资源和 SQLite 并发检查通过；
- Ruff、编译、wheel、shell、whitespace 和数据库指纹检查通过；
- 提交只包含本轮相关改动以及用户已放入工作区的审查文档替换。
