# Classic Games Hub 二次代码审查与本地优先优化任务书

> 审查日期：2026-08-23
> 审查基线：`main` 分支，commit `9f9ef1ddb575181eb94c4cbf12eb39eb75e622bf`（`9f9ef1d`）
> 对比基线：上一轮审查时的 commit `f12a69b1e123ec1a20a42899c88a6d37d674afa8`
> 产品定位：**本地运行、单机为主、可离线使用的经典小游戏合集**
> 审查范围：`client/`、`server/`、`tests/`、启动脚本、依赖与 README

---

## 0. 执行摘要

本轮修复是有效的，而且不是简单地“绕过测试”。上次审查中最严重的几项游戏规则问题已经得到实质修复：

- 推箱子同一关反复通关后重复累计总分：已修复；
- 推箱子跳到最后一关即可伪造完整通关：已修复；
- 祖玛只有一个待处理连锁、多个反应相互覆盖：已改为队列式处理；
- 主线程同步 HTTP 导致 pygame 卡顿：主要请求已异步化；
- 2048 首次提交失败后永久锁死：永久锁死已消除；
- 俄罗斯方块“标准 SRS”描述与自定义规则冲突：文档已改为诚实的自定义辅助旋转；
- 贪吃蛇只有一个待转向方向：已改为双指令队列；
- 服务端分数、昵称、扩展字段和 JSON 校验：明显加强；
- 祖玛轨迹查询、计时余量和临界救场顺序：均有针对性修正；
- 回归子进程没有超时：已补上超时。

但是，**异步化引入了一组新的结果确认与生命周期问题**。当前代码已经能做到“请求时不明显卡住”，但还不能稳定保证“本地成绩一定成功保存”。同时，启动脚本与测试隔离出现了两个确定性缺陷：

1. `run.sh` 使用自定义 `GAMES_PORT` 时，服务端和客户端可能连接不同端口；
2. `run_tests.sh` 没有把临时 `GAMES_DB` 传给直接导入 `server.app` 的测试进程，测试可能创建或修改仓库默认的 `data/scores.db`。

此外，我实际复现了两个俄罗斯方块输入/计时边界问题：

- 同时按住同义按键（例如 `←` 和 `A`），松开其中一个会错误停止另一个；
- 一次大 `dt` 的软降补步可以在旧方块锁定后继续作用于刚生成的新方块，使新块瞬间下落多行。

因此，本轮结论是：

> **核心玩法修复质量较好，但成绩持久化闭环、测试隔离、启动配置和输入时间步仍有发布阻断问题。**

本任务书已经按用户明确的产品方向重新调整：**不建议把项目发展成联网竞技平台**。推荐将当前 Flask 服务降为可选的教学/API 适配层，默认运行路径改为进程内本地 SQLite 仓储；界面从“在线排行榜”转为“个人最佳、最近游玩、关卡进度和本机档案”。

---

## 1. 审查方法与证据等级

### 1.1 已完成的检查

- 阅读当前 `main` 分支全部 Python 生产代码、回归测试、脚本和文档；
- 对照上一轮问题逐项检查修复路径；
- 执行 `python -m compileall`，当前 Python 源码通过语法编译；
- 统计核心模块规模和复杂度热点；
- 使用最小 pygame 替身对象执行针对性逻辑复现：
  - 俄罗斯方块同义物理按键释放错误；
  - 俄罗斯方块大 `dt` 补步跨越方块生命周期。

### 1.2 当前规模

约数如下：

| 模块 | 规模 |
|---|---:|
| `client/common/network.py` | 154 行 |
| `client/common/ui.py` | 约 692 行 |
| `client/launcher.py` | 约 549 行 |
| `client/games/tetris.py` | 约 556 行 |
| `client/games/snake.py` | 约 261 行 |
| `client/games/game_2048.py` | 约 550 行 |
| `client/games/sokoban.py` | 约 597 行 |
| `client/games/zuma.py` | 约 915 行 |
| `server/app.py` | 约 361 行 |
| `tests/regression.py` | 约 2,413 行 |

结构热点：

- `client/launcher.py::main` 约 365 行，网络、布局、输入、渲染、游戏生命周期集中；
- `server/app.py::submit_score` 约 130 行，参数校验、更新、替换、排名和响应集中；
- `client/games/zuma.py::draw` 仍接近 180 行；
- 测试仍是一个超过 2,400 行的导入即执行脚本。

### 1.3 证据等级

| 标记 | 含义 |
|---|---|
| **已复现** | 已通过可执行最小场景重现 |
| **代码路径确定** | 从控制流可以确定会发生，不依赖猜测 |
| **基本到位** | 主要故障已修，但仍有边界或生命周期未闭环 |
| **部分到位** | 修正了原始症状，但未形成完整可靠机制 |
| **待基准验证** | 代码存在明显热点，仍需真实 pygame/跨平台实测 |
| **产品决策** | 不一定是 Bug，需要按本地单机产品定位确定规则 |

### 1.4 环境限制

当前审查环境没有安装 `pygame` 和 `Flask`，因此未能完整运行仓库的原生 pygame/后端回归套件，也没有完成 Windows、macOS、Linux 真机渲染与打包测试。

所以：

- 语法检查已实际完成；
- 两个俄罗斯方块问题已实际复现；
- 推箱子、2048、祖玛、网络、服务端和脚本结论来自明确代码路径；
- 帧率、Surface 分配和跨平台字体等结论属于高概率热点，需在真实环境测量；
- 合并后必须在项目声明的 Python 3.11 环境完整执行测试。

---

## 2. 上一轮问题修复验收矩阵

| 上轮问题 | 当前状态 | 本轮判断 |
|---|---|---|
| 推箱子重玩同关重复累计 | **到位** | 使用 `level_scores` 逐关保存本次闯关最佳值，总分由账本求和，不再无条件追加 |
| 推箱子跳关后伪造完整通关 | **到位** | 增加 `practice_mode`、`completed_levels`，只有完成全部关卡且未进入练习模式才算完整通关 |
| 2048 首次失败后永久不再提交 | **部分到位** | 失败不再设置永久已提交；但没有自动重试、持久待写和退出/重置前的可靠落盘 |
| 祖玛多个 pending 连锁互相覆盖 | **基本到位** | 改为 `pending_chain_matches` 列表，并使用球对象边界；仍建议以状态机/属性测试验证复杂重叠反应 |
| Flask CLI 新数据库不初始化 | **功能到位，结构未到位** | import 时执行初始化使 CLI 可用；但引入导入副作用、测试配置困难和迁移竞争风险 |
| pygame 主线程同步 HTTP | **基本到位** | 使用线程池和 Future；渲染不再直接等待 HTTP，但多数提交结果未被消费，生命周期未闭环 |
| API 接受字符串/浮点分数等 | **基本到位** | 严格整数、排除 bool，限制 extra，规范 JSON，校验玩家名和未知字段 |
| Tetris 声称标准 SRS | **到位** | 已改为 “SRS-inspired assisted rotation”，不再误称标准规则 |
| Tetris 按住下不能持续软降 | **基础功能到位** | 已支持持续软降；但同义键释放和大 `dt` 跨方块问题仍存在 |
| Snake 连续转向丢失 | **到位** | 使用长度为 2 的转向队列，主要场景已解决 |
| 2048 无效方向静默进入错误分支 | **到位** | 无效方向现在抛出 `ValueError` |
| Sokoban parser 校验不足 | **部分到位** | 已检查空图、非矩形、未知字符、多玩家和箱目标数量；仍缺包围、可达性和静态死角验证 |
| Zuma `pos_at` 线性扫描 | **到位** | 已改为 `bisect` 路径查找 |
| Zuma 长帧丢失计时余量 | **到位** | spawn/cooldown 改为保留余量的循环处理 |
| Zuma 临界帧先判负再处理救场弹丸 | **到位** | 弹丸处理顺序已提前 |
| 回归子进程无超时 | **到位** | 增加 15 秒超时；后续应改为可配置并接入 pytest |

### 对上一轮“联网排行榜”建议的修正

上一轮提出过服务端验证、反作弊、在线身份等方向。根据当前用户明确的产品边界，这些内容不应继续作为目标。

本轮保留的只有：

- `attempt_id` / `session_id`：用于本地写入幂等、历史记录和崩溃恢复；
- `ruleset_version`：用于避免规则升级后把不可比较的本地成绩混在一起；
- 可选本机档案：用于家庭成员或多个本地昵称区分进度。

明确不建议建设：

- 登录账号；
- 云端强依赖；
- 公网排行榜；
- 匹配系统；
- 服务端权威判定；
- 反作弊与 replay 审核；
- 在线赛季；
- 默认遥测。

---

## 3. 当前发布阻断问题与新增问题

## 3.1 CG2-F01：异步提交 Future 没有形成结果确认闭环

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `client/common/ui.py:393`
  - `client/common/ui.py:464-478`
  - `client/common/ui.py:480-496`

`BaseGame._submit_result_score()` 把异步结果存入 `_score_submit_future`，但 `BaseGame.run()`、`update_overlay()` 和结算层绘制都没有统一轮询这个 Future。

因此多数游戏当前只保证：

> 请求已排入线程池。

但没有保证：

> 请求成功、失败已知、失败已重试、退出前已落盘。

### 影响

- 服务端刚好重启、短暂繁忙或本机请求超时时，本局成绩静默丢失；
- UI 不知道成绩处于“保存中、已保存、保存失败”中的哪种状态；
- 线程中出现异常也不会被统一记录；
- 玩家按 ESC 返回菜单时，仍在执行的提交没有可靠收尾。

### 修复要求

建立统一 `ScoreSaveController`：

```text
idle
  → saving
  → saved

saving → retry_pending
saving → failed
retry_pending → saving
```

本地优先架构下，建议把状态文案改为：

- 正在保存；
- 已保存到本机；
- 保存失败，点击重试。

不要再使用“在线/离线”作为核心状态。

---

## 3.2 CG2-F02：提交与排行榜刷新存在竞态，结算页可能一直显示旧成绩

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：
  - `client/common/ui.py:480-496`
  - `client/common/ui.py:624-654`
  - `client/common/network.py:38-43`

进入结算状态时，代码先清空排行榜缓存，再异步提交成绩。下一次绘制结算层时又立即异步读取排行榜。线程池有两个 worker，因此 POST 和 GET 可以并发：

```text
POST 提交成绩 ──────────────── 完成
GET 排行榜 ─── 完成（读到旧数据）
```

通用 `BaseGame` 没有在 POST 成功后再次失效排行榜缓存，所以旧榜可能一直显示到玩家离开该结算层。

2048 自己在成功时会 `invalidate_overlay_leaderboard()`，但其他游戏没有统一处理。

### 修复要求

- 结算页先显示本地结果；
- 保存成功后再触发一次明确刷新；
- 或直接把已确认结果乐观合并到本地“个人最佳/最近记录”，无需立即读回整个榜；
- Future 被替换前应取消或忽略其过期结果；
- 使用 request generation/token，防止旧请求覆盖新状态。

---

## 3.3 CG2-F03：推箱子在服务端确认前标记“已提交”

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`client/games/sokoban.py:422-426`

完整通关时，`_submitted_total` 在调用 `on_win()` 前就被设置。如果异步保存失败，本次对象内仍认为该总分已经提交。

### 影响

- 瞬时故障后没有自动重试；
- 若玩家继续留在对象中，逻辑状态和真实存储状态不一致；
- `_submitted_total` 实际表达的是“已发起”，名字却表达“已确认”。

### 修复要求

拆分：

```text
pending_total
confirmed_total
last_save_error
```

只有本地仓储或可选服务明确 ACK 后，才能更新 `confirmed_total`。

---

## 3.4 CG2-F04：2048 不再永久锁死，但仍没有自动可靠重试

- **优先级**：P0
- **证据**：代码路径确定
- **位置**：`client/games/game_2048.py:334-393`

当前修复的优点：

- 失败不会把 `score_submitted` 设为真；
- milestone 请求尚未完成时，最终分可以进入单槽队列；
- 成功后可以利用已有记录 ID 更新。

仍存在的问题：

1. Future 失败后，只是记录失败，没有退避后自动重试；
2. 轮询只在该游戏对象继续执行 `update()` 或 `update_overlay()` 时发生；
3. 玩家退出到启动器、关闭窗口或立即重置时，未完成结果和排队分数可能丢失；
4. `_pending_state` 定义后未形成实际状态机；
5. `_record_score_submission()` 接受任意 truthy 结果，不严格要求 `ok is True`；
6. `result["id"]` 非法时，`int()` 可能抛异常；
7. 只有一个后续方向缓冲，动画期间第三个快速输入会覆盖第二个。

### 修复要求

- 统一交给全局本地保存协调器，而不是让 2048 自己维护半套网络状态机；
- 一个游戏会话使用稳定 `attempt_id`；
- milestone 和 final 都更新同一条本地 attempt；
- 退出/重置前执行可靠 flush，失败时给出用户可见提示；
- malformed 结果必须安全降级；
- 输入缓冲使用长度有限的队列，并按实际游戏手感决定长度 2 或 3。

---

## 3.5 CG2-F05：`run.sh` 自定义端口时客户端连接错误端口

- **优先级**：P0
- **证据**：代码路径确定

`run.sh` 使用 `GAMES_PORT` 启动和健康检查服务，但 `BackendClient` 的默认地址来自：

```python
GAMES_API_URL or "http://127.0.0.1:5000"
```

脚本没有根据自定义端口设置 `GAMES_API_URL`。因此：

```bash
GAMES_PORT=5010 ./run.sh
```

可能出现：

- 后端在 5010；
- 健康检查访问 5010；
- 客户端仍访问 5000。

`GAMES_HOST=0.0.0.0` 也不适合作为客户端连接地址；监听地址与连接地址应分离。

### 修复要求

当前架构下至少设置：

```bash
export GAMES_API_URL="http://127.0.0.1:${GAMES_PORT}"
```

更符合本地项目定位的长期方案是：默认运行不再需要端口和 HTTP，Flask 仅作为可选演示入口。

---

## 3.6 CG2-F06：测试进程可能污染默认成绩数据库

- **优先级**：P0
- **证据**：代码路径确定

`run_tests.sh` 为后台 Flask 进程设置了临时 `GAMES_DB`，但运行 `tests/regression.py` 的主测试进程没有继承该临时数据库路径。

回归测试后部存在直接导入 `server.app` 并向测试客户端提交分数的场景。由于 `server.app` 在 import 时执行数据库初始化，该进程会使用默认路径，并可能创建或修改：

```text
data/scores.db
```

这与 README 中“测试使用独立数据库、不污染真实排行”的目标相冲突。

### 修复要求

短期：

```bash
export GAMES_DB="${TEST_RUNTIME_DIR}/direct-import.db"
```

并让回归进程继承。

长期：

- 所有测试通过 `create_app(test_config)` 注入数据库；
- 禁止 import 时创建文件；
- CI 增加断言：测试前后仓库工作区无新建或变化的 `data/scores.db`；
- 测试进程设置只读仓库目录或以临时工作目录运行。

---

## 3.7 CG2-F07：俄罗斯方块同义物理按键释放错误

- **优先级**：P0
- **证据**：已复现
- **位置**：`client/games/tetris.py:180-182`、`390-426`

当前 `horizontal_pressed` 保存的是方向 `-1/+1`，不是实际物理按键。

复现场景：

1. 按住 `←`；
2. 再按住 `A`；
3. 两个键都代表向左；
4. 松开 `A`，但 `←` 仍物理按下；
5. 代码删除所有 `-1`，水平持续移动错误停止。

`Down` 与 `S` 也有同类问题：松开任一键都会把 `soft_drop_held=False`。

### 修复要求

按物理键记账：

```python
pressed_keys: set[int]
```

然后每帧或每次事件后推导动作状态：

```python
left_active = K_LEFT in pressed_keys or K_a in pressed_keys
right_active = K_RIGHT in pressed_keys or K_d in pressed_keys
soft_drop_active = K_DOWN in pressed_keys or K_s in pressed_keys
```

还要明确左右同时按下时采用：

- 最近按下优先；
- 中立；
- 或当前方向保持。

建议采用“最近按下优先，松开后恢复仍按住的另一侧”。

---

## 3.8 CG2-F08：俄罗斯方块大 `dt` 补步跨越新旧方块生命周期

- **优先级**：P0
- **证据**：已复现
- **位置**：`client/games/tetris.py` 的软降和重力 catch-up 循环

复现场景：

- 当前方块已经位于底部；
- 玩家按住软降；
- 本帧 `dt=0.5`；
- 第一次 `_soft_drop()` 锁定旧方块并生成新方块；
- 循环仍继续消费剩余计时；
- 后续迭代作用于刚生成的新方块，使其同一帧下落多行。

重力 catch-up 也可能产生同类问题。

### 修复要求

为活动方块增加 `piece_generation` 或对象标识：

```python
generation_before = self.piece_generation
while accumulator >= interval:
    step()
    if self.piece_generation != generation_before:
        accumulator = 0
        break
```

同时：

- 对异常大 `dt` 设上限；
- 窗口失焦后自动暂停并清空累计器；
- 明确锁定后新块的重力起点；
- 最好使用固定时间步或带上限的 accumulator。

---

## 3.9 CG2-F09：线程池、Session 和 Future 没有明确关闭

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：`client/common/network.py`

`BackendClient` 懒创建 `ThreadPoolExecutor` 和线程局部 `requests.Session`，但没有：

- `close()`；
- context manager；
- 启动器退出时 `shutdown()`；
- 子游戏返回时的提交 drain；
- 对尚未完成 Future 的取消或收尾策略。

### 影响

- 解释器退出可能等待非守护线程；
- 多次创建客户端可能留下线程；
- Session 生命周期不可控；
- 测试无法可靠断言资源释放。

### 修复要求

```python
def close(self):
    self._executor.shutdown(wait=True, cancel_futures=False)
```

但不能只机械关闭：应先完成或持久化待写成绩，再关闭 worker。

本地优先重构后，网络 worker 仅供可选 Flask 适配器使用。

---

## 3.10 CG2-F10：全局 backoff 在并发线程中语义过粗

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：`client/common/network.py:27`、`45-52`

所有端点共享 `_offline_until`，并由多个 worker 线程读写：

- 健康检查失败可在两秒内阻止分数提交；
- 一个并发成功请求又会清空另一个失败刚设置的 backoff；
- 没有锁，也没有按端点/错误类型区分；
- 4xx 契约错误和网络不可用的反馈仍比较模糊。

对于本地项目，这一复杂度主要来自不必要的进程间 HTTP。

---

## 3.11 CG2-F11：服务端更新可把已有 2048 高分覆盖为低分

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：`server/app.py:225-231`

只要 `submission_id/game_id/player` 匹配，更新会直接写入新分数，没有保证单调不下降。

在当前客户端串行逻辑下通常不会主动发送更低分，但以下情况仍可能发生：

- 乱序重试；
- 旧客户端；
- 手工 API 调用；
- 恢复逻辑错误。

本地优先模型更合适的设计是：

- 一次 attempt 保存完整最终结果；
- `personal_best` 由查询或独立聚合得出；
- 若允许同一次 2048 会话持续更新，则使用 `MAX(existing, incoming)`，并保留最终状态。

---

## 3.12 CG2-F12：`plays` 仍然不是实际游玩次数

- **优先级**：P1
- **证据**：代码路径确定
- **位置**：`server/app.py` 的 stats 查询

`plays=COUNT(*)` 统计的是当前 `scores` 行数：

- 2048 会更新已有行；
- 推箱子可能按玩家替换或保留旧最佳；
- 其他游戏可以让同一玩家保留多条记录。

所以它既不是：

- 真正启动次数；
- 完成局数；
- 每位玩家数量；
- 也不是个人最佳数量。

本地项目应把数据拆成：

```text
attempts       # 每次实际游玩/结算
personal_best  # 可查询或缓存
progress       # 关卡和解锁
save_slots     # 未完成局
```

界面中使用“游玩次数、完成次数、个人最佳、最近记录”这些准确名称。

---

## 3.13 CG2-F13：服务端异常路径仍可能返回 Flask HTML 500

- **优先级**：P1
- **证据**：代码路径确定

健康检查捕获了 SQLite 错误，但成绩提交、排行榜、统计和最近记录的数据库异常没有统一转换为 JSON 错误。

数据库锁、目录只读、磁盘错误或 schema 损坏时，客户端可能收到默认 HTML 500，而网络层只返回 `None`，无法区分“本地服务故障”与“没有数据”。

### 修复要求

如果保留 Flask：

- 统一捕获 `sqlite3.Error`；
- 返回稳定错误码；
- 日志保留 traceback；
- UI 使用“本地成绩库暂时不可写”而不是“离线”；
- 不向用户暴露内部路径或 SQL。

---

## 4. 其他模块的剩余问题

## 4.1 启动器

### 结构问题

`launcher.main()` 仍承担：

- pygame 初始化；
- 玩家名输入；
- 固定游戏元数据；
- 网络健康检查；
- 排行榜和最近记录；
- 卡片布局；
- 输入和 hover 动画；
- 动态导入游戏；
- 子游戏返回后的 display 恢复；
- 异常输出。

建议拆为：

```text
LauncherApp
LauncherState
GameRegistry
LauncherRenderer
LauncherInput
LocalDashboard
GameRunner
```

### 具体问题

1. 游戏元数据重复存在于：
   - 启动器本地列表；
   - 服务端 `SUPPORTED_GAMES`；
   - `module_map`；
   - accent/tag/title 等映射。
2. 布局固定围绕五张卡片，新增或隐藏游戏不够稳健。
3. 游戏选择主要依赖鼠标，没有完整键盘/手柄导航。
4. 玩家名输入使用 `KEYDOWN.event.unicode`，不支持完整 `TEXTINPUT/TEXTEDITING` IME。
5. 客户端限制 16 字符，服务端允许 32，契约不一致。
6. 游戏启动失败只打印终端，UI 无错误详情和恢复按钮。
7. 注释声称描述可换两行，实际绘制路径仍近似单行。
8. 模块文档称游戏列表来自后端，但运行逻辑仍以固定本地列表为核心。
9. “在线/离线”状态不符合本地单机产品语义。
10. hover 插值应基于 `dt`，而不是依赖帧数。

## 4.2 共享 UI

优点仍然明显：

- 统一暂停；
- 失焦暂停；
- 结算层输入防穿透；
- 返回启动器时只关闭 display；
- 字体缓存；
- 异步排行榜读取。

剩余问题：

1. 状态仍是 `"playing" / "paused" / "gameover" / "won"` 魔法字符串；
2. 状态进入、退出动作散落；
3. 每帧创建 overlay、阴影、mask、渐变 panel 等 Surface；
4. 多个游戏在 draw 中反复构造 Button；
5. 固定像素窗口，缺少逻辑画布、缩放和高 DPI；
6. 只使用系统字体，CJK fallback 不确定；
7. broad `except Exception` 会吞掉接口错误；
8. 没有统一保存状态提示；
9. 没有用户可见崩溃页和日志位置；
10. 没有设置、音频、主题、按键映射和可访问性层。

## 4.3 俄罗斯方块

已经修复：

- 规则描述不再误称标准 SRS；
- 持续软降；
- 左右重叠的基本按键优先级；
- 多处旋转回归。

仍需处理：

1. 同义物理键记账错误；
2. 大 `dt` catch-up 跨新旧方块；
3. `_collides(cells, board=None)` 使用 `board = board or self.board` 时，显式传入空列表会被忽略；
4. 随机块仍是 `random.choice`，可能长时间缺某种块；
5. 没有可注入随机源，难以重放 Bug；
6. 没有 lock delay、ghost、hold；这些是可选舒适性功能，不是必须做成竞技规则；
7. 静态棋盘背景和网格可缓存；
8. 规则和计分需要写入帮助页。

## 4.4 贪吃蛇

双转向队列修复有效。

仍需处理：

1. 长帧最多补多步，玩家可能看不到中间位置就直接死亡；
2. 对休闲单机更合理的策略是大卡顿时只走一步或自动暂停；
3. 棋盘底纹每帧重画；
4. 无本地最高分、速度选择、穿墙/障碍等模式；
5. 无可注入 RNG 和调试 replay；
6. 食物颜色可增加形状符号，避免只靠颜色传达。

## 4.5 2048

除提交生命周期外：

1. 动画期间只有一个方向缓存；
2. `_pending_state` 没有形成实际用途；
3. R 在进行中立即重置，进度较高时容易误触；
4. 无本地最高分持久化；
5. 无撤销；
6. 无保存/继续；
7. 随机生成不可注入，不利于测试；
8. 逻辑、动画和持久化仍耦合在一个类中；
9. 静态棋盘背景可缓存。

## 4.6 推箱子

本轮最重要的两项计分漏洞已经修复。

仍需处理：

1. `_submitted_total` 应由保存 ACK 驱动；
2. parser 注释把非墙格称为 reachable，但没有 flood-fill 验证；
3. 未验证地图封闭性、玩家可达区域和静态死格；
4. 可以存在零箱子关卡，需明确是否允许；
5. `1000 - moves` 不能很好表达不同关卡难度；
6. 更适合本地项目的成绩是：
   - 每关最少移动；
   - 最少推动；
   - 是否无撤销；
   - 星级；
   - 完成状态；
7. 无死锁检测；
8. 无提示；
9. 无关卡选择和解锁进度；
10. 无本地关卡编辑器和 XSB 导入；
11. 每关改变窗口尺寸，体验不稳定；
12. history 保存完整箱子集合，当前规模可接受，扩展大图时可改为操作增量。

## 4.7 祖玛

本轮对祖玛的修复较有针对性：

- pending 队列；
- 对象引用边界；
- bisect 路径查找；
- 保留计时余量；
- 临界救场顺序。

仍需处理：

1. 多个重叠反应仍应通过状态机测试和属性测试证明确定性；
2. `incoming` 仍使用 list `pop(0)`，当前数量小，优先级低；
3. level clear 时未特别处理仍在飞行的弹丸，结算层下可能视觉冻结；
4. 部分视觉脉冲使用系统墙钟，暂停与重放不完全确定；
5. 每帧创建全屏氛围、瞄准和粒子 Surface；
6. 完整通关、单关练习和失败成绩仍缺少更清晰的本地记录语义；
7. 无关卡选择、训练模式、色弱符号；
8. 无可注入 RNG；
9. 可增加原创道具球和轨道编辑器，但应先稳定反应状态机。

---

## 5. 本地优先的推荐架构

## 5.1 明确非目标

本项目不需要：

- 强制启动后端进程；
- HTTP 才能保存成绩；
- 账号系统；
- 公网排名；
- 云端身份；
- 反作弊；
- 匹配或实时联机；
- 服务端重放；
- 默认联网或遥测。

## 5.2 推荐默认运行路径

```text
pygame 游戏
    │
    ├── GameEngine
    ├── Renderer / Input
    └── LocalGameStore
            └── SQLite（操作系统用户数据目录）
```

可选教学/API 模式：

```text
Flask adapter
    └── 使用同一个 LocalGameStore / Repository
```

这样可以保留 Flask 学习价值，同时正常启动小游戏时不再依赖：

- 端口；
- 健康检查；
- requests；
- CORS；
- 本地 IP；
- 后台 Flask 进程；
- “在线/离线”状态。

## 5.3 推荐仓储接口

```python
class LocalGameStore(Protocol):
    def record_attempt(self, attempt: GameAttempt) -> None: ...
    def update_attempt(self, attempt_id: str, **changes) -> None: ...
    def get_personal_best(self, game_id: str, mode: str) -> GameAttempt | None: ...
    def list_recent_attempts(self, limit: int = 20) -> list[GameAttempt]: ...
    def save_progress(self, game_id: str, payload: dict) -> None: ...
    def load_progress(self, game_id: str) -> dict | None: ...
    def save_settings(self, payload: dict) -> None: ...
    def load_settings(self) -> dict: ...
```

`attempt_id` 只是本机记录 ID，用于：

- 幂等；
- 更新同一局 2048；
- 崩溃恢复；
- 最近记录。

它不是在线身份，也不需要“验证”。

## 5.4 推荐本地数据表

### `schema_meta`

```text
version
updated_at
```

### `profiles`（可选）

```text
id
display_name
created_at
last_used_at
```

单用户模式可以只有默认 profile。

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
progress_json
updated_at
```

用于推箱子关卡解锁、祖玛关卡进度等。

### `save_slots`

```text
profile_id
game_id
slot
ruleset_version
state_json
updated_at
```

用于 2048 中断继续等。

### `settings`

```text
key
value_json
updated_at
```

### 最佳成绩

初期不必单独建表，可从 `attempts` 查询。数据量增长后再增加缓存表。

## 5.5 保存线程策略

本地 SQLite 单次小事务通常远轻于 HTTP，但仍应以实测决定是否放入渲染线程。

推荐：

1. 先实现短事务直接写入并测量；
2. 若 p95 写入超过帧预算，使用单一 `LocalWriteWorker`；
3. worker 只有一个 SQLite 写连接；
4. 游戏退出、重置和程序关闭前调用 `flush()`；
5. 保存失败必须可见；
6. 数据库写入使用事务和原子迁移；
7. 不使用多个线程同时写 SQLite。

---

## 6. 完整优化任务清单

### 优先级定义

- **P0**：可能丢成绩、污染真实数据、导致错误启动或明确破坏游戏输入；发布阻断。
- **P1**：本地架构、数据语义、测试和资源生命周期问题；稳定版前应完成。
- **P2**：性能、可访问性、存档、输入和维护性。
- **P3**：内容扩展、发行和长期完善。
- **S/M/L/XL**：相对工作量。

---

## 6.1 P0：先关闭当前可靠性缺口

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG2-P0-01 | 修复测试数据库隔离 | 测试配置注入、临时 DB fixture | 完整测试前后仓库默认 DB 不创建、不修改；直接 import 服务端也只使用临时目录 | 无 | S |
| CG2-P0-02 | 修复自定义端口配置 | `GAMES_API_URL` 传播或删除默认 HTTP 依赖 | 任意合法端口启动后，客户端访问同一服务；监听地址与连接地址分离 | 无 | S |
| CG2-P0-03 | 建立统一保存状态机 | `ScoreSaveController` | 所有游戏都能显示 saving/saved/failed；Future 异常不静默；有显式重试 | 无 | L |
| CG2-P0-04 | 修复提交与记录读取竞态 | ACK 后刷新、generation token | 模拟 GET 早于 POST 完成时，结算页最终仍显示新记录；旧 Future 不覆盖新状态 | P0-03 | M |
| CG2-P0-05 | 退出/重置前可靠保存 | flush/drain 机制 | 提交未完成时返回菜单、重置或关闭，成绩最终已保存或明确提示失败 | P0-03 | L |
| CG2-P0-06 | 修复推箱子 ACK 语义 | pending/confirmed total | 保存失败不更新 confirmed；重试成功后只写一次；同关仍不重复计分 | P0-03 | S |
| CG2-P0-07 | 完成 2048 保存生命周期 | 统一 attempt、自动重试、安全结果解析 | 首次失败后无需人工再次触发；退出/重开不丢最终分；非法响应不崩溃 | P0-03/05 | L |
| CG2-P0-08 | Tetris 按物理键跟踪 | pressed key set、动作派生 | `←+A` 松开任一键仍保持另一键；`Down+S` 同理；左右恢复优先级明确 | 无 | M |
| CG2-P0-09 | Tetris 阻止补步跨代 | generation guard、dt cap | 旧方块锁定后，剩余 catch-up 不作用于新方块；大 dt 不瞬降新块 | 无 | M |
| CG2-P0-10 | 统一数据库错误处理 | JSON/本地错误对象、日志 | DB 锁定、只读、损坏路径均不返回 HTML；UI 给出本地保存错误 | P0-03 | M |
| CG2-P0-11 | 严格客户端结果契约 | typed result / dataclass | 成功必须 `ok=True`；非法 ID、错误 JSON、4xx/5xx 可区分且不抛到游戏循环 | P0-03 | M |
| CG2-P0-12 | 新增发布阻断回归测试 | 至少覆盖 F01-F13 的核心路径 | 所有 P0 场景先能失败、修复后通过；CI 中任一失败阻止合并 | P0-01~11 | L |

### P0 必须新增的测试

```text
test_regression_never_touches_default_database
test_custom_port_reaches_same_backend
test_result_save_failure_is_visible_and_retryable
test_result_leaderboard_refreshes_after_ack
test_exit_flushes_pending_local_result
test_sokoban_confirmed_total_requires_ack
test_2048_failed_final_save_retries_automatically
test_2048_reset_does_not_drop_pending_final_score
test_tetris_alias_keys_are_tracked_independently
test_tetris_soft_drop_catchup_stops_after_lock
test_tetris_gravity_catchup_stops_after_lock
test_malformed_save_result_never_crashes_game
```

---

## 6.2 P1：改成本地优先、可维护的基础架构

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG2-P1-01 | 编写本地优先架构决策记录 | `docs/adr/001-local-first.md` | 明确默认无 HTTP、Flask 可选、非目标列表、迁移路径 | P0 稳定后 | S |
| CG2-P1-02 | 抽象 `LocalGameStore` | repository protocol + SQLite 实现 | 游戏代码不依赖 Flask/requests；测试可注入内存 store | P1-01 | L |
| CG2-P1-03 | 默认启动改为进程内存储 | launcher 直接使用 store | 无后端进程也能保存、读取最佳和最近记录；正常启动不需要端口 | P1-02 | L |
| CG2-P1-04 | 将 Flask 降为可选适配器 | 可选 CLI/API 模式 | Flask 路由调用同一 repository；关闭 Flask 不影响桌面功能 | P1-02/03 | L |
| CG2-P1-05 | 使用操作系统用户数据目录 | 路径服务 | 安装目录只读仍可运行；数据库、设置、日志、存档均位于用户目录 | P1-02 | M |
| CG2-P1-06 | 建立 schema version 与迁移 | migration runner、备份 | 升级可迁移旧 `scores.db`；迁移失败不破坏原文件；支持回滚说明 | P1-05 | L |
| CG2-P1-07 | 重构本地 attempt 数据模型 | attempts/progress/save/settings | 每局历史、最佳、进度和存档语义分离；`plays` 是真实 attempt 数 | P1-02/06 | XL |
| CG2-P1-08 | 重做本地成绩界面 | 个人最佳、最近游玩、本机统计 | 不再以“在线排行榜”为默认语言；可选按本机 profile 查看记录 | P1-07 | L |
| CG2-P1-09 | 统一游戏注册表 | `GameDescriptor` | 游戏名、入口、尺寸、主题、模式和规则版本只有一个来源 | 无 | L |
| CG2-P1-10 | 拆分 launcher | App/State/Renderer/Input/GameRunner | 主循环不再同时处理数据、布局和导入；新增游戏无需修改多处映射 | P1-09 | L |
| CG2-P1-11 | 状态枚举与状态转换 | `GameState`、enter/exit hooks | 无散落魔法字符串；非法转换有测试；保存和 overlay 在 enter hook 中触发一次 | P0-03 | L |
| CG2-P1-12 | 明确关闭资源 | `close()`/context manager | worker、数据库连接、可选 Session 和 Future 在退出时全部收尾 | P0-05/P1-02 | M |
| CG2-P1-13 | 使用 Flask app factory（若保留） | `create_app(config)` | import 不创建文件；测试可注入 DB；并发启动迁移不会竞争 | P1-04/06 | M |
| CG2-P1-14 | 迁移到 pytest | fixtures、markers、parametrize | 可单独运行任一测试；不再 import 即执行；超时可配置 | P0-12 | XL |
| CG2-P1-15 | GitHub Actions | 跨平台 headless workflow | Linux/Windows/macOS 至少 smoke；上传 JUnit、日志和失败截图 | P1-14 | L |
| CG2-P1-16 | 建立 `pyproject.toml` 与依赖锁定 | package metadata、dev extras、lock/constraints | 空环境可复现安装；移除重复依赖源和 `sys.path` 补丁 | 无 | L |
| CG2-P1-17 | 结构化日志和用户错误页 | rotating log、crash dialog | 异常返回菜单；显示日志位置、版本和可重试操作，不只打印终端 | P1-05/10 | M |
| CG2-P1-18 | 更新完整工程文档 | README、架构、数据、规则、故障排查 | 新用户从空环境完成安装、运行、测试、备份；文档不再声称错误的 DB 隔离 | P1-01~17 | M |

---

## 6.3 P2：体验、输入、性能和可访问性

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG2-P2-01 | 统一 InputManager | 物理键、动作映射、DAS/ARR、队列 | 所有游戏使用动作而非散落 keycode；暂停/失焦后状态正确清空 | P0-08 | L |
| CG2-P2-02 | 正确支持 IME | `TEXTINPUT/TEXTEDITING` 输入控件 | 中文、日文、韩文组合输入、退格、确认可用；昵称长度统一 | P2-01 | M |
| CG2-P2-03 | 本地设置系统 | 音量、窗口、按键、辅助选项 | 原子保存并带 schema version；设置损坏时安全恢复默认值 | P1-07 | L |
| CG2-P2-04 | 音频系统 | BGM/SFX、分组音量、静音 | 游戏切换、暂停和关闭正确释放；无音频设备时不崩溃 | P2-03 | L |
| CG2-P2-05 | 逻辑分辨率与响应式窗口 | resizable、letterbox、DPI | 常见分辨率不裁切；高 DPI 字体清晰；卡片和 overlay 自动布局 | P1-10 | XL |
| CG2-P2-06 | 字体 fallback 与资源管理 | 字体链、asset manager | 常见系统缺 CJK 字体时仍能显示中文；缺资源有明确降级 | P2-05 | M |
| CG2-P2-07 | 键位重映射和手柄 | action binding、controller support | 启动器和五款游戏均可不用鼠标完成主要操作 | P2-01/03 | L |
| CG2-P2-08 | 可访问性选项 | 色弱符号、高对比度、降低动态效果 | 颜色不是唯一信息通道；可关闭屏幕抖动/脉冲/高频动画 | P2-03/06 | L |
| CG2-P2-09 | 缓存静态 Surface | panel/gradient/grid/text cache | 静态渐变、网格、阴影不再每帧重建；缓存有尺寸和主题失效 | P2-05 | L |
| CG2-P2-10 | 优化祖玛渲染和容器 | Surface pool、deque、轨迹缓存 | 最复杂关卡满足帧预算；逻辑结果与修复前一致 | P2-09 | M |
| CG2-P2-11 | 统一游戏时钟和 RNG | monotonic game clock、injectable RNG | 暂停不推进逻辑时间；相同 seed+输入可重现 Bug | P1-11 | L |
| CG2-P2-12 | 渐进抽取纯逻辑引擎 | rules modules | 核心规则测试无需 SDL；不要求一次性重写全部 UI | P2-11 | XL |
| CG2-P2-13 | 保存/继续未完成局 | save slots | 2048 可继续；需要时扩展到其他游戏；旧规则存档有版本兼容提示 | P1-07/P2-11 | L |
| CG2-P2-14 | 性能基准 | profiler、基准场景 | 指定机器下 60 FPS 场景 p95 frame ≤16.7ms；保存不产生明显长帧 | P2-09/10 | M |
| CG2-P2-15 | 长时间稳定性测试 | 30–60 分钟 soak | 内存、Surface、线程、FD 不持续增长；切换游戏 100 次不泄漏 | P1-12/P2-14 | M |
| CG2-P2-16 | 本地数据导入导出 | JSON/ZIP export、校验 | 可备份成绩、进度、设置；导入不会覆盖损坏现有数据 | P1-06/07 | L |

---

## 6.4 P3：面向单机合集的内容完善

| ID | 任务 | 交付物 | 验收标准 | 依赖 | 量级 |
|---|---|---|---|---|---|
| CG2-P3-01 | Tetris 舒适模式 | 7-bag、ghost、hold、next、lock delay 可选 | 经典自定义模式仍保留；模式和规则版本分开记录 | P2-11/12 | XL |
| CG2-P3-02 | Snake 本地模式 | 经典、穿墙、障碍、速度选择 | 每种模式规则明确；个人最佳按模式区分 | P2-12 | L |
| CG2-P3-03 | 2048 完整本地体验 | 最高分、撤销、保存、棋盘尺寸 | 撤销和保存有明确规则；不会重复写 attempt | P2-13 | L |
| CG2-P3-04 | Sokoban 关卡进度 | 选关、解锁、每关最佳、星级 | 练习/闯关语义清晰；完成进度可靠保存 | P1-07 | L |
| CG2-P3-05 | Sokoban 死锁与提示 | 静态死角、基础提示 | 明确提示“可能死锁”；提示可关闭；不会修改排位概念，因为本项目不做竞技 | P2-12 | XL |
| CG2-P3-06 | Sokoban 编辑器 | 地图编辑、XSB 导入导出、验证 | 拒绝多玩家、箱目标不等、非法字符、明显不封闭和不可达地图 | P3-04/05 | L |
| CG2-P3-07 | Zuma 训练与关卡选择 | 练习关、速度/颜色辅助 | 练习进度不与完整通关记录混淆；支持色弱符号 | P1-07/P2-08 | L |
| CG2-P3-08 | Zuma 原创内容扩展 | 道具球、轨道、目标 | 每项机制有确定性测试；先完成 reaction FSM 再扩展 | P2-12 | XL |
| CG2-P3-09 | Zuma 轨道编辑器 | 可视化轨迹编辑和验证 | 轨迹不越界、长度合法；配置带版本号 | P3-08 | L |
| CG2-P3-10 | 本机档案与成就 | 可选 profiles、成就、统计页 | 无需账号；默认单 profile；成就不会因重复加载/导入重复触发 | P1-07/08 | L |
| CG2-P3-11 | 离线每日挑战与 replay | 本地日期 seed、回放文件 | 完全离线；同版本同 seed 可重现；不用于反作弊或在线排行 | P2-11/12 | L |
| CG2-P3-12 | 谨慎增加新游戏 | 原创或通用玩法插件 | 每个新游戏遵循 registry、存储、输入、测试和可访问性契约 | P1-09/P2-12 | XL |
| CG2-P3-13 | 本地化 | 中文/英文资源、长文本测试 | UI 代码无主要硬编码文案；布局通过长字符串和不同字体测试 | P2-05/06 | L |
| CG2-P3-14 | 桌面打包与发布治理 | Windows/macOS/Linux 包、版本、变更日志 | 无 Python 环境可运行；数据在用户目录；升级不丢进度 | P1/P2 完成后 | XL |
| CG2-P3-15 | 许可证和素材审查 | LICENSE、NOTICE、第三方清单 | 代码、字体、音频、图形来源明确；正式分发前核查名称和素材风险 | 无 | M |
| CG2-P3-16 | 项目展示与社区文件 | 截图/GIF、CONTRIBUTING、SECURITY | README 首屏能展示玩法；问题报告包含日志、版本和复现模板 | P3-14/15 | M |

### 可考虑新增的通用玩法

优先选择不依赖受保护角色和商业素材、规则易测试、适合本地短局的类型：

- 扫雷类；
- 打砖块类；
- 双人同屏球拍类；
- 原创纵向射击；
- 连连看或匹配消除；
- 井字棋/四子棋；
- 数独；
- 华容道；
- 记忆翻牌；
- 弹球或物理小关卡。

不要以“游戏数量”作为主要完成度指标。每增加一款游戏，必须同时交付：

- 纯规则测试；
- 本地记录语义；
- 键盘与可访问性支持；
- 暂停/返回/失焦行为；
- 资源许可；
- 至少一个长帧或边界测试。

---

## 7. 建议新增的测试体系

## 7.1 单元测试

### 通用持久化

- 同一 `attempt_id` 重复写入幂等；
- attempt 历史与个人最佳分离；
- schema 迁移失败保留原数据库；
- 设置文件损坏后恢复默认；
- 保存失败状态可重试；
- flush 后无 pending write。

### Tetris

- 同义键独立记账；
- 左右最近按下优先及释放恢复；
- 软降和重力 catch-up 不跨 piece generation；
- 大 `dt` 上限；
- 显式空 board 参数被正确使用；
- 若加入 7-bag，每 7 个块包含全部类型。

### Snake

- 双转向队列；
- 不接受 180°；
- 长卡顿策略；
- 食物不生成在身体上；
- 长度只在进食时增加。

### 2048

- 每个 tile 每步最多合并一次；
- 移动与合并总和守恒；
- 生成新 tile 前后总和差只能是 2 或 4；
- 多输入队列顺序；
- 撤销、保存和恢复；
- failed save → retry → ack。

### Sokoban

- 同关重复完成不重复累计；
- 练习跳关永不标记完整闯关；
- 完成全部关卡才算完整；
- 保存 ACK 与 confirmed 分离；
- parser 封闭性、可达性、箱目标数量；
- 静态死角；
- 每关最佳移动/推动更新规则。

### Zuma

- 两个独立 pending reaction 都会完成；
- 重叠 pending 的顺序确定；
- reaction 不会因列表下标变化丢失；
- chain 逻辑位置单调；
- 长帧 spawn/cooldown 结果稳定；
- 最后一帧弹丸救场；
- level clear 时弹丸处理规则明确。

## 7.2 集成测试

1. 全新用户目录首次启动；
2. 旧 `scores.db` 自动迁移；
3. 数据目录只读；
4. 数据库锁定；
5. 自定义路径含中文和空格；
6. 游戏结束保存后立即返回菜单；
7. 保存中关闭程序；
8. 连续切换五个游戏 100 次；
9. 测试绝不修改真实用户数据；
10. 可选 Flask 模式与进程内模式读取同一 repository；
11. Windows/macOS/Linux 文件路径；
12. 打包产物首次启动。

## 7.3 UI/E2E

- 全启动器仅键盘可操作；
- 手柄导航；
- IME 昵称输入；
- 高 DPI；
- 低分辨率；
- 色弱符号；
- 减少动态效果；
- 无音频设备；
- 窗口失焦与恢复；
- 双击 overlay 防穿透；
- 保存失败提示和重试；
- 崩溃后回到菜单并能找到日志。

## 7.4 测试工具治理

- 迁移 pytest；
- 每个子进程有可配置 timeout；
- CI 上传失败截图和日志；
- 生成 JUnit；
- 记录覆盖率；
- 对纯规则模块使用属性测试；
- 可选 mutation testing 验证测试是否真的能杀死错误；
- 测试目录运行后执行 `git diff --exit-code` 和数据目录污染检查。

---

## 8. 性能与稳定性验收门槛

指标必须记录基准机器、系统、Python/打包版本和窗口尺寸。

1. 正常游戏目标 60 FPS；
2. 指定基准机 p95 frame time 不超过 16.7ms；
3. 本地保存不得在常规路径产生明显可感知卡顿；
4. 如使用后台 writer，返回菜单或退出时能可靠 flush；
5. Zuma 最大链场景满足帧预算；
6. 静态渐变、网格和 panel 不再每帧重新创建；
7. 连续切换游戏 100 次：
   - 线程数回到基线；
   - Surface/内存不持续增长；
   - 无残留 Flask 进程；
8. 30–60 分钟自动游玩：
   - 内存达到稳定平台；
   - 数据库无未处理 lock；
   - 保存队列最终清空；
9. 模拟 500ms 磁盘/可选 API 延迟：
   - 游戏输入和动画保持响应；
   - 保存状态明确；
10. 窗口休眠、拖动或系统卡顿后，不出现 Tetris 新块瞬降、Snake 不可见多步等跨生命周期行为。

---

## 9. 稳定版本质量门禁

建议把以下条件作为第一个正式稳定版本的最低要求：

- 所有 P0 关闭；
- 默认启动不依赖 HTTP 服务；
- 测试不会接触真实或仓库默认数据库；
- 个人最佳、最近记录、游玩次数语义准确；
- 成绩保存失败可见且可重试；
- 退出/重置不会静默丢成绩；
- Tetris 同义键和大 `dt` 问题有回归测试；
- 数据存放在操作系统用户目录；
- 有 schema version、迁移和备份；
- pytest + CI 可从空环境运行；
- Windows、macOS、Linux 至少有 smoke test；
- 纯规则模块建议：
  - 行覆盖率 ≥ 90%；
  - 分支覆盖率 ≥ 85%；
- 全项目建议行覆盖率 ≥ 80%；
- 静态检查、类型检查和依赖检查通过；
- README、LICENSE、CHANGELOG、数据位置和故障排查完整；
- 不默认联网、不默认遥测。

覆盖率不能替代规则规格、属性测试和真实玩家测试。

---

## 10. 推荐实施顺序

### M0：修复可靠性回归

完成全部 P0：

- 测试隔离；
- 端口配置；
- 保存状态机；
- ACK 后刷新；
- 退出 flush；
- 2048/推箱子保存；
- Tetris 输入与大 dt；
- 数据库错误；
- P0 回归测试。

### M1：本地优先简化

- 抽象 `LocalGameStore`；
- 默认去掉 HTTP 依赖；
- Flask 变为可选适配器；
- 使用用户数据目录；
- 重做 attempts/progress/save/settings；
- 将“在线排行榜”改成“个人最佳和最近游玩”。

这是最符合项目定位、同时能显著减少故障面的阶段。

### M2：工程可持续

- 游戏注册表；
- launcher 拆分；
- 状态枚举；
- pytest；
- CI；
- `pyproject.toml`；
- 依赖锁定；
- 日志与错误页；
- 完整文档。

### M3：体验与性能

- InputManager；
- IME；
- 设置；
- 音频；
- 响应式窗口；
- 字体；
- 手柄；
- 可访问性；
- Surface 缓存；
- RNG/Clock；
- 存档；
- 性能和 soak test。

### M4：内容与发行

在基础稳定后再增加：

- 各游戏舒适性功能；
- 关卡、提示和编辑器；
- 本机成就；
- 离线每日挑战；
- 新游戏；
- 本地化；
- 桌面包；
- 许可证和展示材料。

---

## 11. 最终判断

本轮修复体现了较好的针对性，尤其是推箱子、祖玛、API 校验、主线程阻塞和输入队列方面。项目已经比上一版更稳健，且核心修复并非只针对表面症状。

当前最主要的技术债已经从“明显游戏规则漏洞”转移到：

- 异步保存结果没有闭环；
- 退出、重置和读取榜单时的并发生命周期；
- 测试环境隔离；
- 启动配置一致性；
- 长帧与物理按键模型；
- 本地数据语义。

对于这个项目，最值得做的下一步不是扩展联网能力，而是：

> **把 Flask 从必需运行组件降为可选适配器，用进程内本地仓储稳定保存个人最佳、最近游玩、关卡进度、设置和存档。**

完成 P0 和 P1 后，项目将从“修复较充分的 AI 辅助小游戏原型”进入“可靠、易维护、适合桌面发行的本地经典小游戏合集”阶段。
