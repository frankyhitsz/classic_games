# Classic Games Hub 第三次代码审查与本地优先优化任务书

> 审查日期：2026-08-24
> 当前基线：`main` 分支 commit `f88a6ffed22a25980abda9ce6909a403631870bf`（`f88a6ff`）
> 上次审查基线：commit `9f9ef1ddb575181eb94c4cbf12eb39eb75e622bf`（`9f9ef1d`）
> 产品定位：**本地运行、单机为主、无需账号和公网竞技的经典小游戏合集**
> 审查范围：客户端、五款游戏、Flask/SQLite 记分服务、测试、脚本、依赖与仓库文档

---

## 0. 执行摘要

本轮修复总体质量较好。上一轮指出的多数确定性问题已经被真正修复，而不是通过修改测试、吞掉异常或简单加条件绕过：

- 自定义端口下后端、健康检查和客户端地址不一致：已修复；
- 测试可能创建或修改默认 `data/scores.db`：已修复；
- 通用成绩 Future 没有被消费、提交与榜单读取竞态：已修复；
- 推箱子在服务端确认前标记“已提交”：已修复；
- Tetris 同义物理键释放错误：已修复；
- Tetris 大 `dt` 补步跨越新旧方块：已修复；
- SQLite 请求异常返回 HTML 500：主要请求路径已修复为 JSON 503；
- 2048 低分覆盖高分：已改为单调不下降；
- 读写退避状态互相干扰：明显改善；
- 贪吃蛇长时间停顿后不可见地补多步：已修复；
- 客户端与服务端昵称长度：已统一为 32。

但当前又暴露出一组更深层的可靠性和数据语义问题：

1. **最终失败的成绩只保存在内存**，程序退出后仍会丢失；
2. **结算页重试与 BackendClient 全局重试同时存在**，非替换型游戏可能重复插入同一成绩；
3. `BackendClient.drain()` 会在完成回调尚未更新失败队列时提前返回；
4. **2048 输入队列存在“迟到输入”**：无效果方向被取出后，后续方向可能滞留，并在下一次移动动画结束时意外执行；
5. 2048 达成 2048 时队列没有清空，继续游戏后可能执行胜利前残留输入；
6. 服务端“幂等请求”只比较游戏、玩家和分数，没有比较完整语义负载；
7. `score_requests.score_id` 可能指向已被 `replace=True` 删除的成绩行；
8. “成绩已保存”有时只表示旧个人最佳被保留，**本次游玩记录实际上没有保存**；
9. README 所谓“离线模式”只保证能玩，不保证离线成绩持久保存；
10. 默认启动仍必须拉起 Flask，尚未落实最符合项目定位的“进程内本地存储”。

本轮最关键的结论是：

> **游戏玩法层面的主要漏洞已得到较好修复；现在的首要工作应从继续补 HTTP 重试，转为建立真正可靠的本地成绩、进度和存档体系。**

不建议继续把复杂度投入账号、云端、反作弊、公网排行或赛季系统。推荐把 Flask 降为可选的学习/API 适配器，默认桌面运行直接读写本机 SQLite。

---

## 1. 审查方法、证据等级与限制

### 1.1 已完成的工作

- 锁定当前 `main` 的最新提交；
- 阅读当前生产代码、脚本、测试和仓库文档；
- 将本轮实现与上次审查的 F01–F13 逐项对照；
- 执行 Python 语法编译检查：
  - `client/`
  - `server/`
  - `tests/`
- 当前源码通过 `compileall`；
- 统计结构热点和模块规模；
- 使用最小可执行替身进行针对性复现：
  - `BackendClient.drain()` 完成回调竞态；
  - 2048 无效果输入导致后续方向滞留并迟到执行。

### 1.2 本轮两个实际复现结果

#### A. `drain()` 提前返回

构造一个已完成网络任务，但让 `_capture_score_save()` 回调继续阻塞的场景：

```text
drain_returned: True
capture_finished: False
failed_save_count: 0

回调完成后：
failed_save_count: 1
```

即：

> `drain()` 返回真，并不代表失败保存记录已经完成归档。

根因是 `_discard_future` 先从 `_pending_futures` 删除 Future，随后才执行 `_capture_score_save`。

#### B. 2048 迟到输入

构造：

```text
当前方块位于顶行
动画期间队列 = [up, left]
```

动画完成后：

- `up` 被取出；
- `up` 是无效果移动；
- `left` 留在队列中。

随后玩家主动按 `down`：

```text
down 移动完成后，之前残留的 left 被自动执行
```

复现状态：

```text
先前动画结束：位置 (0, 1)，队列 [left]
玩家 down：逻辑位置 (3, 1)
down 动画结束：位置变成 (3, 0)，并开启了新的 left 动画
```

这是确定性的游戏输入错误。

### 1.3 证据等级

| 标记 | 含义 |
|---|---|
| **已复现** | 通过可执行最小场景重现 |
| **代码路径确定** | 从当前控制流可确定发生 |
| **修复到位** | 上轮问题已形成完整闭环 |
| **基本到位** | 原始缺陷关闭，但仍有相关边界 |
| **部分到位** | 修复了主要症状，数据或生命周期仍不完整 |
| **待基准验证** | 存在性能热点，但需真实 pygame 环境测量 |
| **产品决策** | 需按本地单机定位确定行为，不应冒充 Bug |

### 1.4 环境限制

当前审查环境未安装 `pygame` 与 `Flask`，因此没有完整执行仓库自己的原生 headless 回归套件，也没有独立复现仓库文档中声称的：

- 18,000 次随机输入；
- 120 次并发 SQLite 写入；
- 各游戏 80 帧渲染 p95；
- “103 项检查通过”。

本次实际完成的是：

- 全部 Python 文件语法编译；
- 逐文件静态审查；
- 两个确定性新问题的可执行复现；
- 对现有测试实现和报告数字进行结构核对。

当前 `tests/regression.py` 中按编号章节和 AST `run(...)` 调用计数均为 **95**，而 `task.md` 写的是 **103**。正式发布说明不应引用无法从仓库直接复现的数字。

---

## 2. 上一轮问题修复验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| F01：通用异步提交 Future 未消费 | **修复到位** | `BaseGame` 每轮轮询 Future，并维护 saving/saved/failed |
| F02：POST 与排行榜 GET 竞态 | **修复到位** | 保存中不读取榜单；ACK 后失效缓存并重新读取 |
| F03：推箱子保存确认前标记已提交 | **修复到位** | `_pending_total` 与 `_confirmed_total` 分离，ACK 后确认 |
| F04：2048 失败后无可靠重试 | **部分到位** | 有有限自动重试、失败提示和手动重试；进程退出后仍会丢最终失败记录 |
| F05：自定义端口不一致 | **修复到位** | `run.sh` 派生并导出统一 `GAMES_API_URL` |
| F06：测试污染默认 DB | **修复到位** | 后台服务和直接 import 测试分别使用临时 DB，并比较默认 DB 指纹 |
| F07：Tetris 同义键释放错误 | **修复到位** | 按实际物理键集合与按下顺序推导动作 |
| F08：Tetris 大 dt 跨方块补步 | **修复到位** | 引入 `piece_generation`、dt 上限和跨代中止 |
| F09：线程池、Session 和 Future 无关闭 | **基本到位** | `close()` 已实现；但 `drain()` 仍有回调竞态，失败队列未持久化 |
| F10：全局退避并发语义过粗 | **基本到位** | 读写退避拆分、有锁、旧成功不能清除新失败；仍存在优先级和复杂度问题 |
| F11：2048 低分覆盖高分 | **修复到位但有副作用** | 分数单调不下降；低分请求仍更新 `updated_at`，污染最近记录 |
| F12：`plays` 不是游玩次数 | **准确改名** | 已改为 `records`；真实 attempt 模型仍未建立 |
| F13：SQLite 错误返回 HTML | **主要路径到位** | 请求期 SQLite 异常统一 JSON 503；import/startup 副作用仍可能直接终止进程 |
| 贪吃蛇长帧不可见补步 | **修复到位** | 超过 250 ms 的恢复帧只走一步 |
| 昵称长度不一致 | **修复到位** | 客户端与服务端均为 32 |
| `_collides(..., board=空列表)` 被忽略 | **修复到位** | 改为 `if board is None` |
| 主线程同步 HTTP | **修复到位** | pygame 渲染线程主要请求已异步化 |

### 验收结论

本轮修复中，最值得肯定的是：

- 修复不只覆盖了报告中的单一复现场景；
- 多处引入了 generation、ACK、请求 ID、读写退避等正确抽象；
- 测试隔离和启动参数传播已经达到可接受水平；
- 玩法层面的大部分上轮 P0 已经关闭。

现在的问题不再是“没有重试”，而是：

> 多套重试与确认机制叠加后，谁拥有一条成绩、谁负责最终落盘、谁决定已保存，仍然不够统一。

---

## 3. 当前发布阻断与高优先级问题

## 3.1 CG3-F01：最终失败成绩仍只存在内存

- **级别**：P0
- **证据**：代码路径确定
- **位置**：`client/common/network.py`
- **涉及字段**：`_failed_score_submissions`

有限重试全部失败后，保存负载只进入 Python list：

```python
self._failed_score_submissions: list[dict] = []
```

启动器允许按 S 重试，但以下情况仍会丢失：

- 玩家直接关闭启动器；
- 操作系统结束进程；
- 程序崩溃；
- 机器重启；
- 独立启动某个游戏后退出；
- 后端一直不可用。

`close()` 会等待当前线程任务，但不会把失败队列写入磁盘，也不会在下次启动恢复。

### 影响

README 说后端未启动时可进入离线模式，但当前“离线”只保证游戏继续运行，不保证成绩保存。

### 推荐修复

最符合产品方向的方案不是继续增加 HTTP 重试，而是：

1. 每局结果先写入本机 SQLite；
2. 写入成功即显示“已保存到本机”；
3. Flask 只作为可选读取/展示适配器；
4. 如果暂时保留 HTTP，至少使用本地 SQLite/JSON outbox 持久化待写记录。

---

## 3.2 CG3-F02：结算页重试与全局失败队列会导致重复成绩

- **级别**：P0
- **证据**：代码路径确定
- **位置**：
  - `client/common/ui.py`
  - `client/common/network.py`

当前存在两个重试入口：

1. 结算页 `BaseGame.retry_score_save()`；
2. 启动器 `BackendClient.retry_failed_saves()`。

首次提交失败后：

- BackendClient 会保留原请求及原 `request_id`；
- 结算页按 S 会重新调用 `_submit_result_score()`；
- 新调用会生成新的 `request_id`；
- 原失败项仍保留在 BackendClient 队列。

对 Tetris、Snake、Zuma 这类 `replace=False` 游戏，可能发生：

```text
第一次请求失败 → 旧请求进入失败队列
结算页按 S → 新 request_id 保存成功
返回启动器仍提示一条失败记录
启动器再按 S → 旧 request_id 又保存成功
```

最终产生两条相同成绩。

### 根因

重试所有权不唯一：

- UI 认为自己在重试一条逻辑保存；
- BackendClient 认为 UI 发起了另一条新保存。

### 修复要求

建立 `SaveHandle`：

```text
save_id / request_id
state
payload
retry()
cancel_ui_observation()
```

同一次逻辑保存的所有自动和人工重试必须复用同一个稳定 ID。

或者更直接：

- 取消 BackendClient 内部失败队列；
- 统一由持久化 LocalSaveQueue 管理；
- UI 只调用 `retry(save_id)`，不得重建请求。

---

## 3.3 CG3-F03：`BackendClient.drain()` 语义不真实

- **级别**：P1；若用作退出保证则升为 P0
- **证据**：已复现
- **位置**：`client/common/network.py`

当前回调注册顺序：

```text
Future 完成
  → _discard_future：从 pending 集合删除
  → _capture_score_save：更新失败队列
```

`drain()` 只检查 pending Future，所以可能在第二个回调完成前返回真。

### 风险

未来若使用：

```python
backend.drain()
backend.close()
```

并据此向用户保证“所有成绩已收尾”，该保证不成立。

测试中通过 `time.sleep(0.02)` 等待回调，也说明测试没有真正验证 drain 的契约。

### 修复要求

使用一个统一完成回调：

```text
解析结果
更新成功/失败记录
最后从 pending 移除
```

`drain()` 的验收条件必须是：

```text
网络任务为空
且完成回调为空
且保存状态归档完成
```

禁止通过 sleep 修复时序测试。

---

## 3.4 CG3-F04：2048 无效果缓冲输入导致迟到执行

- **级别**：P0
- **证据**：已复现
- **位置**：`client/games/game_2048.py::_tick_animations`

动画结束后只取出一个方向：

```python
self._move(self._queued_directions.popleft())
```

如果这个方向没有移动棋盘：

- 不会开始新动画；
- 队列中的下一个方向不会继续处理；
- 它会残留到未来某次有效移动结束后执行。

### 推荐规则

动画结束后，按顺序消费队列，直到：

- 某个方向产生有效移动；或
- 队列为空。

伪代码：

```python
while queue and anim_t >= 1:
    direction = queue.popleft()
    moved = try_move(direction)
    if moved:
        break
```

`_move()` 应返回 `bool moved`，不要通过观察 `anim_t` 猜测。

---

## 3.5 CG3-F05：2048 胜利前输入会在继续后迟到执行

- **级别**：P1
- **证据**：代码路径确定
- **位置**：
  - `_tick_animations`
  - `_continue_after_win`

动画结束判定达成 2048 后：

- 状态变为 `won`；
- 因非 playing，不消费队列；
- 队列仍保留；
- `_continue_after_win()` 只恢复 playing，没有清空队列。

玩家继续后，残留方向可能在下一次移动动画结束时执行。

### 修复要求

规则二选一并写测试：

- 进入胜利 overlay 时清空队列；推荐；
- 或继续后立即按顺序处理，但必须让玩家知情。

暂停、游戏结束、重置、返回菜单也应有统一队列清理策略。

---

## 3.6 CG3-F06：“成绩已保存”不等于“本次游玩已保存”

- **级别**：P1
- **证据**：代码路径确定
- **位置**：
  - `server/app.py`
  - `client/common/ui.py`
  - `client/games/game_2048.py`
  - `client/games/sokoban.py`

对 `replace=True`：

- 若旧成绩高于当前成绩；
- 服务端保留旧记录；
- 返回 `ok=True`、`preserved=True`；
- 客户端显示“成绩已保存”。

但本次较低成绩没有形成新的 attempt，也不会出现在最近记录中。

### 数据语义问题

当前一张 `scores` 表同时承担：

- 每次游玩历史；
- 个人最佳；
- 2048 当前局更新；
- 推箱子完整闯关最佳；
- 最近记录。

这四者不是同一个概念。

### 推荐修复

分为：

```text
attempts        每次实际结算
personal_best   查询或缓存结果
progress        关卡进度
save_slots      未完成局
```

界面准确显示：

- 本局已记录；
- 新个人最佳；
- 未超过个人最佳；
- 保存失败。

不要用一个“成绩已保存”覆盖所有语义。

---

## 3.7 CG3-F07：低分 2048 更新会伪造最近活动

- **级别**：P1
- **证据**：代码路径确定
- **位置**：`server/app.py`

当 `submission_id` 有效、传入分数低于已有分数时：

- 保存分数仍取旧高分；
- 但仍更新 `updated_at`。

因此一个过期、乱序或低分请求会把旧记录推到“最近游戏”顶部。

### 修复要求

若传入状态没有带来任何有效变化：

- 不更新 `score`；
- 不更新 `extra`；
- 不更新 `updated_at`；
- 响应增加 `no_op=True`。

建立 attempts 模型后，最终不再需要用同一行同时表达历史和最近活动。

---

## 3.8 CG3-F08：服务端幂等键没有绑定完整请求语义

- **级别**：P1
- **证据**：代码路径确定
- **位置**：`server/app.py`

重复 `request_id` 只比较：

- `game_id`
- `player`
- `requested_score`

未比较：

- `extra`
- `replace`
- `submission_id`

所以同一个 request ID 可以被用于：

```text
同分数但不同 extra
同分数但 replace 语义不同
同分数但更新目标行不同
```

服务端会把它当作同一次请求返回旧结果。

### 修复要求

存储 canonical payload hash：

```text
SHA-256(canonical JSON payload excluding transport-only fields)
```

重复 ID：

- hash 相同：返回原响应；
- hash 不同：409 conflict。

---

## 3.9 CG3-F09：`score_requests` 可指向被删除的成绩行

- **级别**：P1
- **证据**：代码路径确定
- **位置**：`server/app.py`

`score_requests.score_id` 没有外键。

场景：

1. 请求 A 插入 score row 1，并记录 request A → row 1；
2. 后续 `replace=True` 删除 row 1，插入 row 2；
3. 请求 A 晚到重试；
4. 服务端返回 `id=1`，但 row 1 已不存在。

### 修复选项

本地优先建议：

- 幂等记录保存完整 response snapshot，而不是依赖可删除 row；
- 或 attempts 永不物理删除，只标记/聚合个人最佳；
- 若保留外键，明确 `ON DELETE` 策略。

同时：

- 给 `score_requests` 设置清理策略；
- 避免无限增长；
- 增加并发同 request ID 测试。

---

## 3.10 CG3-F10：永久性 4xx 也会进入“可重试”失败队列

- **级别**：P1
- **证据**：代码路径确定
- **位置**：`client/common/network.py`

自动重试会在非 retryable 响应后停止，但 `_capture_score_save()` 仍把所有失败记录加入同一列表。

于是以下错误会反复提示重试：

- 参数契约错误；
- 玩家名非法；
- 游戏 ID 错误；
- 请求 ID 冲突。

### 修复要求

Typed result：

```text
SUCCESS
RETRYABLE_FAILURE
PERMANENT_FAILURE
```

用户可见文案：

- 临时失败：可重试；
- 数据无效：显示错误并记录日志，不无限重试；
- 数据库损坏：给出修复/备份入口。

---

## 3.11 CG3-F11：`retry_failed_saves()` 先清空，再调度

- **级别**：P1
- **证据**：代码路径确定
- **位置**：`client/common/network.py`

当前流程：

```python
failed = list(queue)
queue.clear()
for item in failed:
    schedule(item)
```

若客户端已关闭、线程池创建失败或某个调度调用抛异常：

- 尚未调度的记录已经从队列删除；
- 可能永久丢失。

### 修复要求

逐条状态转换：

```text
failed → scheduling → in_flight
```

只有成功调度后才从 failed 移除；失败则原子恢复。

---

## 3.12 CG3-F12：Flask import 仍有文件系统副作用

- **级别**：P1
- **证据**：代码路径确定
- **位置**：`server/app.py`

模块导入时执行：

```text
创建 DB 父目录
init_db()
PRAGMA WAL
建表/ALTER TABLE
```

这解决了 Flask CLI 新库不可用，但引入：

- import 即写磁盘；
- 单元测试难注入配置；
- 路径不可写时模块直接导入失败；
- 多进程同时首次启动可能竞争迁移；
- 不能先创建 app 再选择数据库；
- 迁移没有版本和回滚。

### 修复要求

若保留 Flask：

```python
def create_app(config=None) -> Flask:
    ...
```

数据库初始化由显式 migration/startup 完成，禁止 import 时写文件。

---

## 3.13 CG3-F13：本地优先目标尚未真正落地

- **级别**：P1，战略优先
- **证据**：当前架构与 README
- **性质**：不是本轮修复失败，而是下一阶段架构任务

当前默认 `run.sh`：

- 必须启动 Flask；
- 必须等待 health 成功；
- 否则直接退出；
- 启动器显示“成绩服务可用/不可用”；
- 正常成绩保存依赖 HTTP。

这与“本地单机小游戏合集”的长期定位并不匹配。

### 推荐目标

默认：

```text
Launcher / Game
    → LocalGameStore
        → 用户数据目录中的 SQLite
```

可选：

```text
Flask API Adapter
    → 同一个 LocalGameStore
```

Flask 保留教学价值，但不再成为本地保存成绩的必要条件。

---

## 4. 各模块剩余问题

## 4.1 `client/common/network.py`

### 已改善

- 线程池异步 I/O；
- Session 复用；
- read/write backoff 分离；
- 锁保护；
- 请求开始时间防止旧成功清除新失败；
- 稳定 request ID 用于一次 Future 内的自动重试；
- 资源关闭；
- 严格 ACK 解析。

### 仍需处理

1. 失败队列只在内存；
2. 两套重试机制导致重复；
3. `drain()` 回调竞态；
4. 永久错误与临时错误未分型；
5. 清空后调度可能丢记录；
6. 重试进行中，启动器暂时显示 0 条失败，没有 `retrying` 状态；
7. 两个 worker 同时承担读和写，慢排行请求可能延迟成绩保存；
8. typed result 缺失，调用方仍大量依赖 `None`；
9. `_confirmed_replace_scores` 只按 `(game, player)`，没有模式或规则版本；
10. 当前复杂度主要源于本机 HTTP，可通过本地仓储直接消除。

## 4.2 `client/common/ui.py`

### 已改善

- 保存状态可见；
- S 键重试；
- ACK 后再读取榜单；
- generation 防旧 Future 覆盖；
- 子游戏与共享 BackendClient 生命周期区分。

### 仍需处理

1. 状态仍是魔法字符串；
2. 保存 UI 与 BackendClient 失败队列不是同一状态源；
3. “已保存”没有区分本局记录与个人最佳；
4. broad `except Exception` 隐藏契约问题；
5. 每帧创建 overlay、panel、shadow、mask、渐变 Surface；
6. 每帧/每次 draw 创建 Button 对象；
7. 固定像素窗口；
8. 系统字体 fallback 不稳定；
9. 无用户可见崩溃页和日志路径；
10. 保存失败退出时没有确认对话框；
11. 没有设置、音频、主题、按键绑定和可访问性服务；
12. `BaseGame` 与 2048 分别维护两套保存状态机。

## 4.3 启动器

1. `main()` 仍接近 400 行；
2. 游戏元数据重复存在于：
   - launcher 本地列表；
   - module map；
   - accent/tag/title map；
   - server `SUPPORTED_GAMES`；
3. `/api/games` 与 `list_games()` 基本成为未使用接口；
4. 固定五卡一行，扩展游戏不稳健；
5. 只能主要依靠鼠标启动游戏；
6. 无键盘焦点与手柄导航；
7. 玩家名仍依赖 `KEYDOWN.unicode`，无完整 IME；
8. 默认语义仍是“在线排行/成绩服务”；
9. 游戏启动异常主要输出终端；
10. 关闭时没有展示未保存记录是否已持久化；
11. 当前 Top 10 对本地单用户并不比“个人最佳 + 最近游玩”更有价值；
12. 同名 `guest` 的多条记录可能占满本机榜单。

## 4.4 Tetris

本轮两个 P0 已修复。

剩余属于规则和体验：

1. 当前是自定义辅助旋转，必须继续明确不是标准 SRS；
2. `random.choice` 可能长时间缺块；
3. 可选增加 7-bag；
4. 无 lock delay；
5. 无 ghost；
6. 无 hold；
7. 同义键分别按下时会各产生一次立即移动，需决定是否符合手感；
8. RNG 不可注入；
9. 静态网格和侧栏可缓存；
10. 计分、旋转和掉落规则应在游戏内帮助页可查看。

这些不是“必须竞技化”。可作为单机舒适模式选项。

## 4.5 Snake

双转向队列和长帧策略修复有效。

剩余：

1. 一帧中吃到食物升级后，剩余 catch-up 仍使用该帧开始时的旧 interval；
2. RNG 不可注入；
3. 棋盘底纹每帧重画；
4. 无个人最佳持久化；
5. 可增加速度选择、穿墙、障碍和双人同屏；
6. 食物和蛇可以增加形状/纹理，避免只靠颜色辨识。

## 4.6 2048

这是当前最需要继续整理的游戏模块。

问题：

1. 无效果方向导致迟到输入；
2. 胜利 overlay 前的队列可能残留；
3. 自己维护一套独立成绩 Future/队列，与 BaseGame 重复；
4. reset 时只“脱离”一个排队分数，语义复杂；
5. `_detach_queued_score_submission()` 重新发起保存但不绑定 UI；
6. 无本地最高分；
7. 无保存/继续；
8. 无撤销；
9. R 直接重开，容易误触；
10. RNG 不可注入；
11. 静态棋盘背景每帧重画。

## 4.7 Sokoban

核心计分修复已经到位。

剩余：

1. 当前服务端只保留最佳完整闯关，无法保留每次 attempt；
2. parser 不验证地图闭合；
3. 不验证玩家可达区域；
4. 不验证静态死角；
5. `extra.level` 使用零基，而 UI 使用一基；
6. `1000 - moves` 不考虑关卡难度和推动次数；
7. 更适合本地记录的指标：
   - 最少移动；
   - 最少推动；
   - 是否使用撤销；
   - 星级；
8. 无死锁检测；
9. 无提示；
10. 无关卡选择和解锁；
11. 无关卡编辑器；
12. 每关调整窗口尺寸，体验不稳定。

## 4.8 Zuma

上一轮核心反应问题修复较好。

剩余：

1. 复杂重叠 reaction 仍缺属性测试；
2. reaction 最好升级为显式 FSM；
3. `incoming.pop(0)` 可换 deque，优先级低；
4. 逻辑、渲染和粒子仍集中于大类；
5. 每帧创建多个透明 Surface；
6. RNG 不可注入；
7. 无训练和关卡选择；
8. 无色弱符号；
9. 完整通关、失败和单关练习应使用不同本地记录语义；
10. 增加道具和轨道前，先冻结 reaction 规则。

## 4.9 服务端与数据库

1. `scores` 混合 attempt 与 best；
2. request id 未绑定完整 payload；
3. 幂等记录可能悬空；
4. score request 表无清理；
5. import 写文件；
6. 无 schema version；
7. 无正式 migration；
8. 无自动备份/恢复；
9. 数据库默认写在仓库目录；
10. `events` 表仍未使用；
11. LAN 暴露没有认证，而本项目其实不需要 LAN 提交；
12. `SUPPORTED_GAMES` 与客户端重复；
13. 对本地单机项目，HTTP 层本身已成为主要故障面。

## 4.10 测试

### 现有优点

- pygame 测试进程隔离；
- 默认数据库污染检查；
- 临时端口；
- 子进程超时；
- 玩法边界覆盖较丰富；
- 新增了保存 ACK、Tetris 输入和服务端单调性测试。

### 剩余问题

1. 单文件约 2,500 行以上；
2. import 即执行；
3. 自定义 runner；
4. 固定 15 秒超时；
5. 无 fixture、marker、参数化；
6. 无 JUnit；
7. 无 coverage；
8. 无 CI；
9. 无失败截图；
10. 大量依赖私有属性；
11. 部分测试固定当前自定义规则，而非独立规格；
12. `time.sleep(0.02)` 掩盖回调竞态；
13. 嵌套子进程缺少自己的 timeout，外层杀死时可能留下子进程；
14. 文档声称 103 项，当前 runner 可数为 95；
15. 随机压力和性能结果没有作为可重复测试提交。

缺失的关键测试：

```text
drain 等待保存回调完成
失败记录跨进程恢复
结算页重试不创建新逻辑保存
全局重试不重复插入
2048 无效果队列继续消费
2048 胜利时清空残留输入
幂等 payload hash 冲突
replace 后旧 request_id 重放
并发同 request_id
低分 update 不刷新最近活动
默认进程内存储无需 Flask
数据库迁移和备份恢复
```

## 4.11 脚本、依赖与仓库治理

1. `run.sh` 仍强制后端成功，不能真正本地无服务启动；
2. `run_launcher.sh`、`run_server.sh`、`run_tests.sh` 不会像 `run.sh` 一样自动创建 Conda 环境；
3. 无 Conda 时没有 Python 版本和依赖检查；
4. Bash-only，不适合 Windows 一键启动；
5. `requirements.txt` 与 `environment.yml` 重复维护；
6. 只有下界，没有锁文件；
7. 无 `pyproject.toml`；
8. 无开发依赖定义；
9. 根目录没有 GitHub Actions 工作流；
10. 无 LICENSE；
11. 无 CHANGELOG；
12. 无 CONTRIBUTING；
13. 无 SECURITY；
14. 无桌面打包配置；
15. README 缺截图/GIF、常见故障、数据备份和平台矩阵；
16. `spec.md`、`task.md` 写“未提交或推送”，但内容已经出现在提交中；
17. `task.md` 的 103 项与当前测试文件不一致；
18. 审查回复、任务书、内部 spec/task 全部放在根目录，影响项目入口清晰度。

建议：

```text
docs/audits/
docs/adr/
docs/development/
```

内部工作记录应移动或归档。

---

## 5. 明确的产品边界

## 5.1 建议继续坚持的非目标

本项目不需要建设：

- 用户注册和登录；
- 云端账号；
- 公网排行榜；
- 赛季；
- 匹配系统；
- 实时联机；
- 反作弊；
- 服务端权威重放；
- 在线商城；
- 默认遥测；
- 强制联网；
- 复杂权限系统。

## 5.2 适合本地小游戏合集的目标

应优先建设：

- 本机可靠成绩；
- 个人最佳；
- 最近游玩；
- 真实游玩次数；
- 关卡进度；
- 未完成局存档；
- 设置和键位；
- 多平台桌面运行；
- 可访问性；
- 音频；
- 本机成就；
- 数据导出与备份；
- 可选家庭成员档案；
- 完全离线挑战和 replay。

---

## 6. 推荐的本地优先目标架构

## 6.1 默认路径

```text
pygame Launcher / Game
        │
        ├── GameEngine
        ├── Renderer / Input
        └── LocalGameService
                │
                └── LocalGameStore
                        └── SQLite（操作系统用户数据目录）
```

## 6.2 可选 Flask 路径

```text
Flask Adapter
    └── LocalGameService
            └── LocalGameStore
```

Flask 不再独立实现数据规则，只复用同一个 service/repository。

## 6.3 推荐接口

```python
class LocalGameStore(Protocol):
    def begin_attempt(self, attempt: GameAttempt) -> None: ...
    def finish_attempt(self, attempt_id: str, result: GameResult) -> None: ...
    def list_recent_attempts(self, limit: int = 20) -> list[GameAttempt]: ...
    def get_personal_best(self, game_id: str, mode: str) -> GameAttempt | None: ...
    def save_progress(self, profile_id: str, game_id: str, payload: dict) -> None: ...
    def load_progress(self, profile_id: str, game_id: str) -> dict | None: ...
    def save_slot(self, profile_id: str, game_id: str, slot: str, payload: dict) -> None: ...
    def load_slot(self, profile_id: str, game_id: str, slot: str) -> dict | None: ...
    def load_settings(self) -> dict: ...
    def save_settings(self, settings: dict) -> None: ...
```

## 6.4 推荐数据表

### `schema_meta`

```text
version
updated_at
```

### `profiles`（可选）

```text
profile_id
display_name
created_at
last_used_at
```

默认只有一个本机档案，不要求登录。

### `attempts`

```text
attempt_id
profile_id
game_id
mode
ruleset_version
status
score
started_at
finished_at
duration_ms
extra_json
```

### `progress`

```text
profile_id
game_id
ruleset_version
progress_json
updated_at
```

### `save_slots`

```text
profile_id
game_id
slot
ruleset_version
state_json
updated_at
```

### `settings`

```text
key
value_json
updated_at
```

### `pending_writes`（仅在异步 writer 必要时）

```text
write_id
payload_json
state
retry_count
created_at
updated_at
```

## 6.5 是否需要后台写线程

不要先假设 SQLite 写入一定会卡顿。

推荐顺序：

1. 使用很短的 SQLite 事务直接写；
2. 在真实环境测量 p95/p99；
3. 若超出帧预算，再引入单一 writer 线程；
4. writer 必须有磁盘级队列和 `flush()`；
5. 不要重新制造仅在内存的 outbox；
6. 所有退出路径必须有明确保存结果。

---

## 7. 完整优化任务清单

### 7.1 优先级定义

- **P0**：可能丢失或重复本地记录、产生错误移动，或让退出保证失真；
- **P1**：本地数据架构、维护性、迁移和正式发行基础；
- **P2**：输入、性能、设置、可访问性和引擎解耦；
- **P3**：内容扩展、打包、展示和长期治理；
- **S/M/L/XL**：相对工作量。

---

## 7.2 P0：关闭当前数据与输入可靠性缺口

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG3-P0-01 | 修复 2048 无效果输入队列 | `_move()->bool`、队列消费循环 | 无效果方向不会阻塞后续方向；后续输入不会迟到到下一局移动 | 无 | M |
| CG3-P0-02 | 清理 2048 状态边界输入 | win/gameover/pause/reset queue policy | 达成 2048、继续、暂停、重置后均无残留方向 | P0-01 | S |
| CG3-P0-03 | 统一保存所有权 | `SaveHandle` / `SaveCoordinator` | 自动与人工重试复用同一逻辑保存 ID；UI 不再创建第二条保存 | 无 | L |
| CG3-P0-04 | 消除双重重试重复记录 | 统一 retry API | 初始失败→结算页重试→启动器重试，数据库始终只有一条逻辑 attempt | P0-03 | M |
| CG3-P0-05 | 持久化失败保存 | Local outbox 或直接 LocalGameStore | 进程关闭、崩溃、重启后失败记录仍存在并可恢复 | P0-03 | L |
| CG3-P0-06 | 修复 drain 完成语义 | 单一 completion callback、completion tracking | drain 返回真时，成功/失败队列和 UI 可观察状态均已完成归档 | P0-03 | M |
| CG3-P0-07 | 可靠退出和重置 | flush/close flow、退出提示 | 有待写成绩时退出：成功落盘或明确提示；不得静默丢失 | P0-05/06 | L |
| CG3-P0-08 | 区分临时和永久失败 | typed save result | 4xx 不无限重试；网络/DB 临时错误可重试；文案准确 | P0-03 | M |
| CG3-P0-09 | 明确本局记录与个人最佳 | result status model | UI 显示“本局已记录/新纪录/未破纪录”，不再把 preserved 误称本局已保存 | 无 | L |
| CG3-P0-10 | 修复幂等完整性 | canonical payload hash、response snapshot | 相同 ID 不同 payload 返回 409；旧 score 行删除后重放仍返回有效语义 | P0-03 | L |
| CG3-P0-11 | 修复低分 no-op 的 recency | no-op update | 低分乱序更新不改变 `updated_at` 和最近记录排序 | 无 | S |
| CG3-P0-12 | P0 回归套件 | 新增确定性测试 | 覆盖 P0-01 至 P0-11；测试不使用 sleep 等待时序 | 全部 P0 | L |

### P0 必须新增的测试

```text
test_2048_noop_command_does_not_stall_queue
test_2048_win_clears_buffered_commands
test_retry_uses_same_logical_save_id
test_overlay_retry_and_launcher_retry_do_not_duplicate
test_failed_save_survives_process_restart
test_drain_waits_for_completion_callback
test_exit_flushes_or_reports_failure
test_permanent_validation_error_is_not_retried
test_saved_copy_distinguishes_attempt_and_personal_best
test_request_id_hash_conflict
test_replay_after_replaced_score_returns_valid_snapshot
test_stale_lower_update_does_not_touch_recent_timestamp
```

---

## 7.3 P1：落实本地优先和工程基础

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG3-P1-01 | 写本地优先 ADR | `docs/adr/001-local-first.md` | 明确默认无 HTTP、Flask 可选及非目标 | P0 稳定 | S |
| CG3-P1-02 | 抽象 LocalGameStore | Protocol + SQLite repository | 五款游戏和启动器不直接依赖 requests/Flask | P1-01 | L |
| CG3-P1-03 | 默认启动改为进程内存储 | 新主入口 | 无 Flask、无端口也能保存成绩和进度 | P1-02 | L |
| CG3-P1-04 | Flask 降为可选适配器 | `classic-games api` 可选命令 | Flask 与桌面端复用同一 repository；停掉 Flask 不影响游戏 | P1-02/03 | L |
| CG3-P1-05 | 使用用户数据目录 | path service | 安装目录只读时可运行；DB、设置、日志和存档位于用户目录 | P1-02 | M |
| CG3-P1-06 | 建立 schema version 和迁移 | migration runner、自动备份 | 可从旧 `scores.db` 升级；失败保留原库；迁移可测试 | P1-05 | L |
| CG3-P1-07 | 建立 attempts 数据模型 | attempts/progress/save_slots/settings | 游玩次数、最近记录、个人最佳和进度语义分离 | P1-02/06 | XL |
| CG3-P1-08 | 迁移旧数据 | legacy importer | 旧分数可导入为历史/最佳；迁移可重复且幂等 | P1-06/07 | L |
| CG3-P1-09 | 重做本地首页数据 | 个人最佳、最近游玩、进度 | 默认不显示“在线/离线”；不需要 Top 10 才能体现成绩 | P1-07 | L |
| CG3-P1-10 | 单一游戏注册表 | `GameDescriptor` | 名称、入口、尺寸、主题、规则版本和模式只有一份来源 | 无 | L |
| CG3-P1-11 | 拆分 launcher | App/State/Renderer/Input/GameRunner | 主循环职责清晰；新增游戏不改多处字典 | P1-10 | L |
| CG3-P1-12 | 状态枚举与转换 | `GameState`、enter/exit hooks | 无散落状态字符串；队列/保存/overlay 的进入退出行为统一 | P0/P1-11 | L |
| CG3-P1-13 | Flask app factory | `create_app(config)` | import 不写磁盘；测试可注入数据库；启动错误可控 | P1-04/06 | M |
| CG3-P1-14 | 清理可选 API 幂等表 | retention、snapshot/FK policy | 不悬空、不无限增长；并发同 ID 行为确定 | P0-10/P1-13 | M |
| CG3-P1-15 | 迁移 pytest | fixtures、markers、parametrize | 任一测试可单独运行；不再 import 即执行 | P0-12 | XL |
| CG3-P1-16 | 建立 CI | GitHub Actions | Linux/Windows/macOS smoke；上传 JUnit、日志和截图 | P1-15 | L |
| CG3-P1-17 | 建立 pyproject 与锁定 | `pyproject.toml`、dev extras、lock | 空环境可复现；不再双份手工维护依赖 | 无 | L |
| CG3-P1-18 | 结构化日志与崩溃页 | rotating log、crash UI | 游戏异常返回菜单；用户可查看日志路径和版本 | P1-05/11 | M |
| CG3-P1-19 | 数据备份与恢复 | export/import、校验 | 可备份成绩、进度、设置和存档；损坏导入不覆盖现有数据 | P1-06/07 | L |
| CG3-P1-20 | 清理仓库文档结构 | `docs/audits`、`docs/adr` | spec/task 状态准确；测试数量自动生成；根目录保持清晰 | 无 | S |
| CG3-P1-21 | 跨平台命令入口 | console scripts | Windows 不依赖 Bash；启动前检查 Python/依赖 | P1-17 | M |
| CG3-P1-22 | 完整开发文档 | architecture、data、testing、troubleshooting | 新贡献者从空环境能安装、测试、运行和恢复数据 | P1-01~21 | M |

---

## 7.4 P2：输入、体验、性能与可访问性

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG3-P2-01 | 统一 InputManager | action map、物理键状态、队列 | 所有游戏不再散落处理 keycode；失焦/暂停统一清理 | P1-12 | L |
| CG3-P2-02 | 正确支持 IME | TEXTINPUT/TEXTEDITING 控件 | 中文、日文、韩文组合输入可用 | P2-01 | M |
| CG3-P2-03 | 键位重映射 | settings + binding UI | 冲突可检测；默认键可恢复 | P2-01 | L |
| CG3-P2-04 | 手柄与键盘启动器导航 | focus/navigation layer | 不用鼠标可完成选游戏、暂停、重试和返回 | P2-01 | L |
| CG3-P2-05 | 本地设置系统 | 窗口、音量、按键、辅助项 | 原子保存、版本化、损坏安全恢复 | P1-07 | L |
| CG3-P2-06 | 音频系统 | BGM/SFX、分组音量 | 无音频设备时不崩；暂停/切换生命周期正确 | P2-05 | L |
| CG3-P2-07 | 逻辑分辨率与缩放 | resizable、letterbox、DPI | 常见窗口和高 DPI 不裁切、不模糊 | P1-11 | XL |
| CG3-P2-08 | 字体与资源管理 | licensed fallback、asset manager | 缺系统 CJK 字体仍可显示；缺资源有降级 | P2-07 | M |
| CG3-P2-09 | 可访问性 | 色弱符号、高对比度、降低动态 | 颜色不是唯一信息通道；脉冲/抖动可关闭 | P2-05/08 | L |
| CG3-P2-10 | 统一 Clock 与 RNG | injectable clock/random | 相同 seed+输入可重现 Bug；暂停不推进逻辑时间 | P1-12 | L |
| CG3-P2-11 | 渐进抽取纯规则 Engine | 五款规则模块 | 核心逻辑测试无需 SDL；不要求一次性重写 UI | P2-10 | XL |
| CG3-P2-12 | 静态 Surface 缓存 | gradient/grid/panel cache | 只在 profiler 证明有收益后实施；有失效规则 | P2-07 | L |
| CG3-P2-13 | Zuma 反应 FSM | explicit reaction state | 多个重叠反应确定、可重放、可属性测试 | P2-11 | L |
| CG3-P2-14 | 性能基准 | repeatable benchmark | 记录机器/系统/分辨率；结果可在 CI 或本机重复 | P2-11/12 | M |
| CG3-P2-15 | 长时间稳定性 | soak test | 切换游戏 100 次、运行 30–60 分钟无持续资源增长 | P1-18/P2-14 | M |
| CG3-P2-16 | 存档槽 | 2048 等 save slots | 中断后恢复；规则版本不兼容时给出提示 | P1-07/P2-10 | L |
| CG3-P2-17 | 本地导入导出 UI | backup screen | 用户不接触数据库文件也能备份恢复 | P1-19 | M |

---

## 7.5 P3：适合单机合集的内容与发行完善

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG3-P3-01 | Tetris 舒适模式 | 7-bag、ghost、hold、lock delay 可选 | 保留当前自定义模式；按模式记录个人最佳 | P2-10/11 | XL |
| CG3-P3-02 | Snake 模式 | 速度、穿墙、障碍、双人同屏 | 每种模式规则和最佳记录分离 | P2-11 | L |
| CG3-P3-03 | 2048 完整本地体验 | 最高分、撤销、保存、棋盘尺寸 | 撤销和存档规则明确；attempt 不重复 | P2-16 | L |
| CG3-P3-04 | Sokoban 关卡进度 | 选关、解锁、星级、最佳移动/推动 | 练习和顺序闯关语义清晰 | P1-07 | L |
| CG3-P3-05 | Sokoban 死锁与提示 | 静态死角、基础提示 | 提示可关闭；明确是否影响星级 | P2-11 | XL |
| CG3-P3-06 | Sokoban 编辑器 | XSB 导入导出与验证 | 拒绝非法、未闭合、明显不可达地图 | P3-04/05 | L |
| CG3-P3-07 | Zuma 训练和选关 | 训练、速度辅助、颜色符号 | 训练记录不混入完整通关 | P2-09/13 | L |
| CG3-P3-08 | Zuma 原创扩展 | 道具球、轨道和目标 | 每项有确定性测试；不破坏经典模式 | P2-13 | XL |
| CG3-P3-09 | Zuma 轨道编辑器 | 可视化路径编辑 | 轨迹合法、可版本化、可预览 | P3-08 | L |
| CG3-P3-10 | 本机档案 | 可选 profiles | 默认单档案；无需账号；家庭成员可分开记录 | P1-07/09 | M |
| CG3-P3-11 | 本机成就与统计 | achievements、history | 重放和导入不重复触发；无遥测依赖 | P1-07 | L |
| CG3-P3-12 | 离线每日挑战 | 日期 seed、规则版本 | 完全离线；同版本同 seed 可重现 | P2-10/11 | L |
| CG3-P3-13 | 本地 replay | command log + viewer | 用于复盘和调试，不用于反作弊 | P2-10/11 | L |
| CG3-P3-14 | 本地化 | 中文/英文资源 | 主要 UI 无硬编码；长文本布局通过 | P2-07/08 | L |
| CG3-P3-15 | 桌面打包 | Windows/macOS/Linux | 无 Python 环境可运行；升级不丢数据 | P1/P2 完成后 | XL |
| CG3-P3-16 | 自动发布 | tag workflow、checksums | 构建经过 smoke test；失败不发布 | P1-16/P3-15 | L |
| CG3-P3-17 | 许可证与素材清单 | LICENSE、NOTICE | 代码、字体、音效和图形来源明确 | 无 | M |
| CG3-P3-18 | 项目展示 | 截图、GIF、功能矩阵 | README 首屏能说明玩法和本地优先特点 | P3-14/15 | M |
| CG3-P3-19 | 社区文件 | CONTRIBUTING、SECURITY、Issue 模板 | Bug 报告包含版本、日志和复现步骤 | P1-22 | S |
| CG3-P3-20 | 谨慎增加新游戏 | registry 插件模板 | 每款新游戏同时交付规则、存储、输入、暂停和测试 | P1-10/P2-11 | XL |

### 推荐新增游戏方向

更适合本地合集且不依赖商业角色素材：

- 扫雷；
- 打砖块；
- 双人同屏球拍；
- 四子棋；
- 数独；
- 华容道；
- 记忆翻牌；
- 连连看；
- 原创纵向射击；
- 弹球；
- 五子棋；
- 迷宫。

不要把游戏数量作为主要质量指标。每增加一款，必须同时提供：

- 确定性规则测试；
- 个人最佳或进度语义；
- 暂停、失焦、返回行为；
- 键盘可操作；
- 可访问性；
- 资源许可说明；
- 至少一个长帧/边界测试。

---

## 8. 建议测试体系

## 8.1 保存与数据测试

- 初次失败、自动重试、人工重试始终只有一个逻辑保存；
- 退出并重启后恢复 pending；
- 成功回调完成前 drain 不得返回；
- 永久错误不进入无限重试；
- 本局低于最佳时仍形成 attempt；
- personal best 正确聚合；
- schema migration 幂等；
- 迁移失败保留原 DB；
- 用户数据目录只读时给出明确错误；
- 导入损坏文件不覆盖现有数据。

## 8.2 游戏属性测试

### Tetris

- 任何锁定格都在边界内；
- 方块 generation 变化后 accumulator 不再作用；
- 同义键独立；
- 左右最近按下优先；
- 若加入 7-bag，每袋包含七种块；
- 旋转模式按独立规格验证。

### Snake

- 队列不接受 180°；
- 两次快速合法转向按序执行；
- 长停顿只执行规则允许的步数；
- 身体长度只在吃食物时增加；
- 食物永不生成在身体上。

### 2048

- 每个 tile 每步最多合并一次；
- 合并前后总值守恒；
- 新生成方块只增加 2 或 4；
- 无效果队列不阻塞；
- 胜利/暂停/重置清空输入；
- 保存、恢复和撤销保持棋盘一致。

### Sokoban

- 同关重复完成不重复累计；
- 练习跳关永不算完整闯关；
- parser 闭合、可达、箱目标数量；
- 箱子不会离开 floor；
- 死角检测；
- 每关最佳移动和推动更新正确。

### Zuma

- 多个 pending reaction 不丢失；
- 重叠反应顺序确定；
- chain 位置单调；
- 长帧计时不丢余量；
- 临界弹丸救场；
- 反应状态可 replay。

## 8.3 UI/E2E

- 启动器完全键盘操作；
- IME；
- 手柄；
- 高 DPI；
- 小窗口；
- 无音频设备；
- 色弱模式；
- 减少动态效果；
- 保存失败提示；
- 退出未保存提示；
- 崩溃返回菜单；
- 游戏切换 100 次；
- 打包产物首次启动；
- 旧数据库升级。

## 8.4 测试工具门禁

- pytest；
- 可配置 timeout；
- JUnit；
- coverage；
- 失败截图；
- 日志产物；
- 属性测试；
- 可选 mutation testing；
- 测试完成后检查工作区和用户数据目录未污染；
- 测试数量由工具自动统计，不手写到 `task.md`；
- 所有性能和随机压力测试必须可重复、带 seed 并提交到仓库。

---

## 9. 性能与稳定性门槛

必须记录基准机器、操作系统、Python/打包版本、窗口尺寸。

1. 正常游戏目标 60 FPS；
2. 指定基准机 p95 frame time ≤ 16.7 ms；
3. 本地保存 p95 不产生可感知长帧；
4. 如使用 writer，退出前可靠 flush；
5. Zuma 最大链场景满足帧预算；
6. 2048 输入队列在不同帧率下顺序一致；
7. Tetris 系统卡顿后不跨方块补步；
8. Snake 系统卡顿后不回放不可见路径；
9. 切换游戏 100 次：
   - 线程数回到基线；
   - 文件描述符不增长；
   - Surface/内存达到平台；
10. 30–60 分钟自动游玩：
    - pending write 最终清空；
    - 无未处理 SQLite lock；
    - 无持续内存增长；
11. 数据库损坏、只读和磁盘空间错误：
    - 不崩溃；
    - 不覆盖原文件；
    - 有用户可见恢复入口。

---

## 10. 稳定版本质量门禁

第一个正式稳定版本建议至少满足：

- 全部 P0 关闭；
- 默认启动不依赖 Flask/HTTP；
- 离线成绩可跨进程持久保存；
- 自动与人工重试不重复成绩；
- 2048 无迟到输入；
- attempts、personal best、recent、progress 语义分离；
- 数据位于操作系统用户目录；
- schema version、迁移和备份齐全；
- pytest + CI 可从空环境执行；
- 测试不会触碰真实用户数据；
- 测试数量和性能结果可自动复现；
- Windows、macOS、Linux 至少 smoke；
- 纯规则模块建议：
  - 行覆盖率 ≥ 90%；
  - 分支覆盖率 ≥ 85%；
- 全项目建议行覆盖率 ≥ 80%；
- formatter、lint、typing、依赖审计通过；
- README、LICENSE、CHANGELOG、数据位置和故障排查完整；
- 默认不联网；
- 默认不上传遥测。

覆盖率不能替代规则规格、属性测试和真实玩家测试。

---

## 11. 推荐实施顺序

### M0：关闭新发现的可靠性问题

优先完成：

- 2048 队列；
- 保存所有权；
- 双重重试；
- 持久失败记录；
- drain；
- 退出保存；
- typed result；
- 幂等 payload；
- P0 回归。

### M1：真正本地优先

- LocalGameStore；
- 默认无 Flask；
- 用户数据目录；
- attempts/progress/save slots/settings；
- 旧数据库迁移；
- 本地首页；
- Flask 可选适配器。

这是本项目投入产出比最高的阶段。

### M2：工程可持续

- registry；
- launcher 拆分；
- state enum；
- pytest；
- CI；
- pyproject；
- 锁文件；
- 日志；
- 文档整理；
- 跨平台入口。

### M3：体验和性能

- InputManager；
- IME；
- 键位；
- 手柄；
- 音频；
- 响应式窗口；
- 字体；
- 可访问性；
- Clock/RNG；
- 渐进式规则引擎；
- 存档；
- benchmark/soak。

### M4：内容和发行

- 五款游戏的单机舒适性功能；
- 推箱子与祖玛编辑器；
- 本机档案与成就；
- 离线挑战和 replay；
- 本地化；
- 桌面包；
- 许可证和展示材料；
- 再谨慎增加新游戏。

---

## 12. 最终判断

本轮修复整体是成功的。

与上一版相比：

- 玩法漏洞明显减少；
- 保存状态更透明；
- Tetris 输入模型更正确；
- 脚本配置与测试隔离更可靠；
- 服务端校验和错误响应更成熟；
- 回归意识继续增强。

但当前代码已经走到一个架构分岔点：

### 路线 A：继续围绕本机 HTTP 增加重试、幂等、队列和生命周期

这会继续增加：

- Future；
- request ID；
- outbox；
- health；
- backoff；
- 端口；
- Flask 生命周期；
- 客户端与服务端双状态机。

### 路线 B：默认直接使用本地仓储，Flask 仅作为可选适配器

这更符合项目本质，也能一次性减少大量故障面。

推荐路线 B。

项目下一阶段的核心目标应是：

> **即使断网、后端未启动、程序崩溃或系统重启，玩家的本机成绩、关卡进度、设置和存档仍然可靠；同时不引入账号、公网竞技和云平台复杂度。**

完成 P0 与 P1 后，这个仓库将从“经过多轮修复的 AI 辅助小游戏合集”升级为“数据可靠、结构清晰、可跨平台发行的本地经典小游戏产品”。
