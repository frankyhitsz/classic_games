# 本地基准记录

基准只用于发现回归，不作为不同机器之间的性能承诺。运行时固定临时数据库、输入规模和 seed，
同时记录 Python、操作系统、耗时和峰值。

## Archive v2 内存

2026-08-25 在 macOS 13.7.8 arm64、Python 3.11.15 上，以一个档案写入 5,000 条合法 Snake
attempt，然后在 `tracemalloc` 下执行完整导出：

| rows | archive | elapsed | tracemalloc peak | peak/archive |
| ---: | ---: | ---: | ---: | ---: |
| 5,000 | 2,344,194 B | 0.600 s | 11,332,465 B | 4.83× |

这证实 v2 确实同时保留 rows、canonical JSON 和编码结果；审查提出的内存放大成立。不过当前每表
64 MiB、archive 128 MiB 和 JSON 节点/字符串上限让它是有界风险，而不是无界输入漏洞。v3 的流式
设计条件和不继续扩充 v2 的决定记录在 `storage-protocol.md`。

## 既有 stress profile

`python -m tests.stress` 使用固定 seed 执行 20,000 步玩法状态、并发持久化、资源循环、FD 检查，
并输出当前机器的 Zuma render p95 与保存 p99。结果必须与运行日志一起解释；共享 CI runner 的数值
不作为帧时间门槛。
