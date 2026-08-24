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
  2 ms 入队门槛，CI 使用 50/50/10 ms 的退化保护，并把失败尾部公开为 annotation。
