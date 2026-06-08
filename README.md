# LarkListener

让飞书 Bot 帮你盯消息：定时把你的飞书未读消息收集起来，用 AI 理一遍，再通过 Bot 私聊把**重点汇总**发给你，并弹一个 macOS 桌面通知。不用一直盯着飞书，也不会漏掉重要消息。

---

## 运行环境

- **系统：macOS**（用到 macOS 的后台服务与通知机制，暂不支持 Windows / Linux）。
- **Python 3.9 及以上**：macOS 一般自带，通常无需自己装（缺了安装时会提示）。
- **飞书 `lark-cli`**（含 Node.js）：本工具靠它收发飞书消息，见下方「准备」。
- **一个 AI 服务**（三选一）：Claude / OpenAI 兼容接口（如 DeepSeek）/ 本地 ollama。前两者需要你自己的 API Key；ollama 是本地模型、无需 Key。
- **保持开机联网**：它是后台常驻服务，电脑开着、能上网时才会持续帮你盯消息。

## 一、准备（只需一次）

本工具依赖飞书官方命令行 `lark-cli`。如果你还没装，复制下面三行到「终端」依次回车：

```bash
npm install -g @larksuite/cli
lark-cli config init
lark-cli auth login --scope search:message
```

> 没有 `npm`？它来自 Node.js，到 https://nodejs.org 下载安装即可。

## 二、安装

复制这一行到终端回车，按提示走完即可（不会弹「无法验证开发者」之类的安全警告）：

```bash
curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh | bash
```

装好后，运行配置向导（跟着提问填即可：选 Bot、轮询间隔、关心的关键词、AI 模型等）：

```bash
lark-listener setup
```

> 短命令 `lark-listener` 若提示 `command not found`，多半是 PATH 还没刷新——**重开一个终端窗口**即可（安装时已自动把它加入 PATH）。急用可先用完整路径 `~/.lark_listener/venv/bin/lark-listener setup`。

最后启动服务：

```bash
lark-listener start
```

看到 Bot 给你发来「✅ 已启动」就成功了。

## 三、日常使用（直接和 Bot 聊天）

给你的 LarkListener Bot 发消息即可：

| 你发给 Bot | 效果 |
|---|---|
| `汇总` / `总结` / `summary` | 立刻汇总一次最近的消息 |
| `汇总最近2小时` | 汇总指定时间范围 |
| `当前配置` | 查看当前设置 |
| `帮助` | 查看能改哪些设置、怎么改 |
| `轮询间隔改成10分钟` / `关注关键词 上线` | 用大白话改设置（改完回复「确认」生效） |

设置改动会在下一次轮询自动生效，无需重启。

## 四、管理服务

```bash
lark-listener status     # 看服务在不在跑
lark-listener stop       # 停止
lark-listener restart    # 重启
lark-listener config     # 打开配置文件手动编辑
lark-listener uninstall  # 彻底卸载（删服务、配置、全部数据）
```

## 五、升级到新版本

```bash
~/.lark_listener/venv/bin/pip install --force-reinstall "git+https://github.com/AltairEven/LarkListener.git"
lark-listener restart
```

> 升级后必须 `restart`，否则跑的还是旧版本。

## 六、出问题了？

先看日志，多数问题日志里都有线索：

```bash
tail -f ~/.lark_listener/logs/stderr.log
```

常见情况：
- **拉不到消息**：多半是 `lark-cli` 授权过期，重跑 `lark-cli auth login --scope search:message`。
- **Bot 不回消息**：`lark-listener status` 看服务是否在跑；不在就 `lark-listener start`。
- **桌面没有通知**：通知是次要提醒，汇总仍会通过 Bot 私聊送达，可忽略。

---

开发与测试说明见 [CLAUDE.md](CLAUDE.md)。
