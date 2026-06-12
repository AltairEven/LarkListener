---
name: lark-listener
description: 管理、诊断并按需汇总本机 LarkListener 飞书消息汇总服务。当用户要汇总/总结最近一段时间的飞书消息（如「汇总最近半小时/1小时的消息」「总结一下最近的未读」），或提到 LarkListener / 飞书汇总服务 / 飞书消息总结 bot，或要启动/停止/重启、查看状态、改配置、排查「不工作/收不到汇总/bot 不回」等运行时问题时使用。
---

# 操作 LarkListener（macOS 后台服务）

LarkListener 是装在本机的 launchd 后台服务：定时拉飞书未读 → AI 汇总 → bot 私聊推送。
本 skill 教你（AI）安装后如何安全操作它。**`lark-listener --help` 与 `lark-listener doctor`
是契约的唯一事实源——本文若与其冲突，以命令输出为准。**

## 按需汇总（用户说「汇总最近 X / 总结最近的消息」时）
这是用 CLI 触发汇总的正路——**用 `lark-listener summarize`，不要改用 lark-im 去搜消息**：
```bash
# 「最近 30 分钟」：用 date 算起止 Unix 秒时间戳（macOS）
lark-listener summarize --start $(date -v-30M +%s) --end $(date +%s) --quiet
# 「最近 2 小时」用 -v-2H，「最近 1 天」用 -v-1d，以此类推
```
- `--start` / `--end` 均必填，**Unix 秒时间戳**；「最近 N 分钟/小时」= `date -v-NM/-NH +%s` 算起点、`date +%s` 算终点。
- **stdout 是统一 JSON 封套 `{code, errorMsg, data}`**（成功/空/错误都是合法 JSON，退出码＝`code`）：
  - `code: 0` 成功，`data.conversations` 为会话数组（空数组＝该时间窗没有可汇总内容）；
  - `code != 0` 出错，看 `errorMsg`。**先 `json.loads` 再判 `code`，不要把 stdout 当人读文本转发**；
    给用户转述时用 `data` 里的 `title`/`summary`/`snippet`/`link` 自行组织。
- 你（AI）自己要拿结果看就加 `--quiet`（只回 stdout，不打扰用户）；想让用户也收到，就去掉 `--quiet`（推飞书交互卡片 + 弹 macOS 桌面通知）。
- 只读：不写 state、不影响正在跑的定时轮询，可随时跑。

## 先诊断
排查任何问题，先跑（机读）：
```bash
lark-listener doctor --json     # 主动自检：config/服务/lark-cli 授权/轮询时效/日志/AI 后端/特别关注配置
lark-listener status --json     # 服务三态 + 进程 PID + 文件位置 + 上次轮询
```
`doctor` 每项带 `fix` 字段，直接给修复命令。退出码：status 0=运行/3=停/4=未装；doctor 0=全过/1=有 fail。

## ✅ 可直接（非交互）运行
- `lark-listener summarize --start <epoch> --end <epoch> [--quiet]` — 按需汇总某时间窗，stdout 为 JSON 封套（见上「按需汇总」）
- `lark-listener start | stop | restart` — 服务控制
- `lark-listener status [--json]` / `lark-listener doctor [--json] [--deep]`
- `lark-listener config get [KEY] [--json]` — 查看配置（api_key 已脱敏）
- `lark-listener config set KEY VALUE [--add|--remove] [--force]` — 改配置，无需重启：
  轮询开启时下次轮询生效；`poll_interval=0`（自动轮询已关闭）时最迟约 10 分钟被服务感知
  - 点号路径：`poll_interval`、`keywords`、`ai.model`、`notify.user_id`、`lark_cli_appid`、`special_focus.enabled`、`special_focus.max_messages` 等
  - `poll_interval` 为非负整数秒，**0 = 关闭自动轮询**（服务保持在线，仅 bot 按需汇总/改配置）
  - 列表：整体 `config set keywords a,b`；增 `--add`；减 `--remove`
  - `exclude_chats` 的 `--add`/`--remove` 值为裸 chat_id（如 `oc_xxx`），name 由服务自动补全
  - `special_focus.chats`（含每群专属关注关键词）**不可经 CLI/bot 修改**，需直接编辑 `~/.lark_listener/config.yaml`；`special_focus.enabled`/`special_focus.max_messages` 可经点号路径修改
  - 受保护项（`ai`/`notify`/`lark_cli_appid`）需 `--force`
  - **例外：从 `exclude_chats` 移除 Bot 自身会话也会被拒**，确需移除加 `--force`（防汇总自反馈）
  - **例外：改 `lark_cli_appid` 后需 `lark-listener restart` 才生效**（bot 监听子进程按启动时的 profile 订阅）
- `lark-listener agent-skills install|uninstall`

## 🚫 不要无人值守运行（交互输入 / 弹 GUI；EOF/Ctrl-C 会干净取消并退出码 1）
- `lark-listener setup`（交互向导）、`lark-listener uninstall`（二次确认）、
  `lark-listener config`（无参=开 GUI 编辑器）——交给用户在自己终端跑。

## 会话分类（`summarize` 返回的 `category` 枚举）

`data.conversations[].category` 取值及含义（卡片显示顺序与此一致）：

| category | 含义 |
|---|---|
| `p2p` | 私聊 |
| `at_me` | 群内 @我 |
| `at_all` | @所有人（未免打扰的非特别关注群全收；勿扰群仅命中关键词才收） |
| `special` | 特别关注群（`special_focus.enabled=true` 且群未免打扰；全量汇总） |
| `keyword` | 关键词命中（勿扰群/普通群均适用） |

勿扰群 = 飞书「免打扰」开启的群；特别关注群 = `special_focus.enabled=true` 时所有未免打扰的群（与是否在 `special_focus.chats` 中无关）；普通群 = 未免打扰的群（仅 `special_focus.enabled=false` 时存在）。`special_focus.chats` 只为单个群叠加专属关注关键词（仅影响 AI 分析侧重），不改变分类。

## 常见修复
- 浅检 doctor 全绿但收不到汇总（授权过期浅检验不出）→ `lark-listener doctor --deep`（真探 search:message + AI 后端）
- 拉不到消息 → lark-cli 授权过期：`lark-cli auth login --profile <配置的 appid> --scope search:message`（appid 见 `lark-listener config get lark_cli_appid`）
- bot 不回 → `lark-listener status`，没跑就 `lark-listener start`
- 升级后行为没变 → 必须 `lark-listener restart`
- 日志：`tail -n 100 ~/.lark_listener/logs/stderr.log`

## 路径
`~/.lark_listener/`（config.yaml / state.json / logs / venv）；
`~/Library/LaunchAgents/com.larklistener.plist`。
