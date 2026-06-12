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

环境要求见上方「速览」表，一般不用提前操心：Python macOS 自带（缺了安装脚本会提示）；`lark-cli` 的安装见下方「准备」；AI 后端三选一，Claude / OpenAI 兼容需要你自己的 API Key，ollama 是本地模型、无需 Key。另有两点请知悉：

- **数据说明**：被汇总的消息内容（及少量上下文）会发送给你所配置的 AI 服务用于分析。介意的话请选 ollama——全程本地处理，不出机器。
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
> 短命令 `lark-listener` 若提示 `command not found`，多半是 PATH 还没刷新，**重开一个终端窗口**即可（安装脚本通常会自动处理 PATH；若仍找不到，按安装结尾的提示操作）。急用就先用完整路径 `~/.lark_listener/venv/bin/lark-listener setup`。
>
> 如果你装了 Claude Code（存在 `~/.claude/` 目录），安装时还会自动把一个操作 skill 写到 `~/.claude/skills/lark-listener/`，让任意 Claude 会话都知道怎么操作本服务；卸载时会一并清理。

最后启动服务：

```bash
lark-listener start
```

看到 Bot 给你发来「✅ LarkListener 已启动…」，就成功了。

## 三、日常使用（直接和 Bot 聊天）

日常使用就是在**飞书里**给你的 LarkListener Bot 发消息（注意：这是在聊天框里发，不是终端命令）：

| 你发给 Bot | 效果 |
|---|---|
| `汇总` / `总结` / `summary` | 立刻汇总一次最近的消息 |
| `汇总最近2小时` | 汇总指定时间范围内的消息 |
| `当前配置` | 查看当前设置 |
| `帮助` | 看看能改哪些设置、怎么改 |
| `轮询间隔改成10分钟` / `关注关键词 上线` / `不要关注 故障` | 用大白话改设置（改完回复「确认」才生效） |

设置改动会在下一次轮询时自动生效，无需重启。其中 `ai` / `notify` / `lark_cli_appid` 受保护，**不能**经 Bot 修改——可手动编辑配置文件，或用 `lark-listener config set <键> <值> --force`（改 `lark_cli_appid` 后需 `lark-listener restart` 才生效）。

全部配置项及说明见 [config.example.yaml](config.example.yaml)（关键词、排除会话、特别关注、AI 分析上下文条数等）。

> Bot 只听你本人（配置里 `notify.user_id`）的指令：其他人私聊它会被静默忽略，不会触发汇总或消耗 AI 调用。

> **不想被定时打扰？** 把轮询间隔设为 **0** 即可关闭自动轮询：服务保持在线，Bot 照常响应「汇总」「改配置」，只是不再定时推送。随时把间隔改回正数即恢复（无论经 Bot 还是 `lark-listener config set` 改，最迟约 10 分钟内被服务感知）。

## 四、会话分类与 @所有人 行为

LarkListener 按飞书「免打扰」状态把群聊分为三类，各类对 @所有人 消息的处理方式不同：

| 类别 | 说明 | @所有人 处理 |
|---|---|---|
| **勿扰群** | 已开启飞书免打扰的群 | 仅当消息命中 `keywords` 关键词时才收录 |
| **普通群** | 未免打扰的群（仅当 `special_focus.enabled=false` 时存在此类型） | @所有人 消息全部收录 |
| **特别关注群** | `special_focus.enabled=true` 时所有未免打扰的群 | 窗口内全量汇总（含未 @你、未命中关键词的消息），每群每轮上限 `special_focus.max_messages`（默认 20 条） |

**特别关注配置说明**：

- `special_focus.enabled` / `special_focus.max_messages` 可用 `config set` 点号路径修改。
- `special_focus.chats`（含每群专属关注关键词）**只能直接编辑** `~/.lark_listener/config.yaml`，CLI 与 Bot 均不支持修改。
- `chats` 中配置的群**不改变其分类**——只是在 AI 分析时叠加专属关注词。
- 若该群被飞书免打扰，关注词也不会生效（`doctor --deep` 会提示）。

```yaml
special_focus:
  enabled: true
  max_messages: 20
  chats:
    - chat_id: oc_xxxxxxxx
      name: 核心团队        # 留空轮询时自动补全
      keywords: [扩容, 发布]  # 叠加给 AI 分析的关注词（不改变分类）
```

**排除会话**（不参与任何汇总）：

```yaml
exclude_chats:
  - chat_id: oc_xxxxxxxx
    name: 某某群            # 留空轮询时自动补全
```

汇总卡片按分类分区展示，顺序为：🟦 私聊 → 🟩 @我 → 🟥 @所有人 → 🟪 特别关注 → 🟧 关键词命中；一条消息同时命中多类时，只归入最靠前的一类。

## 五、命令参考

服务管理：

```bash
lark-listener status     # 查看服务是否在跑（兼诊断面板：进程 + 所有文件位置）
lark-listener stop       # 停止
lark-listener restart    # 重启
lark-listener doctor     # 主动自检，逐项给修复建议（详见「六、出问题了？」）
lark-listener uninstall  # 彻底卸载（删服务、配置与全部数据，会二次确认）
```

查看 / 修改配置（不想开编辑器时）：

```bash
lark-listener config             # 打开配置文件手动编辑
lark-listener config get         # 查看全部配置（api_key 自动脱敏）
lark-listener config get ai.model
lark-listener config set poll_interval 600          # 点号路径直接改
lark-listener config set keywords --add 上线        # 列表项增 / 减用 --add / --remove
lark-listener config set special_focus.enabled true # 开启特别关注
lark-listener config set exclude_chats oc_xxxxxxxx --add   # 排除会话（裸 chat_id，name 轮询时自动补全）
lark-listener config set ai.model xxx --force       # 受保护键（ai/notify/lark_cli_appid）需 --force
# 例外：从 exclude_chats 移除 Bot 自身会话也需 --force（防汇总自反馈）
```

其他：

```bash
# 按需汇总某个时间窗（Unix 秒时间戳；不加 --quiet 会同时推飞书卡片）
lark-listener summarize --start $(date -v-30M +%s) --end $(date +%s)
lark-listener agent-skills install   # 手动安装/卸载 Claude Code 操作 skill
```

> - `lark-listener status` 不只告诉你服务在不在跑，还会列出进程和所有文件位置（配置、日志、venv、launchd、短命令软链），排查问题或确认安装位置时很方便。
> - `status` / `doctor` / `config get` / `summarize` 都支持 `--json`（`summarize` 的 stdout 本身就是 JSON），方便脚本或 AI 助手机读。

## 六、升级到新版本

```bash
~/.lark_listener/venv/bin/pip install --force-reinstall "git+https://github.com/AltairEven/LarkListener.git"
lark-listener restart
```

> 升级后一定要 `restart`，否则跑的还是旧代码。

旧版配置（`exclude_chat_ids`、`include_at_all`）无需手动修改：服务读取时自动兼容，并在首次轮询时把配置文件自动迁移为新格式（`exclude_chats` + 移除废弃键，注释保留）。

## 七、出问题了？

想一次性自检，可跑 `lark-listener doctor`——它会逐项检查配置、`lark-cli`、服务状态、上次轮询时效、AI 后端，并对有问题的项给出修复建议。注意浅检验不出授权过期：**怀疑授权问题或「收不到汇总」时跑 `lark-listener doctor --deep`**，它会真实探测 `search:message` 授权与 AI 后端连通。

或先看日志，多数问题都能在里面找到线索：

```bash
tail -f ~/.lark_listener/logs/stderr.log
```

几种常见情况：

- **拉不到消息**：多半是 `lark-cli` 授权过期，重新跑 `lark-cli auth login --profile <你的 appid> --scope search:message`（appid 见 `lark-listener config get lark_cli_appid`）。
- **Bot 不回消息**：用 `lark-listener status` 看服务在不在跑，不在就 `lark-listener start`。
- **桌面没弹通知**：通知只是次要提醒，汇总仍会通过 Bot 私聊送达，可以忽略。想要更好的体验可以 `brew install terminal-notifier`——装了它之后点击通知能直接跳转到飞书会话。

## 八、卸载

```bash
lark-listener uninstall
```

会停掉服务并删除：launchd 配置（`~/Library/LaunchAgents/com.larklistener.plist`）、短命令软链、数据目录 `~/.lark_listener/`（配置、状态、日志、venv），以及安装时写入的 `~/.claude/skills/lark-listener/`（如有）。执行前有二次确认。

---

开发与测试说明见 [CLAUDE.md](CLAUDE.md)。
