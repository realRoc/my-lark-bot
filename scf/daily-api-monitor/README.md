# Daily API Monitor SCF

腾讯云 SCF 定时采集 AMA 二维码报警率、Router 折扣率和 Router 请求成功率，并发送飞书交互卡片。

Router 请求统一携带 `Cache-Control: no-cache`，避免前置 HTTP 缓存返回旧窗口。请求成功率直接读取 `/channel-monitoring?refresh=1` 的 30 分钟汇总；由于看板的 live-span 分布式缓存可能先返回旧值再后台刷新，监控会短暂轮询同一个精确窗口，并严格校验 API 回显的起止时间。AMA 同样校验 `metadata.time_range`。任何最终仍错窗或缺少窗口回显的情况都会转成“采集失败”告警，不再把旧窗口数值当成当前窗口指标。Router 折扣率 API 当前仅回显日期，因此卡片使用本次请求的精确时间窗，避免把日期回显误写成统计范围。

运行时：Python 3.10，入口：`index.main_handler`。

定时触发器使用北京时间 `08:50` 至 `22:50`，对应 SCF cron：

```
0 50 8-22 * * * *
```

所需环境变量：`AMA_BASE_URL`、`AMA_API_KEY`、`ROUTER_BASE_URL`、`ROUTER_USERNAME`、`ROUTER_PASSWORD`、`FEISHU_WEBHOOK_URL`、`ASKMANY_BASE_URL`、`ASKMANY_USERNAME`、`ASKMANY_PASSWORD`。

dev 部署可设置 `FUNCTION_NAME=daily-api-monitor-dev DRY_RUN=1`。`DRY_RUN` 仍执行所有采集，但不会向飞书发送卡片；dev 函数不配置定时触发器。

AskManyAI TTFT 探测每次运行都会通过 `/api/oauth2/token` 自动获取新的 `Authorization` Cookie，再并发调用 `/api/engine/sseQuery`；不保存手工 Cookie 或 JWT。

Router 折扣率默认按模型族判断；`gpt-5.6-luna` 使用模型级 0.40 告警阈值，其他 GPT 模型保持族级阈值。
