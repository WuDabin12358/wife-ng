# Wife NG

[English](README.en.md)

Minecraft 1.16.5 的自然语言客户端机器人：DeepSeek 负责规划，Fabric 客户端负责感知与验证，Baritone 负责寻路和采集。

`玩家聊天 → 模型规划 → 单个工具 → 游戏执行 → 结果验证 → 下一步`

## 功能

- 感知坐标、生命、饥饿、背包、附近方块、实体及聊天。
- 移动、跟随、采集、合成、烧炼、容器交互与生存建房。
- 创造模式区域扫描、自由蓝图、室内和场地布局、分步施工与逐格复核。
- 任务查询、暂停、恢复、取消、任务事件和按世界隔离的规划记忆。
- 本地 HTTP 工具接口；主人权限与可选 OP 工具。

这是开发中的项目。离线测试不能证明所有地形下的生存任务或大型建筑都能成功；实机验收请参阅 [测试说明](docs/TESTING.md)。

## 环境

| 用途 | 版本 |
| --- | --- |
| 游戏 | Minecraft Java 1.16.5 |
| 客户端运行 | Java 8、Fabric Loader 0.19.3、Fabric API 0.42.0+1.16 |
| 寻路 | Baritone API Fabric 1.6.3（外部模组） |
| 构建 | JDK 21、仓库内 Gradle wrapper |
| 规划器与离线测试 | Python 3.10+，验证环境为 3.12；标准库即可 |

## 构建

安装 Git、Python 和 JDK 21，设置 JAVA_HOME。克隆本仓库后在根目录运行：

```powershell
python scripts/fetch_dependencies.py
.\gradlew.bat build architectureChecks --no-daemon
python -m unittest discover -s tests -p 'test_*.py' -v
```

Linux/macOS 将 `.\gradlew.bat` 换为 `./gradlew`，首次先运行 `chmod +x gradlew`。
Baritone 从官方固定版本下载，并校验 SHA-256；其 JAR 不上传到本仓库。

## 普通客户端运行

1. 在独立游戏目录安装 Minecraft 1.16.5、Java 8 和对应 Fabric。
2. 将 `build/libs/wife-ng-0.1.0-dev.jar`、`libs/baritone-api-fabric-1.6.3.jar` 和 Fabric API 放入该目录的 `mods/`。不要安装 `-sources.jar`。
3. 在启动游戏与规划器的环境中配置主人名称和接口。下例为 PowerShell；从此终端启动 Minecraft 启动器，确保客户端继承环境变量：

```powershell
$env:WIFE_NG_OWNER = '你的Minecraft用户名'
$env:WIFE_NG_BOT_NAME = 'wife'
$env:WIFE_NG_WORLD_ID = 'my-world'
$env:WIFE_NG_TOKEN = '替换为你自己的随机控制令牌'
$env:DEEPSEEK_API_KEY = '替换为你的API密钥'
```

4. 使用机器人账号进入世界或服务器；机器人用户名与 `WIFE_NG_BOT_NAME` 一致。你与机器人同时登录在线服务器时需要各自的账号。
5. 在同样配置的终端运行 `python WifeAgent.py`。使用自己的玩家账号发送：`wife，跟着我`、`wife，停止`、`wife，收集16个橡木原木`。

`WIFE_NG_OWNER` 默认仍为原开发环境的 `Wu_Dabin`，新用户必须改成自己的用户名。其他玩家可聊天，但不能触发游戏动作。
可通过 `DEEPSEEK_MODEL` 和 `DEEPSEEK_API_URL` 更换模型与端点；实际工具调用、上下文和思考协议需与规划器兼容。
完整变量见 [.env.example](.env.example)，该文件仅作参考，不会自动加载。

## 无头运行（可选，Windows）

主路径是上述普通客户端。仓库保留 `Start-Wife-NG.ps1` 和三个中文启动入口，供已经安装 HeadlessMC 的用户使用。
先按 [HeadlessMC 官方文档](https://headlesshq.github.io/headlessmc/) 安装并配置 1.16.5 的 Fabric 游戏实例，将启动器包装 JAR 放到 `headlessmc/headlessmc-launcher-wrapper.jar`。
在 HeadlessMC 中配置自己的 Java 8 路径、游戏目录和机器人账号；将上面的三个模组装入其游戏目录。
设置 `WIFE_NG_HEADLESS_JAVA` 为启动 HeadlessMC 的 Java 可执行文件（JDK 21），再运行：

```powershell
.\Start-Wife-NG.ps1 -Server 127.0.0.1:25565
```

`-NoDeepSeek` 只启动身体；`-OpMode` 启用服务器命令工具；`-CreativeBuild` 额外请求并确认创造模式，需服务器授权。
本仓库不包含 HeadlessMC、JDK、Minecraft、世界存档或账号缓存；首次克隆不能直接双击启动。

## 文档与目录

- [架构与 API](docs/ARCHITECTURE.md)
- [测试与实机验收](docs/TESTING.md)
- [贡献说明](CONTRIBUTING.md)、[安全说明](SECURITY.md)、[第三方说明](THIRD_PARTY_NOTICES.md)
- `src/main/`：Fabric 客户端；`src/architecture/`：离线注册表与几何检查。
- `WifeAgent.py`：规划器；`Building*.py`、`InteriorLayout.py`、`SiteLayout.py`：蓝图、记忆和布局。
- `tests/`：离线单元测试及需要手动运行的实机/渲染工具；`scripts/`：依赖准备。

项目原创代码采用 [MIT](LICENSE)；第三方组件保留各自许可证。
NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.
