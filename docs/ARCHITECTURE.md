# 架构与 API

规划器一次发出一个工具调用。客户端将持续动作放入任务引擎，规划器等待真实结果，再读取状态并决定下一步。
持续跟随占用移动槽；聊天和可选服务器命令使用即时通道。停止消息走独立抢占通道。

| 接口 | 用途 |
| --- | --- |
| GET /health | 运行与连接状态 |
| GET /v1/state | 感知快照 |
| GET /v1/capabilities | 实际可用工具与参数 |
| POST /v1/tools | 提交工具调用 |
| GET /v1/tasks | 查询任务 |
| GET /v1/tasks/{id} | 查询单个任务 |
| POST /v1/tasks/{id}/pause、resume、cancel | 控制任务 |
| GET /v1/memory/containers | 容器记忆 |

接口默认仅监听 loopback 的 8766 端口。客户端与规划器使用相同 WIFE_NG_TOKEN；请求头为 Authorization: Bearer <token>。
WIFE_NG_PORT 修改客户端端口，WIFE_NG_API 修改规划器目标地址，两者需要对应。

```powershell
$headers = @{ Authorization = "Bearer $env:WIFE_NG_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8766/v1/capabilities -Headers $headers
$body = @{tool='goto'; arguments=@{x=10; y=65; z=10}} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8766/v1/tools -Method Post -Headers $headers -ContentType application/json -Body $body
```

创造建筑采用区域扫描 → 蓝图与布局校验 → 分批施工 → 独立逐格验证。未加载的格不能被当成已经验证的格，停止的任务不能被当成施工成功。
数据保存在游戏目录的 wife-ng/ 和规划器的 headlessmc/architecture/，包含世界坐标、聊天或模型记录，均不提交 Git。
WIFE_NG_WORLD_ID 应对不同服务器/世界设置不同值，防止复用错误的规划记忆。
