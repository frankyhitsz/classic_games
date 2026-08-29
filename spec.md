# 第十七次审查修复规格

## 目标

逐条核对第十七次审查的 30 条 Finding 与 P0–P3 优化清单。以当前代码和本机测试为准，先关闭
progress component 重放、state recovery BUSY 分型、archive 自兼容、terminal transaction cleanup 和
2048 settled autosave 五个发布阻断族，再收口相邻的提交清理、时间、事件、生命周期和推箱子会话契约。

## 当前范围

- progress resolver 在 LWW 前识别 aggregate 已吸收的 component；Store 对普通 set replay 同样检查
  merge receipt；legacy upgrade、live、orphan 和 import 共用 resolver；
- state reject temp 使用唯一文件名，在 key/digest lock、grace 和稳定 fingerprint 下恢复；BUSY 不隔离
  journal、marker 或 restore；state timestamp 的解析结果不依赖当前 wall clock；
- archive writer 发布前执行 reader 等价的节点、字节、manifest 和 hash 校验；增加不打开用户数据库的
  deep verify；historical pending 先按 ruleset 分类，再决定是否解析为 active；
- terminal import root 先原子改名到 cleanup namespace，再尽力递归删除；startup、status 和 export 使用
  同一 transaction classifier；transaction journal 的 reader/writer 共享有界 JSON 契约；
- 2048 退出只保存 settled board；旧 ruleset slot 先保留到本机隔离记录再新开；
- SQLite commit 与 journal cleanup 分离，cleanup 失败保留 committed 结果；同 identity state event 遵守
  终态优先；application lease 覆盖仍在运行的 worker；score lock 改为固定 stripe；
- Sokoban 恢复统一 attempt identity，检查 outer ruleset、ledger、坐标和逐步可达性；恢复点在正常关闭或
  明确返回 campaign 前保持 active；异步 Future 创建不再被当作 durable；
- 任务书、逐 Finding 答复和完整优化矩阵归档到 `docs/audits/`。

## 约束

- 不把网页端控制流推演当作已证实问题；每项必须有当前代码、定向测试或运行证据；
- Archive v3 和现有 journal reader 保持兼容；需要新 manifest 语义时使用显式新版本或 ADR；
- 不删除无法证明可恢复的本机数据；BUSY、INVALID、UNREADABLE 分开处理；
- Optional Flask 仍是本机调试适配器，默认不联网、不遥测；
- branch protection、LICENSE、签名和素材权利只在取得相应仓库/权利人证据后标记完成。

## 验收标准

- 30 条 Finding 和完整 P0–P3 ID 均有成立性、处理状态、代码证据与测试证据；
- 五个 P0 组合不变量有定向故障或逐帧测试；成功 export 必能由同版本 load，BUSY 不触发 quarantine；
- commit 后 cleanup 锁失败仍为 COMMITTED；terminal cleanup 失败不留下 active `.import-*`；
- 2048 四方向普通/合并/胜负边界的动画中退出均恢复 settled board；
- Sokoban 恢复后的 attempt、ruleset、ledger、tombstone 和 durability 契约一致；
- 至少两轮独立“发现—修复—复验”，通过 Ruff、compile、storage、gameplay、stress、release 和远端 CI；
- 最终提交推送到 `origin/main`，远端 CI 成功且工作区干净。
