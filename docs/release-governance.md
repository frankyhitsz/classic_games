# 发行前权利与版本检查

这份清单用于仓库所有者决定是否公开分发，不替代权利人的许可判断。

## 权利确认

- [ ] 确认仓库全部代码的作者、雇佣关系和贡献授权；
- [ ] 选择与上述权利一致的开源或闭源许可证，再添加根目录 `LICENSE`；
- [ ] 若接受外部贡献，确定贡献者声明或 CLA/DCO 规则；
- [ ] 核对“2048”“俄罗斯方块”“祖玛”等名称、玩法描述和图形是否需要改用通用称呼；
- [ ] 在打包平台逐项复核商标、应用图标、截图和商店文案。

## 素材清单

当前运行时不打包外部图片、音频或字体文件。游戏图形由 pygame 程序绘制；中文字体从操作系统
字体链选择，字体文件不会复制进发行物。仓库若加入图片、音效、音乐、字体或关卡来源，必须在
发布前记录：文件路径、作者、来源链接、许可证、修改情况和署名要求。

确定许可后，可据此生成 `NOTICE`：列出实际进入发行物的第三方内容及其必要声明。没有权利证据
的素材不得进入安装包。

## 版本规则

- 应用使用 SemVer；用户可见兼容功能为 minor，兼容修复为 patch，破坏命令或数据契约为 major；
- SQLite schema 只递增，升级前保留数据库备份，不支持静默降级；
- state journal schema 必须保留明确的旧版迁移器和原始字节备份；
- 改变计分、关卡完成条件或辅助规则时增加对应游戏 `ruleset_version`，历史成绩保留原版本；
- 发布标签前运行 `python -m tests.release release`，并确认 release-gate、三平台测试和兼容矩阵通过。

`constraints-release.txt` 固定正式验证使用的完整解析依赖闭包；`pyproject.toml` 的范围用于普通
安装和兼容性 CI。升级约束时应重新解析三平台依赖，运行依赖审计、完整测试和隔离 venv wheel
与 sdist smoke，并检查 release profile 输出的 CycloneDX `release-sbom.json` 和
`release-installed-packages.json`。当前约束是精确版本清单，但还不是带 hash 的跨平台 lock；正式
发布前若启用 `--require-hashes`，必须同时收集 Windows、macOS 和 Linux 所需 wheel 的 hash，
不能只锁开发机平台。
