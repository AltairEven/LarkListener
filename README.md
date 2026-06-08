# LarkListener

让飞书 Bot 帮你盯消息：定时把你的飞书未读消息收集起来，用 AI 理一遍，再通过 Bot 私聊把**重点汇总**发给你，并弹一个 macOS 桌面通知。不用一直盯着飞书，也不会漏掉重要消息。

> **At a glance** — macOS 后台常驻服务（launchd）：定时 `lark-cli` 拉未读 → AI 分析 → Bot 私聊推汇总 + 桌面通知。
>
> | | |
> |---|---|
> | **Platform** | macOS only（依赖 launchd 后台服务 + osascript 通知，暂不支持 Windows / Linux） |
> | **Runtime deps** | Python ≥ 3.9 · `lark-cli`（含 Node.js）· 一个 AI 后端：Claude / OpenAI 兼容（如 DeepSeek）/ 本地 ollama |
> | **两类界面** | ① **CLI**（终端跑 `lark-listener <cmd>`，装/管服务）② **Bot 聊天**（在飞书里给 Bot 发自然语言，日常使用与改配置） |
> | **数据目录** | `~/.lark_listener/`（`config.yaml` · `state.json` · `logs/` · `venv/`） |
> | **日志** | `~/.lark_listener/logs/stderr.log` |
> | **launchd** | `~/Library/LaunchAgents/com.larklistener.plist` |

> 你是来帮用户安装/操作这个工具的 AI 助手？直接看文末 [**给 AI 助手（For AI assistants）**](#给-ai-助手for-ai-assistants)。

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

装好后，运行配置向导（**交互式**，跟着提问填即可：选 Bot、轮询间隔、关心的关键词、AI 模型等）：

```bash
lark-listener setup
```

> **第一项就是「用哪个 Bot」**——填承载服务的 `lark-cli` **appId（`cli_xxx`）**。不确定有哪些可选，先跑 `lark-cli profile list` 看一眼。这一步选错，服务就会挂到错误的 Bot 上。
>
> **如果是 AI 助手在帮你装**：`setup` 是交互式的，需要**你本人**来跑（在对话框里输入 `! lark-listener setup`），AI 不能替你闷头跑；并且务必先想好要用哪个 appId。详见 [AGENTS.md](AGENTS.md)。

> 短命令 `lark-listener` 若提示 `command not found`，多半是 PATH 还没刷新——**重开一个终端窗口**即可（安装时已自动把它加入 PATH）。急用可先用完整路径 `~/.lark_listener/venv/bin/lark-listener setup`。

最后启动服务：

```bash
lark-listener start
```

看到 Bot 给你发来「✅ 已启动」就成功了。

## 三、日常使用（直接和 Bot 聊天）

给你的 LarkListener Bot 发消息即可（这是在**飞书里**发，不是终端命令）：

| 你发给 Bot | 效果 |
|---|---|
| `汇总` / `总结` / `summary` | 立刻汇总一次最近的消息 |
| `汇总最近2小时` | 汇总指定时间范围 |
| `当前配置` | 查看当前设置 |
| `帮助` | 查看能改哪些设置、怎么改 |
| `轮询间隔改成10分钟` / `关注关键词 上线` / `不要关注 故障` | 用大白话改设置（改完回复「确认」生效） |

设置改动会在下一次轮询自动生效，无需重启。`ai` / `notify` / `lark_cli_appid` 受保护，**不能**经 Bot 改，需手动编辑配置文件。

## 四、管理服务

```bash
lark-listener status     # 看服务在不在跑（兼诊断面板：进程 + 文件位置）
lark-listener stop       # 停止
lark-listener restart    # 重启
lark-listener config     # 打开配置文件手动编辑（GUI 文本编辑器）
lark-listener uninstall  # 彻底卸载（删服务、配置、全部数据，会二次确认）
```

> `lark-listener status` 不只看运行状态，还会列出进程和所有文件位置（配置、日志、venv、launchd、短命令软链），排查或确认安装位置时很方便。

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

## 给 AI 助手（For AI assistants）

If you're an AI agent (Claude Code, etc.) helping a user install or operate LarkListener, read this section first. It states what's safe to run yourself, what needs the human, and the exact contract of each surface.

> **📋 Canonical agent contract: [AGENTS.md](AGENTS.md)** — read it before running anything.
>
> **The one input you must get from the user: the bot appId (`cli_xxx`).** LarkListener runs *as* a specific Lark bot. Confirm which one with `lark-cli profile list`, then have the user pick — don't assume the active profile. Getting this wrong points the service at the wrong bot.

### Two interaction surfaces — don't conflate them

- **CLI** — `lark-listener <command>` in a terminal. Used to **install and manage the service**.
- **Bot chat** — natural-language messages the user sends to the LarkListener Bot **inside Feishu/Lark**. Used for **daily use and config edits**. These are *not* shell commands; don't run them in Bash. (If you drive the user's Lark via `lark-cli` / a lark-im tool, you *could* send these as bot messages — but the recipient is the bot, in chat.)

### ⚠️ Interactive commands — do NOT run unattended

These block on stdin / open a GUI and will hang or stall a non-interactive shell. Hand them to the user (e.g. suggest they type `! lark-listener setup` so it runs in their session):

- **`lark-listener setup`** — interactive wizard; **have the user run it** (`! lark-listener setup`). Make sure they know **which bot appId (`cli_xxx`)** to use first — run `lark-cli profile list` and let them pick. For a fresh config it prompts, in order:
  1. **Bot appId（`cli_xxx`）← 最关键**：检测到 active bot 时问 `使用它？(Y/n)`，否则要求输入 appId。**不要默认就用 active 的，先和用户确认。**
  2. `轮询间隔（秒，默认 300）`
  3. `关注的关键词（逗号分隔，可空）`
  4. AI 后端 `1) openai  2) claude  3) ollama`（默认 1）
  5. `模型名称`（如 `gpt-4o` / `claude-sonnet-4-6` / `qwen2.5:7b`）
  6. `API Key`（ollama 可空）
  7. `API Base URL`（留空用默认）
  8. `user_id`：自动获取，失败则要求手输 `ou_xxx`
  9. `bot_chat_id`：自动获取（向用户发一条测试消息），失败则手输 `oc_xxx`
  10. 授权：缺 `search:message` scope 时问 `现在发起授权登录？(Y/n)`（会开浏览器）
- **`lark-listener uninstall`** — prompts `确认卸载？(y/N)` then deletes the service, plist, shim and `~/.lark_listener`.
- **`lark-listener config`** — runs `open -t config.yaml` (opens a GUI editor). To read/edit programmatically, touch `~/.lark_listener/config.yaml` directly instead.

### ✅ Safe / non-interactive commands

- **`lark-listener status`** — the diagnostic entry point; prints service state + main/listener PIDs + every file location with ✓/—. **Start here when diagnosing.**
- **`lark-listener start` / `stop` / `restart`** — non-interactive; manage the launchd service.

### Deterministic setup flow

```text
1. Prereqs (user):  npm i -g @larksuite/cli && lark-cli config init
                    lark-cli auth login --scope search:message
2. Install:         curl -fsSL .../install.sh | bash        # creates venv, installs, links short command
3. Configure:       lark-listener setup                     # INTERACTIVE → user runs it
4. Start:           lark-listener start                     # bot DMs "✅ 已启动" on success
5. Trigger:         user messages the Bot 「汇总」 in Feishu
```

### Command reference

| Command | Interactive? | Effect |
|---|---|---|
| `lark-listener setup` | **Yes** (prompts) | Install wizard: pick bot, set interval/keywords/AI, write launchd plist, guide auth |
| `lark-listener start` | No | Load the launchd service (idempotent: unloads then loads) |
| `lark-listener stop` | No | Unload the service + reap listener subprocess |
| `lark-listener restart` | No | `stop` then `start`（升级或改代码后必须） |
| `lark-listener status` | No | Diagnostic panel: state + PIDs + file locations |
| `lark-listener config` | **Yes** (GUI editor) | `open -t ~/.lark_listener/config.yaml` |
| `lark-listener uninstall` | **Yes** (`y/N`) | Remove service, plist, shim, `~/.lark_listener` |
| `lark-listener run` | No (internal) | The daemon loop — launchd calls this; don't run by hand |

### Bot natural-language intents (sent in Feishu, not the shell)

| Send to Bot | Intent |
|---|---|
| `汇总` / `总结` / `summary` | Summarise recent messages now |
| `汇总最近2小时` / `汇总今天上午` | Summarise a custom time range |
| `当前配置` | Show editable config (secrets hidden) |
| `帮助` | Show what's editable and how |
| `轮询间隔改成10分钟` | Set a scalar field (then reply `确认`) |
| `关注关键词 上线` / `不要关注 故障` | Add / remove a list item (then reply `确认`) |
| `确认` / `取消` | Confirm / cancel a pending config change |

> Protected fields `ai` / `notify` / `lark_cli_appid` are **rejected** over chat by design — edit `~/.lark_listener/config.yaml` and the next poll picks it up (no restart needed for config; restart only for code upgrades).

### Diagnostics

- `lark-listener status` — service state, PIDs, file locations.
- `tail -n 100 ~/.lark_listener/logs/stderr.log` — most failures leave a trace here.
- Can't fetch messages → `lark-cli` auth expired → `lark-cli auth login --scope search:message`.
- Bot silent → check `status`; if not running → `lark-listener start`.

开发与测试说明见 [CLAUDE.md](CLAUDE.md)。
