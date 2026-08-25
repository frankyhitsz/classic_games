# 第十六次审查修复规格

## 目标

逐条核对第十六次审查的 26 条 Finding 与 121 个优化任务。优先关闭 score spool 的 hard-link 发布窗口和
progress set/merge 语义冲突，再加固 orphan、state clock、transaction、archive、构造生命周期和游戏端
single-flight/退出保存行为。

## 范围

- score canonical 全程使用 request lock 和 single-link atomic replace；durable receipt 前二次读取验证；
- progress resolver 明确 set/set、set/merge、merge/set、merge/merge 四种组合；
- score/state/clock orphan 在 lock 后复查稳定性，lock timeout 保留，坏 canonical 先隔离；
- state clock bounded/no-follow，future timestamp 拒绝，LocalStateEvent 带完整 winner identity；
- transaction terminal/unsafe root 分型，raw sidecar 进入 manual recovery，fresh staging 纳入 inventory；
- import/replace 空间 preflight，reserved prefix 完整保护；export 默认 snapshot-only；
- archive/pending 全部 descriptor-level bounded read，历史 pending evidence-only，v3 冻结并记录 v4 目录设计；
- Store/Flask canonical path，backend 构造失败释放 worker/session；
- slot intent 返回 resolution，2048 RNG 可注入，Sokoban progress 单 key single-flight；
- Sokoban 练习前 campaign 现场写 durable slot，异常退出后可恢复；
- API 非 loopback 需显式确认，build-system 精确固定，Python 声明与 3.11–3.13 CI 一致；
- 任务书、逐 Finding 答复和 121 项矩阵归档到 `docs/audits/`。

## 约束与非目标

- Archive v3、transaction v3 和现有 state journal 不在同一版本号下改字段；需要新字段的设计进入 v4；
- raw SQLite 主文件无法证明包含 WAL/SHM 时停止自动 rollback，不猜测 sidecar 内容；
- transaction rollback、fresh DB 和用户 backup 生命周期不同，本轮以 preflight 防止磁盘耗尽，不强行共用；
- branch protection、三平台 hash lock、LICENSE、签名和安装包需要外部权限或发行环境，准确保留状态；
- P2/P3 路线不冒充当前 Bug，但每一项都在矩阵中给出已有基础、待办或外部状态。

## 验收标准

- 26 条 Finding 有逐条成立性结论；两个 P0 和关键 P1 有 fault/model 回归；
- canonical score 在发布与扫描期间 `nlink=1`，lock timeout 不丢 temp；
- set/set 不产生空 component，set/merge 结果可被当前 parser/Store 接受；
- quarantine 失败、坏 target、future clock、unsafe transaction root 和 raw sidecar 均 fail closed；
- export 默认不修复，`inspect-archive` 不打开目标数据库；历史 pending 不进入 active outbox；
- equal revision event、slot resolution、2048 RNG、Sokoban Future coalescing 与 durable campaign 恢复有测试；
- 至少两轮“发现—修复—复验”，通过 Ruff、compile、storage、gameplay、stress、release 和远端 CI；
- 最终提交推送到 `origin/main`，远端 CI 成功且工作区干净。
