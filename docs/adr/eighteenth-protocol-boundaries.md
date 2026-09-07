# 第十八轮协议边界

## 删除与进度重置

选择 SQLite schema 8 的持久屏障，不新增一个可离线排队的 delete journal。隔离只有在原始内容、
删除和屏障一起提交后才返回 QUARANTINED；BUSY/FAILED 不删除。这样没有已确认但尚未持久化的删除，
也不需要把 destructive intent 混入现有 save_slot reader。v7 升级保留已有 replay receipts。

进度 reset 是替换操作，不是可交换的 merge。纯 merge 的 component map 做幂等、交换、结合检查；
混合 set/merge 按操作顺序及 replacement 规则判断，不能把纯 merge 的代数性质推广到 set。
数据库的 reset frontier 在后续 merge 后仍保留。schema 3 的 aggregate 不含每个 component 的独立值与
revision，不能从任意历史 aggregate 中精确减去一个被重置的部分；未来若要跨 reset 拆分 aggregate，
必须设计显式新版本和迁移，不能猜测各 component 的贡献。对应任务的全序列模型证明仍未完成。

component receipts 不做按日过期或 Bloom filter 清理。新 set 安全截断当前 key 的旧 component rows，
status 暴露三类 receipt/barrier 数量。仅 merge 的长期集合仍可能增长；要进一步压缩，需要可证明的
frontier/区间协议，不能用概率集合决定是否丢弃真实进度。

## 文件发布与恢复

输出使用 Windows 的非覆盖 rename、macOS `renamex_np(RENAME_EXCL)` 或 Linux
`renameat2(RENAME_NOREPLACE)`。不支持原子非覆盖改名时拒绝发布，不回退为会暴露半份输出的复制。
进程在改名前退出只留下同目录隐藏 temp；改名后输出为完整、单链接文件。暂存文件不自动当作备份使用，
也不自动删除用户目录中无法证明身份的同名文件。

接口依据：[Linux rename 文档](https://man7.org/linux/man-pages/man2/renameat2.2.html)、
[Apple exclusive rename 能力说明](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming)。

score 的 stripe + legacy 双锁至少保留一个兼容周期。只有不再支持 0.8 同目录并行写入，且 release notes
明确该升级边界后，才移除 legacy 锁。过渡期不能宣称总锁文件数固定为 256。

## Archive v4 与后续格式

v4 仍是单个 canonical JSON，ruleset catalog 仍是每游戏一个主要版本，不表示完整历史集合。
本轮没有改变它的含义。若设计 v5，应按 committed、active pending、evidence 三类分别记录 observed
ruleset sets；正文按 section 保存，每节独立 size/hash，索引带版本，并考虑随机读取与中断恢复。
只有先有大数据实测和迁移样本，才把该设计投入发行；当前只作设计约束。

现有内存估计为编码体积的 8 倍，只用于诊断，不是 RSS 上界。Python 对象、解码和临时 SQLite 仍有额外
开销；MemoryError 返回结构化失败，但不能保证操作系统不会直接终止超内存进程。

## 后续实施范围

数据管理 GUI、输入重映射、手柄、显示设置、无障碍、回放和编辑器建议方向合理。本轮实际增加了
replace 预览、紧凑撤销历史、依赖哈希和类型门禁，未将这些界面和玩法模式与数据库迁移一起交付。
它们不是“不属于产品”的拒绝项；逐项未完成状态见优化矩阵，不能用已有 CLI 或按键快捷方式冒充 GUI。
