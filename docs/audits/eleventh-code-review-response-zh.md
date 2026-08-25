# 第十一次代码审查核对结果

## 结论

任务书的基线提交 `8f1b713` 与审查时的 `main` 一致。31 条 finding 中，F01–F24、F26–F27、
F29–F30 均能在当时代码中复现，已采纳修复；F25 是真实的单槽产品限制，本轮先关闭静默覆盖，
但没有冒充已经交付多槽 UI；F28 是 GitHub 仓库设置，不是代码缺陷；F31 是权利人的发行决定，
仓库只能提供清单，不能代替权利人选择许可证。

审查并非“bug 越改越多”的证据。第十轮加入了 receipt baseline、2048 owner token 和数据 CLI，
第十一轮大部分问题正是对这些新安全边界做更强的故障注入。它们揭示的是实现仍缺少的原子性、
冲突报告和生命周期条件，而不是五款游戏基础规则普遍退化。审查中列出的 P2/P3 大量项目本身也
被原任务书第 6 节定义为不建议在本轮强行加入的路线图，不能把“尚无双人、编辑器、安装包”统计
成当前版本新增的 bug。

## Findings

| ID | 判断 | 处理与证据 |
|---|---|---|
| F01 | 成立 | export path guard 拒绝 DB、WAL、SHM、journal、pending、quarantine、backup 及 symlink；有定向测试。 |
| F02 | 成立 | archive v2 分别保存完整 score envelope 与 state operation，导入恢复到 active journal。 |
| F03 | 成立 | attempts 不再导入自增 `id`；同时检测 attempt UUID、request ID、source key；移除 `INSERT OR IGNORE`。 |
| F04 | 成立 | importer 只调用 missing-only baseline seed，既有 receipt 不改写。 |
| F05 | 成立 | baseline 保留旧 high-water；latest-value operation 与 baseline 先按 `occurred_at` 截止。 |
| F06 | 成立 | state outbox 返回 previous operation；永久拒绝时隔离新 winner 并原子恢复旧 pending。 |
| F07 | 成立 | K 接管前重新读取；schema 5 用 owner/epoch/slot revision/full-row hash 做 CAS，竞态失败再次读取。 |
| F08 | 成立 | 恢复 won/gameover 且 confirmed score 缺失或偏低时，以同 attempt 的新 revision 补交。 |
| F09 | 成立 | v4 恢复现在读取保存的 `won_announced`；未宣布 milestone 恢复到 win overlay。 |
| F10 | 成立 | owner epoch 每次 takeover/released claim 递增；旧实例的旧 epoch 不能复活。 |
| F11 | 成立 | slot receipt hash 纳入 state、state version、ruleset version。 |
| F12 | 成立 | pending high-water 在后台扫描；每个新 key write 还会在 durable clock 分配前读取当前 pending revision。 |
| F13 | 成立 | committed tables 在显式 SQLite read transaction 中读取。 |
| F14 | 成立 | archive 设 128 MiB、深度、节点、字符串、文件数和 recovery 总量限制，并拒绝非标准数字。 |
| F15 | 成立 | shared planner 校验 UUID、时间、JSON、progress、slot version、关系和 alternate identity。 |
| F16 | 成立 | CLI 使用 exclusive maintenance lock；桌面 score/state durable write 使用 shared application lock。 |
| F17 | 成立 | 现有普通文件默认报冲突，只有 `--force` 可替换。 |
| F18 | 成立 | recovery 明确为 evidence；导入只写 `imported-recovery/<archive-id>`，不覆盖 active journal。 |
| F19 | 成立 | 每表行数/编码量、archive 总量和 recovery 总量均有硬边界；未采用复杂流式 JSON，内存已由配额封顶。 |
| F20 | 成立 | direct baseline revision 至少为旧 receipt + 1；溢出时由时间截止语义保持 winner。 |
| F21 | 成立 | 健康 receipt 只读；仅发现损坏时二次进入 `BEGIN IMMEDIATE` 并重新检查。 |
| F22 | 成立 | 构造器只用 `os.scandir` 判断 score pending，解析、升级、隔离在 worker 完成。 |
| F23 | 成立 | permanent state rejection 不再直接 unlink；拒绝文件留在 state quarantine。 |
| F24 | 成立 | 普通连续移动使用 150 ms latest-value debounce；终局、claim、release 和关闭仍立即 flush。 |
| F25 | 成立但非数据损坏 | 当前 UI 仍是一个 autosave 槽；schema 5 已保证多窗口不能静默共用，多槽选择/删除另需 UI 设计。 |
| F26 | 成立 | release runner 每阶段有独立 timeout，超时以 exit 124 写入 JSON/JUnit。 |
| F27 | 成立 | release profile 构建 wheel，在无 system-site-packages 的 venv 安装依赖并检查 import/console script。 |
| F28 | 仓库设置 | required checks 需要修改 GitHub branch protection；“提交并推送”不授权额外更改仓库保护规则。 |
| F29 | 成立 | checkout/setup-python/upload-artifact 全部固定到当前 v7 tag 对应 commit SHA。 |
| F30 | 成立 | `constraints-release.txt` 固定完整解析依赖闭包；release 同时输出 CycloneDX SBOM。 |
| F31 | 成立但需权利人决定 | `docs/release-governance.md` 记录代码、素材、商标和版本检查；没有伪造 `LICENSE` 结论。 |

## 游戏专项

- 俄罗斯方块：确认同义物理键会触发两次逻辑边沿，已按 action state 修复 Left/A、Right/D、
  Down/S；7-bag、ghost、hold、lock delay 会改变现有 `tetris-assist-2` 体验，应作为新 mode/ruleset。
- 贪吃蛇：RNG 注入、速度/穿墙/障碍和双人模式是合理扩展，但不是当前 classic 规则的正确性
  缺陷；若实现必须让成绩按 mode/ruleset 分开。
- 2048：任务书列出的八项高优先级中，CAS、epoch、终局补分、v4 milestone、debounce 和 receipt
  已修；terminal 仍明确显示原棋盘并提供新开/返回，多槽浏览与删除没有伪装成已完成。
- 推箱子：选关、最佳推动、死锁提示、XSB 与编辑器均是合理路线图；它们需要地图格式、计分和
  campaign/practice 兼容定义，不是现有 16 关可解性回归。
- 祖玛：增加了每种球色独立形状标记，颜色不再是唯一信息；reaction FSM、训练模式、道具和
  轨道编辑器保留为玩法演进，现有 pending reaction/swept collision/path bisect 继续测试。

逐项 P0–P3 状态见 `eleventh-optimization-matrix-zh.md`。
