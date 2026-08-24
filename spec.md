# 第七次审查修复规格

## 目标

核对第七次审查指出的升级、档案、进度和自动存档问题，保证上一版本留下的数据库与待保存
成绩可以直接升级，并让默认桌面模式在数据库暂时被锁或损坏时保住最新本机状态。

## 范围

- Windows 和 POSIX 使用操作系统文件锁串行化同一 request，不使用 PID 探测；
- 待保存成绩 schema 1 原件先备份，再转换为 schema 2 和统一的 UUID 档案身份；
- SQLite schema v5 显式转换旧 settings、progress、save_slots，增加外键、规则版本、数据版本
  和损坏本机状态隔离表；
- standalone 结算与旧成绩导入在同一事务中补齐 profile，排行榜采用当前显示名；
- 启动器在档案确认前不启动游戏，支持本机档案新建、轮换和改名；
- 推箱子与祖玛进度按 ruleset 隔离，使用单调合并，练习与闯关分键；
- 档案、设置、进度和存档使用 keyed latest-value journal，在写库失败后自动补写；
- 2048 在读取自动存档期间屏蔽玩法输入，存档包含 attempt/revision/提交确认状态，终局存档
  不恢复成可继续的棋盘；
- schema repair、系统时钟回拨和保存状态缓存淘汰有可恢复的状态语义；
- HTTP 调试模式明确保持“成绩 API only”，不伪装成本机数据服务的等价实现。

## 非目标

- 不增加账号、云同步、公网排行、遥测或在线反作弊；
- 不在没有新 ruleset、玩法说明和验收样例时改变计分、随机性或关卡规则；
- 不代表仓库所有者选择代码和素材许可证；
- 不把 headless CI 当作 Windows/macOS 安装包和真实显示设备的发布验收。

## 关键决策

1. 锁文件是稳定 inode，锁的持有与释放由内核负责；文件内容不表示进程存活。
2. schema 1 pending 的旧 payload hash 先按旧语义核对，身份转换后重算 schema 2 hash；原字节
   保存在 migration-backup。
3. schema v5 的状态表迁移与版本写入位于一个 `BEGIN IMMEDIATE` 事务；开始前用 SQLite backup
   API 创建独立备份，坏 JSON 进入 `invalid_local_state`。
4. 当前档案名负责展示，attempt 行保留结算时名字作为恢复后备；具体策略见
   `docs/adr/profile-display-name.md`。
5. 关卡进度合并对数字取最大值、布尔取或、集合列表取并集、分数字典逐项取最大值；规则版本
   和 practice/campaign 都是存储维度。
6. keyed state journal 每个语义键只保留最新操作。写库成功后仅在文件 hash 仍匹配时删除，避免
   旧 worker 清掉另一进程写入的更新。
7. 2048 schema 2 存档拒绝越界分数、非法棋盘值、矛盾 won 状态和无效 attempt 元数据；旧
   schema 1 棋盘可读取并在下一次写入时升级。
8. 对未来墙钟超过五分钟的合法 pending 使用当前时间入库并在回执中标记 `clock_adjusted`，
   不把时钟校正当作永久请求错误。

## 验收标准

- 第七次审查 F01–F22 均有代码结果或明确取舍；
- schema v2 含旧 attempts/state tables 的数据库可升级、备份、重复初始化，分数和状态可读；
- schema 1 per-request pending 能恢复旧显示名身份、重算 hash 并重放；
- Windows 锁路径不调用 `os.kill`，坏锁内容与 metadata 前崩溃不造成永久阻塞；
- orphan 状态写入被拒绝，损坏 JSON 被隔离并返回默认值；
- 数据库持锁期间的进度写入返回 durable pending，解锁后可补写；
- 2048 载入前输入无效，恢复 attempt 身份，gameover slot 不恢复为 playing；
- 原有玩法、界面、存储、迁移和压力检查通过；Ruff、编译、whitespace、构建与默认数据库
  指纹检查通过；
- 完成两轮独立复查，修复每轮新发现的问题后重新验证。
