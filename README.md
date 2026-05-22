# 飞书工单机器人 — 项目模板

群里 `@机器人 +bug 描述` → 自动建工单到飞书多维表格，识别优先级 / 模块 / 前后端，落
表后回一条"已记录 + 记录 ID"。

完整搭建指引见上层 `SKILL.md`。本 README 只讲项目本身怎么跑。

## 架构（30 秒看完）

```
[飞书群消息] → poll_chat.py（每 5s 轮询每个白名单群）
            → 过滤：sender_type=user + @mention 命中 bot 名字
            → parser：识别 P0-P3 / 模块 / 前后端
            → upload 图片 → create_issue_record → 群里回 ack
```

通信方式：**轮询**（`im/v1/messages`），不是 webhook。好处是无需公网回调地址。

## 起步

> 要求 Python **3.11 – 3.13**。Python 3.14 pydantic-core 编译不稳定，会装失败。

```bash
python3.12 -m venv .venv         # 或任意 3.11/3.12/3.13
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# 编辑 .env 填入你的 app_id / app_secret / 群 chat_id / Bitable token 和字段 ID

# 跑测试
.venv/bin/python -m pytest tests/ -x

# 启动 worker（前台）
./scripts/start_poll_worker.sh
```

## lark-cli cheatsheet（替代 SKILL.md 里的 curl）

本项目依赖 [lark-cli](https://github.com/larksuite/cli) 简化 setup。装好并 auth 后所有下面的命令都自动管 token：

```bash
brew tap larksuite/cli && brew install lark-cli
lark-cli config init      # 一次性：灌入你的飞书 App ID/Secret 到 keychain
```

### 1. 创建 Bitable 表 schema（推荐：脚本化）

先在飞书 UI 里手动建一个空 Bitable，从 URL 里取 `app_token`（`/base/<token>`）：

```bash
# Wiki 下挂的 Bitable 先解 app_token（URL 是 /wiki/<token>）
lark-cli wiki +node-get --as user \
  --token 'https://your.feishu.cn/wiki/<wiki_token>' \
  -q '.data | {obj_token, obj_type, title}'

# 用 obj_token 作为 APP_TOKEN
python3 scripts/setup_bitable.py <APP_TOKEN>

# 中途失败可断点续跑（跳过已建字段）
python3 scripts/setup_bitable.py <APP_TOKEN> --table-id <table_id> --skip 问题描述 --skip 提出者
```

会自动建出 16 个字段（含状态/目标 Repo/子任务/GitHub Issue URLs 等），结束后打印一坨 `TEAMO_FEISHU_BASE_*_FIELD_ID=` 可直接粘到 `.env`。

### 2. 拿 bot 在哪些群（拿 chat_id 填 ALLOWED_CHAT_IDS）

```bash
# bot 加群后跑（注意：要用 bot 身份，不是用户身份；bot 视角才看得到自己所在的群）
lark-cli im +chat-list --as bot -q '.data.items[] | {chat_id, name}'

# 已知群名，直接搜：
lark-cli im +chat-search --as bot --query "工单测试群" \
  -q '.data.items[] | {chat_id, name}'
```

把目标群的 `chat_id` 加进 `.env` 的 `TEAMO_FEISHU_ALLOWED_CHAT_IDS`（逗号分隔多个）。

### 3. 拿 default_assignee 的 open_id（按姓名反查）

```bash
# search-user 必须用 user 身份
lark-cli contact +search-user --as user --query "张三" \
  -q '.users[] | {name, open_id}'

# 拿到自己的：
lark-cli contact +search-user --as user --user-ids me \
  -q '.users[] | {name, open_id}'
```

填进 `.env` 的 `TEAMO_FEISHU_DEFAULT_ASSIGNEE_ID`。

### 4. 验证字段 ID 都对得上

```bash
lark-cli base +field-list \
  --base-token $TEAMO_FEISHU_BASE_APP_TOKEN \
  --table-id $TEAMO_FEISHU_BASE_TABLE_ID \
  -q '.data.items[] | {field_id, field_name, type}'
```

应该看到 16 个字段，名字、类型跟 `setup_bitable.sh` 里定义的一致。

### 5. 发条测试消息（不用手机也能 dogfood）

```bash
lark-cli im +messages-send --as bot \
  --receive-id <chat_id> --receive-id-type chat_id \
  --msg-type text \
  --content '{"text":"@_user_1 P1 这是一条 lark-cli 发的测试工单"}'
```

如果 worker 在跑，5 秒内应该看到工单落表。

### 6. 偷看一行 Bitable 记录（写完工单后验证字段都填了）

```bash
lark-cli base +record-list \
  --base-token $TEAMO_FEISHU_BASE_APP_TOKEN \
  --table-id $TEAMO_FEISHU_BASE_TABLE_ID \
  --limit 5 --format json \
  -q '.data.items[] | {record_id, fields}'
```

## 部署 (macOS launchd)

```bash
# 编辑 plist 把 REPLACE_USERNAME 和 REPLACE_PROJECT_DIR 替换掉
cp launchd/com.example.ticket-bot.plist ~/Library/LaunchAgents/com.yourcompany.ticket-bot.plist
launchctl load -w ~/Library/LaunchAgents/com.yourcompany.ticket-bot.plist
# 改 .env 后重启
launchctl kickstart -k gui/$(id -u)/com.yourcompany.ticket-bot
# 查日志
tail -f .state/launchd.err.log
```

## 多群路由

一个机器人服务多个群、不同群写不同 sheet：编辑 `.env` 的 `TEAMO_FEISHU_CHAT_ROUTES_JSON`：

```json
{
  "oc_groupA": {"app_token": "DJ...", "table_id": "tbl_a"},
  "oc_groupB": {"app_token": "DJ...", "table_id": "tbl_b", "surfaces": ["web", "server"]}
}
```

没在路由里的群继续走默认 `BASE_APP_TOKEN/BASE_TABLE_ID`。`surfaces` 可选 override，
用来去除某群表里不存在的选项（否则飞书会拒绝写入）。

## 字段映射逻辑

`.env` 配的是**字段 ID**，但写记录走的是**字段名**（运行时调 `/bitable/v1/.../fields`
把 ID 解析成 name）。所以表字段改名不影响写入；字段被删掉才会报错。

不同群的 Bitable 表如果用同一套字段 ID（比如从同一张表"另存为数据表"复制出来的），
代码完全复用，零配置改动。

## 已知大坑（搭新机器人时先看这里）

### 1. open_id 是 App-scoped

同一个用户在不同 App 的视角下 `open_id` 不同。`.env` 里的 `DEFAULT_ASSIGNEE_ID` 必须
是**工单 bot 自己 App 视角**下的 open_id；从 lark-cli (它有自己的 App) 抓出来的
ou_xxx 在工单 bot 视角下无效，写人员字段会报 `1254066: UserFieldConvFail`。

验证某用户在工单 bot 视角下的 open_id：

```bash
.venv/bin/python scripts/probe_sender_id.py
# 在群里发一条 @AMA工单助手 的消息，跑这个会打印消息的 sender.id —— 那个就是
# 该用户在本 bot App 视角下的 open_id。
```

### 2. URL style 字段不接受纯字符串

如果某个文本字段建表时设了 `style.type = url`，写入时必须用
`{"link": "https://...", "text": "显示文字"}` 对象，不能传纯字符串，否则报
`1254068: URLFieldConvFail`。代码里这个转换在 `domain/service.py::_build_fields`
处理。

### 3. 冷启动会 seed 掉所有历史消息

worker 第一次跑且 `.state/poll_seen.json` 不存在时，会把当前群里所有消息标为"已见"
但不处理。这是为了避免重启时反复处理旧消息。

要让 worker 处理某条已存在的消息：删掉 `.state/poll_seen.json` 再用
`--process-existing` 启动一次：

```bash
rm .state/poll_seen.json
./.venv/bin/python -m app.workers.poll_chat --process-existing
```

跑完之后再正常重启，新机制就走"从 state 继续"路径。
