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
- 你（AI）自己要拿结果看就加 `--quiet`（只回 stdout，不打扰用户飞书）；想让用户也在飞书收到，就去掉 `--quiet`。
- 只读：不写 state、不影响正在跑的定时轮询，可随时跑。退出码 0 成功（含无消息）/ 非 0 出错。

## 先诊断
排查任何问题，先跑（机读）：
```bash
lark-listener doctor --json     # 主动自检：config/服务/lark-cli 授权/轮询时效/日志/AI 后端
lark-listener status --json     # 服务三态 + 进程 PID + 文件位置 + 上次轮询
```
`doctor` 每项带 `fix` 字段，直接给修复命令。退出码：status 0=运行/3=停/4=未装；doctor 0=全过/1=有 fail。

## ✅ 可直接（非交互）运行
- `lark-listener summarize --start <epoch> --end <epoch> [--quiet]` — 按需汇总某时间窗到 stdout（见上「按需汇总」）
- `lark-listener start | stop | restart` — 服务控制
- `lark-listener status [--json]` / `lark-listener doctor [--json] [--deep]`
- `lark-listener config get [KEY] [--json]` — 查看配置（api_key 已脱敏）
- `lark-listener config set KEY VALUE [--add|--remove] [--force]` — 改配置，下次轮询生效（不重启）
  - 点号路径：`poll_interval`、`keywords`、`ai.model`、`notify.user_id`、`lark_cli_appid` 等
  - 列表：整体 `config set keywords a,b`；增 `--add`；减 `--remove`
  - 受保护项（`ai`/`notify`/`lark_cli_appid`）需 `--force`
- `lark-listener agent-skills install|uninstall`

## 🚫 不要无人值守运行（会卡 stdin / 弹 GUI）
- `lark-listener setup`（交互向导）、`lark-listener uninstall`（二次确认）、
  `lark-listener config`（无参=开 GUI 编辑器）——交给用户在自己终端跑。

## 常见修复
- 拉不到消息 → lark-cli 授权过期：`lark-cli auth login --scope search:message`
- bot 不回 → `lark-listener status`，没跑就 `lark-listener start`
- 升级后行为没变 → 必须 `lark-listener restart`
- 日志：`tail -n 100 ~/.lark_listener/logs/stderr.log`

## 路径
`~/.lark_listener/`（config.yaml / state.json / logs / venv）；
`~/Library/LaunchAgents/com.larklistener.plist`。
