# 第十一次审查修复规格

## 目标

核对第十一次审查的 31 条发现和 129 项建议，优先关闭可能覆盖数据库、遗漏待保存记录、
静默漏导成绩、回退状态 winner 或让 2048 旧窗口覆写新棋盘的问题。审查中的长期产品建议
保留逐项结论，但不能混入现有 ruleset，也不能写成已经交付。

## 范围

- 数据归档升级为 schema 2：保护数据库和 journal 路径，普通文件默认不覆盖，数据库使用一致
  读快照，并纳入可恢复的 score/state active pending；
- archive 带 manifest 与内容 hash，限制总大小、深度、节点、字符串、恢复文件数量和总量，拒绝
  NaN/Infinity；恢复证据只写入隔离目录；
- preview 与 apply 共用导入计划，区分新行、完全重复、语义冲突和非法行；attempt 自增 ID 不导入，
  同时检查 attempt UUID、request ID 和 source key；
- state baseline 不降低既有 revision，旧 journal 与 direct baseline 按发生时间裁决；slot receipt
  绑定 state、state version 和 ruleset；
- 新 state operation 写入前执行纯语义校验；永久拒绝时隔离新 journal 并恢复旧 pending，启动时
  在后台扫描 pending high-water；
- 2048 slot schema 5 使用 owner epoch、slot revision 和完整 value hash 做 CAS；接管前重新读取，
  终局未确认成绩自动补交，v4 `won_announced` 按原值恢复；
- 2048 普通移动自动存档合并 150 ms 内的连续写，关闭、终局、owner claim/release 仍立即落入
  durable state 流程；
- release runner 为每个阶段设置 timeout，构建 wheel 后在隔离 venv 安装并检查入口，生成 SBOM；
  Actions 固定到 commit SHA，发行约束覆盖完整依赖闭包；
- 修复俄罗斯方块同一逻辑动作的同义键重复边沿。

## 约束

- 默认仍为本地单机，不增加账号、云同步、遥测、广告或联网对战；
- 不把 7-bag、撤销、多地图编辑器、双人模式等新玩法塞入当前 ruleset；需要改变计分、随机分布
  或完成条件时，必须先定义新 mode/ruleset 和迁移策略；
- import 必须显式 `--apply`，执行前创建数据库备份；冲突不再静默跳过，而是拒绝整次导入；
- export/import 取得维护锁；正常桌面写使用共享应用锁，文件解析和 fsync 不进入渲染线程；
- `LICENSE` 只能由代码与素材权利人选择。仓库提供权利、商标和素材清单，不代替授权决定；
- branch protection 是额外的 GitHub 仓库设置，本次“提交并推送”授权不包含修改保护规则。

## 关键决策

1. committed 表与 active pending 是两类不同事实，archive manifest 分别计数；quarantine/backup
   属于证据，不直接恢复到活动 journal 路径。
2. 导入不是“尽量 INSERT”。任一身份冲突、关系错误或语义错误都会在 dry-run 中出现，并阻止
   apply；完全相同的行可以幂等跳过。
3. latest-value baseline 的业务发生时间可以淘汰更早 journal，即使旧 journal 的逻辑 revision
   因历史时钟故障更高；单调 merge 仍按 component 幂等合并。
4. state journal 替换不是提交。数据库永久拒绝新 winner 时，新文件转入 quarantine，替换前的
   pending 原子恢复；这样既保留故障证据，也不丢用户唯一副本。
5. 2048 所有权是带 epoch 的租约状态。接管或 released claim 必须引用刚读到的 owner、epoch、
   slot revision 和完整 value hash；失败后重新读取并停止接收棋盘输入。
6. 单槽是当前 UI 策略，不再声称支持多窗口共享。CAS 保证不会静默覆盖；多槽需要独立的信息
   架构、选择界面和删除/导出语义，列入后续 ruleset-neutral 功能。

## 验收标准

- F01–F31 和 P0–P3 共 129 项均有成立性、处理状态及代码/测试/决策证据；
- 导出不能指向 DB/WAL/SHM/journal、active pending 或恢复目录；普通现有文件只有 `--force`
  才能替换；
- committed 表来自同一 SQLite 快照，score/state pending 可往返恢复，manifest 损坏可检测；
- 非空目标库导入不受 surrogate ID 碰撞影响，alternate unique 冲突明确拒绝，preview 与 apply
  使用同一计划；
- import、direct write、receipt rebuild 和 journal replay 均不能降低或绕过当前 state winner；
- 2048 stale takeover、released owner 复活、v4 milestone 和 terminal score 均有确定性测试；
- 完成两轮独立复查，Ruff、compile、storage、gameplay、stress、release、wheel 安装和数据恢复
  演练通过；
- 推送后核验远端提交与 GitHub Actions 当前 run。
