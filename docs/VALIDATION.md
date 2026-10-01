# 公开版验证记录

2026-10-02，在 Windows、Temurin JDK 21、Python 3.12 环境执行：

- `python scripts/fetch_dependencies.py`：官方 Baritone 1.6.3 下载与 SHA-256 校验通过。
- `python -m unittest discover -s tests -p 'test_*.py'`：92 项通过。
- Gradle `build architectureChecks --no-daemon`：BUILD SUCCESSFUL，37 项建筑检查通过，注册表含 763 种方块。
- PowerShell 启动器语法检查通过。
- JAR 元数据、图标、许可证说明检查通过；没有将 Baritone 类打入项目 JAR。
- 公开版 18 个 Java 源文件与本地原项目哈希一致。
- 暂存文件密钥格式、个人绝对路径和运行数据排除检查通过。

本轮未启动游戏或调用付费模型；以上结果不代表实机采集或建筑验收。
持续集成会对每个提交重新执行离线测试和构建，其结果以对应 GitHub Actions 运行记录为准。
