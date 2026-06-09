# AGENTS.md — LarkListener 安装/操作契约（给 AI agent）

This file is for AI coding agents (Claude Code, etc.) installing or operating
LarkListener on a user's machine. **Read it before running anything.** Human guide: [README.md](README.md).

> **🚫 Do NOT clone this repo.** You don't need the source — installation is a single
> `curl … | bash` (step 2 below) that fetches everything. Just read this file (raw:
> `https://raw.githubusercontent.com/AltairEven/LarkListener/main/AGENTS.md`) and drive
> the install through the user's terminal. Cloning wastes a step and confuses the user.
>
> **Want to audit `install.sh` before running it?** Good instinct — but fetch just that
> one file, don't clone the whole repo for it:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh -o /tmp/ll-install.sh
> # read /tmp/ll-install.sh, then:
> bash /tmp/ll-install.sh
> ```

## What it is

macOS-only background service (launchd): periodically pulls the user's Feishu/Lark
unread messages via `lark-cli` → AI summarises → the bot DMs a digest + a desktop
notification. Runtime deps: Python ≥ 3.9, `lark-cli` (Node.js), one AI backend
(Claude / OpenAI-compatible / local ollama).

## ⚠️ The one input you MUST get from the user: the bot appId

LarkListener runs **as a specific Lark bot**. Before configuring, you MUST confirm
**which bot — the `lark-cli` appId, `cli_xxx` — carries the service.** Do **not**
silently assume the currently-active `lark-cli` profile is the right one; ask.

- Enumerate the available bots: `lark-cli profile list` (JSON with `appId` / `user` / `brand`).
- Show them to the user and have them choose the appId. This is the single most
  important decision in setup — getting it wrong points the service at the wrong bot.

## Install contract — who runs what

| Step | Command | Who runs it |
|---|---|---|
| 1. Prereqs | `lark-cli` installed + logged in with `--scope search:message` | user (you can verify via `lark-cli profile list`) |
| 2. Install | `curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh \| bash` | **you** (non-interactive) |
| 3. Configure | `lark-listener setup` | **the USER** — interactive (see below) |
| 4. Start | `lark-listener start` | **you** (non-interactive) |
| 5. Use | message the bot 「汇总」 in Feishu | user |

## 🚫 Do NOT run these unattended

`setup`, `uninstall`, `config` block on stdin or open a GUI — running them through
your Bash tool will hang or hit EOF and leave a half-configured install.

- **`lark-listener setup`** — interactive wizard. **Hand it to the user**: tell them
  to run `! lark-listener setup` so it runs in their own session. Before they do,
  make sure they have ready: **the bot appId (`cli_xxx`, see above — emphasise this)**,
  their AI backend + model + API key. Prompts, in order:
  1. **bot appId (`cli_xxx`)** ← the critical one
  2. poll interval (sec, default 300)
  3. keywords (comma-separated, optional)
  4. AI backend: `1) openai  2) claude  3) ollama`
  5. model name
  6. API key (blank for ollama)
  7. API base URL (blank = default)
  8. user_id / bot_chat_id — auto-derived via `lark-cli` (no need to ask the user)
  9. authorise `search:message` — opens a browser
- **`lark-listener uninstall`** — prompts `确认卸载？(y/N)`.
- **`lark-listener config`** — opens a GUI editor; edit `~/.lark_listener/config.yaml` directly instead.

## ✅ Safe for you to run

- `lark-listener doctor [--json] [--deep]` — active self-check (config / service /
  lark-cli auth / poll freshness / logs / AI backend), each finding carries a `fix`.
  **Start here when something is wrong.** Exit 0 = all pass, 1 = has a fail.
- `lark-listener status [--json]` — service state + main/listener PIDs + file
  locations + last poll. Exit 0 running / 3 stopped / 4 not installed.
- `lark-listener summarize --start <epoch> --end <epoch> [--quiet]` — on-demand
  summary of a time window to stdout (Unix-second timestamps; default also pushes
  the Feishu DM, `--quiet` returns stdout only). Read-only; safe alongside the daemon.
- `lark-listener config get [KEY] [--json]` — view config (api_key masked).
- `lark-listener config set KEY VALUE [--add|--remove] [--force]` — non-interactive
  edit; dotted paths (`poll_interval`, `keywords`, `ai.model`, …); protected keys
  (`ai`/`notify`/`lark_cli_appid`) need `--force`; takes effect next poll.
- `lark-listener start | stop | restart` — non-interactive service control.
- `lark-listener agent-skills install | uninstall` — manage on-machine operating skill.
- `lark-cli profile list` — enumerate available bots.
- `tail -n 100 ~/.lark_listener/logs/stderr.log` — logs.

## Operating it (chat, not shell)

Daily use is **natural-language messages sent to the bot inside Feishu** — these are
not shell commands: 「汇总」/「总结」/`summary`, 「汇总最近2小时」, 「当前配置」, 「帮助」,
「轮询间隔改成10分钟」, 「关注关键词 上线」 / 「不要关注 故障」 (reply 「确认」 to apply).
`ai` / `notify` / `lark_cli_appid` are protected — change them by editing the file,
not over chat; config edits take effect on the next poll (no restart). Restart is
only needed after a code upgrade.

## Upgrade

```bash
~/.lark_listener/venv/bin/pip install --force-reinstall "git+https://github.com/AltairEven/LarkListener.git"
lark-listener restart   # required — without it the old code keeps running
```

## Troubleshooting

- **Can't fetch messages** → `lark-cli` auth expired → `lark-cli auth login --scope search:message`.
- **Bot silent** → `lark-listener status`; if not running → `lark-listener start`.
- **No desktop notification** → secondary channel only; the bot DM still arrives, safe to ignore.
- First look: `tail -n 100 ~/.lark_listener/logs/stderr.log`.

## On-machine discovery (Claude Code)

Installing LarkListener also drops a Claude Code skill at
`~/.claude/skills/lark-listener/` (when `~/.claude/` exists), so any later Claude
session auto-discovers how to operate the service — no need to re-fetch this file.
The skill defers to `lark-listener --help` / `doctor` as the source of truth.

## Paths

- `~/.lark_listener/` — `config.yaml` · `state.json` · `logs/` · `venv/`
- `~/Library/LaunchAgents/com.larklistener.plist` — launchd config
