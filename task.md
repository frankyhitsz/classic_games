# 第八次审查修复记录

## 状态

- [x] 逐项核对 F01–F26 和 P0–P3 优化清单；
- [x] 实现 state journal v2、跨进程锁、CAS、logical revision、ruleset 冻结和有界扫描；
- [x] 增加 non-durable state fallback、退出门禁与 `LocalStateEvent`；
- [x] 完成游戏级 progress schema、journal merge、读写 generation 和状态 HUD；
- [x] 完成 `ProfileController`、canonical default profile 和绑定档案的 launch token；
- [x] 加强 schema fast path、无写锁读取和 profile 碰撞迁移；
- [x] 完成 2048 typed load、超时/重试、新开确认、slot v3、坏槽隔离和 UUID 续局；
- [x] 增加 durable receipt committed 重建；
- [x] 完成第一轮独立复查并重新验证；
- [x] 完成第二轮独立复查并重新验证；
- [x] 完成最终静态检查、构建、完整测试和默认数据库指纹检查；
- [x] 创建提交并推送到 `origin/main`。

## 初版验证

- 原有 48 项 v4/v5/v6 定向存储用例通过；
- 新增 13 项 journal、故障、slot、profile 和迁移检查通过；
- 完整项目脚本首次运行：107 项功能检查、89 项存储/生命周期用例和固定 seed 20,000 步
  全部通过；240 次 SQLite 并发写入后 `integrity_check=ok`；100 次资源循环 FD 19→19。

## 第一轮复查

- 发现启动未解析最近档案时，queued launch 绑定了临时 default ID，最近档案返回后会静默丢弃
  点击。`ProfileController` 现在区分 unresolved token：generation 未变化时在解析后绑定实际档案。
- 发现 state scanner 在坏文件尚未解析出 key 时直接隔离，可能与另一进程替换同一路径竞态。
  scanner 现在先按 64 位文件名摘要取得同一把 OS 锁，再读取、升级或隔离。
- 发现 state v2 parser 没有交叉核对外层 ruleset 与参数，也没有完整限制 legacy 字段。parser
  现在验证 key/operation ID/时间/参数和派生 ruleset，并把类型错误稳定转为隔离结果。
- 修复后第八轮 13 项用例、Ruff 和编译重新通过。

## 第二轮复查

- 发现 profile 碰撞迁移若一侧 progress/setting/slot JSON 损坏，原选择逻辑可能保留损坏新值
  并隔离有效旧值。迁移现在分别校验两侧：只要一侧有效就保留有效值；两侧 progress 有效时
  才单调合并；所有损坏/舍弃原文留在 `invalid_local_state`。
- 为此加入“较新值损坏、较旧值有效”的 setting、progress 和 slot fixture；测试首次暴露异常
  分支未把已解析但校验失败的局部值清空，修正后碰撞升级通过。
- 发现 2048 read Future 永不完成时会永久停在 loading。增加 8 秒超时并保持门禁，用户可以重试
  或返回；确定性 tick 用例通过。
- 修复后第八轮定向测试重新通过。

## 最终验证

- 功能检查 107 项通过；存储、升级和生命周期用例 90 项通过；
- 固定 seed 执行 20,000 步；240 次并发写入后 `integrity_check=ok`；
- 渲染 p95：Tetris 2.762 ms、Snake 2.731 ms、2048 1.911 ms、Sokoban 2.037 ms、
  Zuma 4.665 ms；
- 本机保存 p99 2.385 ms；SQLite 持锁时异步提交 p99 0.123 ms；100 次资源循环 FD 19→19；
- storage + stress 分支覆盖率 62%，通过 60% 门槛；
- Ruff、Python 编译、shell 语法、whitespace、默认数据库指纹和 wheel 导入 smoke 通过；
- `games_env` 未安装 coverage/build，最终检查从临时目录载入工具，没有修改用户环境。
- 首次推送触发的 CI 暴露 Ruff 0.16 扩大隐式规则集造成的工具漂移；现显式固定项目既有
  correctness 规则，并用 Ruff 0.12 与 0.16.4 双版本检查。GitHub Actions 升至 Node 24 的
  v7 action，移除 runner 的 Node 20 弃用路径。
- 第二次 CI 的 lint 与 Python 兼容任务通过，macOS gameplay 阶段呈现单项冷启动超时特征。
  单项子进程预算由 15 秒调至 30 秒，job 总超时不变；CI 现在始终上传 gameplay 详细日志，
  后续失败不再只剩退出码。
- CI #6 进一步定位到 Windows storage 与 macOS gameplay。启动器速度用例原先只发一次退出，
  在慢 runner 上会与新增的未持久 profile 退出保护混测；现固定使用无 profile 状态的 HTTP stub。
  Windows 子进程完成预算由 2 秒调至 10 秒，storage 输出也始终进入 artifact。
- CI #7 仍复现两处失败，而匿名访问不能下载 artifact。runner 现把 gameplay 的失败测试名和
  storage traceback 尾部直接写成 GitHub annotation，确保公开摘要即可定位真实根因。
- CI #8 的公开 traceback 确认 Windows 失败来自测试 fixture：`sqlite3.Connection` 的上下文
  只提交事务，并不会关闭文件句柄。所有临时数据库 fixture 和 stress 检查现显式关闭连接，
  90 项存储测试重跑通过。gameplay 失败摘要同时限制长度并写入 job summary，单项耗时也随
  结果输出，便于继续核对 macOS runner 上的唯一失败。
- CI #9 确认 Windows 文件占用已消失，并暴露旧 v2 数据在低精度系统时钟下可能让 guest 与
  有成绩档案得到相同 `last_used`。迁移现沿用源记录时间，平局时优先有游玩记录的档案；对应
  两项升级用例通过。macOS 的 stress 属共享 runner 性能抖动：本地仍执行 16.7 ms 帧/保存和
  2 ms 入队门槛，CI 使用独立退化保护，并把失败尾部公开为 annotation。
- CI #10 中 Windows 的身份迁移用例已通过，随后在 Defender 介入 SQLite fsync 时录得 100 ms
  同步保存 p95。游戏走另行验证的异步入队路径，因此 CI 的同步磁盘退化线改为 250 ms，本机
  16.7 ms 门槛不变。macOS stress 已通过，仅 gameplay step 仍失败，但测试进程内部的 workflow
  command 未被 runner 收录；workflow 现从 shell 外层发布失败用例名。
- CI #11 在 macOS 复现后台持久化完成超过测试固定 1 秒、Windows 复现 16 个直连写者在慢 fsync
  下耗尽 2 秒 busy timeout。两项都不是前台延迟约束：后台 Future 等待改为 5 秒；仅测试使用的
  Windows 多写者完整性场景允许 60 秒锁等待，提交返回速度仍由独立的异步入队 p99 断言约束。
- CI #12 又在同类后台 Future 的另一处 1 秒字面量超时；这验证了问题是测试预算散落，而不是
  某条保存路径失效。storage v2–v6 中所有 I/O Future 与 drain 的 1/2 秒完成预算统一为 5 秒，
  前台 `submit_score_async` 立即返回的毫秒级断言不变。
- CI #13 的外层日志终于确认 macOS gameplay 并未进入任何游戏用例：临时 Flask 服务仍在启动，
  固定 5 秒健康检查已先行退出。服务就绪等待改为单调时钟控制的 20 秒；游戏子进程的断言与
  30 秒单项预算均未放宽。
- CI #14 显示延长等待后 macOS 的 Flask 开发服务器仍停在绑定前，说明问题不是普通冷启动。
  CI HTTP 往返现改由进程内 Werkzeug server 提供：端口在同步绑定时由系统分配，不再有
  “探测空闲端口—关闭—子进程重绑”的竞态；健康检查、真实 HTTP 请求和关闭回收仍完整执行。
- CI #15 全部通过：Ubuntu、macOS、Windows 的 storage、stress、gameplay，Python 3.12/3.13
  兼容任务及 core-only 安装任务均成功。
- 纯文档提交触发的 CI #16 在 Ubuntu 偶发卡住 `launcher-guest-placeholder`，查明六个启动器 UI
  用例虽然 monkeypatch 了 `BackendClient`，却没有启用 HTTP 测试模式，实际走了本机档案初始化
  和退出保护。六项现显式使用其 stub 后端，避免把输入/悬停/点击测试与持久化生命周期混测。
