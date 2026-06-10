# LarkListener

让飞书 Bot 帮你盯消息：定时把你的飞书未读消息收集起来，用 AI 理一遍，再通过 Bot 私聊把**重点汇总**发给你，并弹一个 macOS 桌面通知。不用一直盯着飞书，也不会漏掉重要消息。

> ## 🤖 想让 AI 助手帮你装？把 [AGENTS.md](AGENTS.md) 交给它
>
> 如果你打算让 Claude Code 这类 AI 助手帮你安装或操作，**直接让它读仓库里的 [AGENTS.md](AGENTS.md)**（raw 地址可直接抓取：`https://raw.githubusercontent.com/AltairEven/LarkListener/main/AGENTS.md`）——那是专门写给 AI 的完整安装/操作契约，**无需 clone 整个仓库**，按它一步步来即可。
>
> 想自己动手装？继续往下看就行。

> **速览** — macOS 后台常驻服务（launchd）：定时用 `lark-cli` 拉未读 → AI 分析 → Bot 私聊推汇总 + 桌面通知。
>
> | | |
> |---|---|
> | **系统** | 仅 macOS（依赖 launchd 后台服务 + osascript 通知，暂不支持 Windows / Linux） |
> | **依赖** | Python ≥ 3.9 · `lark-cli`（含 Node.js）· 一个 AI 后端：Claude / OpenAI 兼容（如 DeepSeek）/ 本地 ollama |
> | **两类界面** | ① CLI（终端跑 `lark-listener <命令>`，装和管服务）② Bot 聊天（在飞书里给 Bot 发自然语言，日常使用与改配置） |
> | **数据目录 / 日志** | `~/.lark_listener/` · `~/.lark_listener/logs/stderr.log` |

---

## 运行环境

- **系统是 macOS**：用到了 macOS 的后台服务（launchd）与桌面通知机制，暂不支持 Windows / Linux。
- **Python 3.9 及以上**：macOS 一般自带，通常无需自己装（缺了安装脚本会提示）。
- **飞书 `lark-cli`（含 Node.js）**：本工具靠它收发飞书消息，安装方式见下方「准备」。
- **一个 AI 服务（三选一）**：Claude、OpenAI 兼容接口（如 DeepSeek）、或本地 ollama。前两者要填你自己的 API Key；ollama 是本地模型，无需 Key。
- **保持开机联网**：它是常驻后台服务，电脑开着、能上网时才会持续帮你盯消息。

## 一、准备（只需一次）

本工具依赖飞书官方命令行 `lark-cli`。如果还没装，把下面三行复制到「终端」依次回车：

```bash
npm install -g @larksuite/cli
lark-cli config init
lark-cli auth login --scope search:message
```

> 没有 `npm`？它随 Node.js 一起来，到 https://nodejs.org 下载安装即可。

## 二、安装

把这一行复制到终端回车，按提示走完即可（过程中不会弹「无法验证开发者」之类的安全警告）：

```bash
curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh | bash
```

装好后运行配置向导。它是**交互式**的，会逐项问你：用哪个 Bot、轮询间隔、关心的关键词、AI 模型等：

```bash
lark-listener setup
```

> **第一项「用哪个 Bot」最关键**：填承载服务的 `lark-cli` **appId（`cli_xxx`）**。不确定有哪些可选，先跑 `lark-cli profile list` 看一眼——这一步选错，服务就会挂到错误的 Bot 上。
>
> 短命令 `lark-listener` 若提示 `command not found`，多半是 PATH 还没刷新，**重开一个终端窗口**即可（安装时已自动把它加进 PATH）。急用就先用完整路径 `~/.lark_listener/venv/bin/lark-listener setup`。

最后启动服务：

```bash
lark-listener start
```

看到 Bot 给你发来「✅ 已启动」，就成功了。

## 三、日常使用（直接和 Bot 聊天）

日常使用就是在**飞书里**给你的 LarkListener Bot 发消息（注意：这是在聊天框里发，不是终端命令）：

| 你发给 Bot | 效果 |
|---|---|
| `汇总` / `总结` / `summary` | 立刻汇总一次最近的消息 |
| `汇总最近2小时` | 汇总指定时间范围内的消息 |
| `当前配置` | 查看当前设置 |
| `帮助` | 看看能改哪些设置、怎么改 |
| `轮询间隔改成10分钟` / `关注关键词 上线` / `不要关注 故障` | 用大白话改设置（改完回复「确认」才生效） |

设置改动会在下一次轮询时自动生效，无需重启。其中 `ai` / `notify` / `lark_cli_appid` 受保护，**不能**经 Bot 修改，需要手动编辑配置文件。

> **不想被定时打扰？** 把轮询间隔设为 **0** 即可关闭自动轮询：服务保持在线，Bot 照常响应「汇总」「改配置」，只是不再定时推送。随时把间隔改回正数即恢复（经 Bot 改立即生效；用 `lark-listener config set` 改最迟约 10 分钟生效）。

## 四、管理服务

```bash
lark-listener status     # 查看服务是否在跑（兼诊断面板：进程 + 所有文件位置）
lark-listener stop       # 停止
lark-listener restart    # 重启
lark-listener config     # 打开配置文件手动编辑
lark-listener uninstall  # 彻底卸载（删服务、配置与全部数据，会二次确认）
```

> `lark-listener status` 不只告诉你服务在不在跑，还会列出进程和所有文件位置（配置、日志、venv、launchd、短命令软链），排查问题或确认安装位置时很方便。

## 五、升级到新版本

```bash
~/.lark_listener/venv/bin/pip install --force-reinstall "git+https://github.com/AltairEven/LarkListener.git"
lark-listener restart
```

> 升级后一定要 `restart`，否则跑的还是旧代码。

## 六、出问题了？

想一次性自检，可跑 `lark-listener doctor`——它会逐项检查配置、`lark-cli` 授权、服务状态、上次轮询时效、AI 后端，并对有问题的项给出修复建议。

或先看日志，多数问题都能在里面找到线索：

```bash
tail -f ~/.lark_listener/logs/stderr.log
```

几种常见情况：

- **拉不到消息**：多半是 `lark-cli` 授权过期，重新跑 `lark-cli auth login --scope search:message`。
- **Bot 不回消息**：用 `lark-listener status` 看服务在不在跑，不在就 `lark-listener start`。
- **桌面没弹通知**：通知只是次要提醒，汇总仍会通过 Bot 私聊送达，可以忽略。

---

开发与测试说明见 [CLAUDE.md](CLAUDE.md)。
