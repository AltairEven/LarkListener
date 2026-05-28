# LarkListener

定时从飞书获取未读消息，AI 分析后通过 Bot 私聊推送汇总 + macOS 通知。

## 安装

```bash
# 前置依赖
npm install -g @nicholaschen/lark-cli
brew install terminal-notifier

# 安装服务
./install.sh
```

## 配置

编辑 `~/.lark_listener/config.yaml`：

```yaml
poll_interval: 300          # 轮询间隔（秒）

keywords:                   # 关注的关键词
  - 部署
  - 故障

ai:
  provider: claude          # claude / openai / ollama
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
  base_url: ""              # openai 兼容或 ollama 地址

notify:
  user_id: ou_xxx           # lark-cli contact +get-user 获取
  bot_chat_id: oc_xxx       # lark-cli im +chat-search 获取
```

## 使用

```bash
# 启动
launchctl load ~/Library/LaunchAgents/com.larklistener.plist

# 停止
launchctl unload ~/Library/LaunchAgents/com.larklistener.plist

# 查看日志
tail -f ~/.lark_listener/logs/stderr.log

# 手动前台运行
python3 -m lark_listener.main
```

## 主动触发

给 bot 发送 **"汇总"**、**"总结"** 或 **"summary"** 可立即触发一次消息汇总。

修改配置后无需重启，下次轮询自动生效。
