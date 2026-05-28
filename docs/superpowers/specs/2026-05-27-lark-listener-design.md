# LarkListener 设计文档

本地后台服务，定时从飞书获取与我相关的未读消息，经 AI 分析后通过 Bot 私聊推送汇总，并弹出 macOS 系统通知。

## 架构总览

```
┌─────────────────────────────────────────────────┐
│                 LarkListener                     │
│                                                  │
│  ┌──────────┐    ┌──────────┐    ┌───────────┐  │
│  │ Scheduler │───▶│ Fetcher  │───▶│ Analyzer  │  │
│  │ (定时器)  │    │(消息获取) │    │(AI 分析)  │  │
│  └──────────┘    └──────────┘    └───────────┘  │
│                                        │         │
│                                        ▼         │
│                  ┌──────────┐    ┌───────────┐  │
│                  │  State   │    │ Notifier   │  │
│                  │(状态管理) │    │(Bot+通知)  │  │
│                  └──────────┘    └───────────┘  │
└─────────────────────────────────────────────────┘
         ▲                               │
         │ lark-cli im +messages-search  │ lark-cli im +messages-send
         ▼                               │ terminal-notifier
    ┌──────────┐                   ┌──────────┐
    │  飞书 API │                   │  你的私聊  │
    └──────────┘                   └──────────┘
```

### 模块职责

- **Scheduler**：按配置间隔（秒级）定时触发轮询循环
- **Fetcher**：调用 lark-cli 获取三类未读消息
- **Analyzer**：调用 AI 模型做相关性判断、紧急度分析、摘要提炼
- **State**：持久化轮询时间戳和已处理消息 ID，避免重复
- **Notifier**：通过 Bot 私聊发送富文本汇总 + macOS 系统通知

## 消息获取策略（Fetcher）

每次轮询执行三组 lark-cli 调用，获取三类消息：

### 第一类：未读私聊

```bash
lark-cli im +messages-search \
  --chat-type p2p \
  --start <上次轮询时间> \
  --end <当前时间> \
  --format json
```

### 第二类：@我 和 @所有人的群消息

```bash
lark-cli im +messages-search \
  --is-at-me \
  --chat-type group \
  --start <上次轮询时间> \
  --end <当前时间> \
  --format json
```

### 第三类：关键词匹配的群消息

对配置的每个关键词分别搜索：

```bash
lark-cli im +messages-search \
  --query <关键词> \
  --start <上次轮询时间> \
  --end <当前时间> \
  --format json
```

### 去重逻辑

- 三类结果按 message_id 去重
- 优先级：私聊 > @我/@所有人 > 关键词匹配
- 同一条消息只保留最高优先级分类

### "只看未读"的实现

飞书 API 不提供"我是否已读"字段，通过时间窗口近似实现：

- `--start` 设为上次轮询时间戳，`--end` 设为当前时间
- State 模块持久化时间戳，进程重启不丢失

## AI 分析模块（Analyzer）

### 分析任务

对获取到的消息统一送入 AI，一次调用完成：

1. **相关性判断**：与关键词的语义相关度（high / medium / low）
2. **紧急度分析**：urgent / normal / low
3. **摘要提炼**：一句话提炼核心内容

### Prompt 结构

```
你是消息分析助手。用户关注的关键词：{keywords}

请对以下消息进行分析，输出 JSON：
1. 相关性：与关键词的语义相关度（high/medium/low）
2. 紧急度：urgent / normal / low
3. 摘要：一句话提炼核心内容

消息列表：
{messages}
```

### 模型后端可配置

| provider | 说明 | SDK |
|----------|------|-----|
| `claude` | Anthropic API | anthropic |
| `openai` | OpenAI 兼容 API（含 deepseek 等） | openai |
| `ollama` | 本地 Ollama | HTTP 调用 |

Analyzer 统一封装，上层通过 `ai.provider` 切换，对调用方透明。

### 优化策略

- 没有新消息时跳过 AI 调用
- 私聊和 @我 的消息默认 high 相关性，只做紧急度分析和摘要
- 关键词匹配的消息做完整的相关性 + 紧急度 + 摘要分析

## 汇总推送（Notifier）

### 飞书 Bot 消息

通过 bot 身份发送富文本消息：

```bash
lark-cli im +messages-send \
  --user-id <open_id> \
  --msg-type post \
  --content <富文本> \
  --as bot
```

消息格式：

```
📬 LarkListener 消息汇总（15:00 - 15:30）

━━ 私聊消息（3 条）━━
🔴 [紧急] 张三：线上服务报错了，能看下吗
     ➜ 线上故障求助  👉 查看原文
⚪ 李四：明天下午的会议改到3点
     ➜ 会议时间变更通知  👉 查看原文

━━ @我 / @所有人（2 条）━━
🔴 [紧急] 王五 @ 技术群：@所有人 紧急发版
     ➜ 要求全员关注紧急发版  👉 查看原文
⚪ 赵六 @ 项目群：@你 PR 帮忙 review 下
     ➜ 请求代码审查  👉 查看原文

━━ 关键词命中（1 条）━━
⚪ 钱七 @ 运维群：部署流水线挂了（命中：部署）
     ➜ CI/CD 流水线故障，相关度 high  👉 查看原文
```

- 每条消息末尾有「查看原文」超链接，跳转到飞书消息上下文
- 链接格式：`https://applink.feishu.cn/client/message/link/open?body={"token":"<message_id>"}`
- 紧急消息排在每类最前面
- 没有新消息时不推送

### macOS 系统通知

推送的同时弹出系统通知：

```bash
terminal-notifier \
  -title "LarkListener" \
  -subtitle "有新消息汇总" \
  -message "3条私聊、2条@我、1条关键词命中" \
  -open "https://applink.feishu.cn/client/chat/open?openChatId=<bot_chat_id>"
```

- 点击通知跳转到飞书 Bot 聊天窗口
- 通知内容简要显示各类消息数量
- 只在有新消息时弹通知

## 配置管理

所有文件集中在 `~/.lark_listener/`：

```
~/.lark_listener/
├── config.yaml           # 配置文件
├── state.json            # 轮询状态
└── logs/
    ├── stdout.log        # 标准输出
    └── stderr.log        # 错误日志
```

### config.yaml

```yaml
# 轮询间隔（秒）
poll_interval: 300

# 关注的关键词
keywords:
  - 部署
  - 故障
  - 发版

# AI 模型配置
ai:
  provider: claude        # claude / openai / ollama
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY  # 从环境变量读取，不存明文
  base_url: ""            # openai 兼容或 ollama 地址，留空用默认

# 推送目标
notify:
  user_id: ou_xxxxxxxxxxxx        # 你的 open_id
  bot_chat_id: oc_xxxxxxxxxxxx    # Bot 聊天的 chat_id（用于通知跳转）
```

- 配置文件变更无需重启，每次轮询时重新读取
- API key 通过环境变量引用，不存明文

### state.json

```json
{
  "last_poll_time": "2026-05-27T15:30:00+08:00",
  "processed_message_ids": ["msg_001", "msg_002"]
}
```

- `processed_message_ids` 保留最近 1000 条，作为去重的二次保障

## 进程管理（launchd）

plist 文件位于 `~/Library/LaunchAgents/com.larklistener.plist`（macOS 要求）。

行为：
- 登录后自动启动
- 进程崩溃后自动重启（间隔 10 秒）
- 日志输出到 `~/.lark_listener/logs/`

日常管理命令：

```bash
# 启动
launchctl load ~/Library/LaunchAgents/com.larklistener.plist

# 停止
launchctl unload ~/Library/LaunchAgents/com.larklistener.plist

# 查看状态
launchctl list | grep larklistener
```

## 项目文件结构

```
LarkListener/
├── lark_listener/
│   ├── __init__.py
│   ├── main.py          # 入口，Scheduler 循环
│   ├── config.py         # 配置加载
│   ├── fetcher.py        # 调用 lark-cli 获取消息
│   ├── analyzer.py       # AI 分析（多后端）
│   ├── notifier.py       # Bot 推送汇总 + 系统通知
│   └── state.py          # 状态持久化
├── config.example.yaml   # 配置模板
├── install.sh            # 一键安装脚本（创建目录、安装 plist）
├── requirements.txt      # Python 依赖（pyyaml, anthropic, openai）
└── README.md
```

## 外部依赖

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| lark-cli | 飞书 API 调用 | `npm install -g @nicholaschen/lark-cli` |
| terminal-notifier | macOS 系统通知 | `brew install terminal-notifier` |
| pyyaml | YAML 配置解析 | pip |
| anthropic | Claude API（可选） | pip |
| openai | OpenAI 兼容 API（可选） | pip |
