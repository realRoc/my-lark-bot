# Daily API Monitor SCF

腾讯云 SCF 定时采集 AMA 二维码报警率、Router 折扣率和 Router 请求成功率，并发送飞书交互卡片。

运行时：Python 3.10，入口：`index.main_handler`。

定时触发器使用北京时间 `08:50` 至 `22:50`，对应 SCF cron：

```
0 50 8-22 * * * *
```

所需环境变量：`AMA_BASE_URL`、`AMA_API_KEY`、`ROUTER_BASE_URL`、`ROUTER_USERNAME`、`ROUTER_PASSWORD`、`FEISHU_WEBHOOK_URL`、`ASKMANY_BASE_URL`、`ASKMANY_USERNAME`、`ASKMANY_PASSWORD`。

AskManyAI TTFT 探测每次运行都会通过 `/api/oauth2/token` 自动获取新的 `Authorization` Cookie，再并发调用 `/api/engine/sseQuery`；不保存手工 Cookie 或 JWT。

Router 折扣率默认按模型族判断；`gpt-5.6-luna` 使用模型级 0.40 告警阈值，其他 GPT 模型保持族级阈值。
