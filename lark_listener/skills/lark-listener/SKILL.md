---
name: lark-listener
description: 管理与诊断本机的 LarkListener 飞书消息汇总后台服务。当用户提到 LarkListener / 飞书汇总服务 / 飞书消息总结 bot，或要启动/停止/重启、查看状态、改配置、排查「不工作/收不到汇总/bot 不回」等运行时问题时使用。
---

# 操作 LarkListener（macOS 后台服务）

LarkListener 是装在本机的 launchd 后台服务：定时拉飞书未读 → AI 汇总 → bot 私聊推送。
本 skill 教你（AI）安装后如何安全操作它。**`lark-listener --help` 与 `lark-listener doctor`
是契约的唯一事实源——本文若与其冲突，以命令输出为准。**

## 先诊断
排查任何问题，先跑（机读）：
```bash
lark-listener doctor --json     # 主动自检：config/服务/lark-cli 授权/轮询时效/日志/AI 后端
lark-listener status --json     # 服务三态 + 进程 PID + 文件位置 + 上次轮询
```
`doctor` 每项带 `fix` 字段，直接给修复命令。退出码：status 0=运行/3=停/4=未装；doctor 0=全过/1=有 fail。

## ✅ 可直接（非交互）运行
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
