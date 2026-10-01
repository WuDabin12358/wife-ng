# 测试与验收

## 离线检查

```powershell
python scripts/fetch_dependencies.py
python -m unittest discover -s tests -p 'test_*.py' -v
.\gradlew.bat build architectureChecks --no-daemon
```

Python 单元测试覆盖蓝图、布局、审查、修订范围和规划上下文；Java architectureChecks 校验 1.16.5 注册表及几何规则。
GitHub Actions 运行同样的离线检查，不启动游戏、不使用模型密钥、不证明实机成功。
PNG 预览工具可选安装 `python -m pip install -r requirements-dev.txt`；非 Windows 系统可通过 WIFE_NG_PREVIEW_FONT 设置支持中文的字体路径。

## 手动实机验收

在可回滚的测试世界中使用自己的玩家和机器人账号：

1. 确认本次 JAR 与部署 JAR 的 SHA-256 一致，重启客户端，确认 /health 和 /v1/state 的 connected。
2. 查询 /v1/capabilities；普通模式应没有 server_command，OP 模式才应提供。
3. 测试聊天、跟随、停止、移动；通过实际坐标和任务终态确认执行结果。
4. 测试采集、合成、烧炼；对比执行前后的背包，不以模型回复判断成功。
5. 建造前保存世界；记录蓝图与 task ID，等待施工终态，然后执行独立验证。
6. 测试施工暂停/恢复与主人停止；检查停止后不会继续发出新施工步骤。
7. 检查断线、死亡、材料不足、未加载区域及客户端重启后的任务和记忆。

tests/live_*.py、dry_run_architect.py 和 apply_saved_revision.py 是手动验收工具，部分会调用真实模型或修改世界。先阅读参数与实现；它们不在自动单元测试中运行。
发布证据应记录提交版本、模式、环境、任务 ID、状态和独立观察结果，移除账号、密钥、聊天和服务器密码。
