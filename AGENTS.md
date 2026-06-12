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

`setup`, `uninstall`, `config` block on stdin or open a GUI. EOF/Ctrl-C cancels
cleanly (exit 1, nothing half-written; `setup` exits 0 on success / 1 on cancel or
missing prereqs) — but still hand them to the user; running them through your Bash
tool just wastes a cancelled run.

- **`lark-listener setup`** — interactive wizard. **Hand it to the user**: tell them
  to run `! lark-listener setup` so it runs in their own session. Before they do,
  make sure they have ready: **the bot appId (`cli_xxx`, see above — emphasise this)**,
  their AI backend + model + API key. Prompts, in order:
  1. **bot appId (`cli_xxx`)** ← the critical one
  2. poll interval (sec, default 300; **0 = disable auto-polling**, on-demand summaries only)
  3. keywords (comma-separated, optional)
  4. AI backend: `1) openai  2) claude  3) ollama`
  5. model name
  6. API key (blank for ollama)
  7. API base URL (blank = default)
  8. user_id / bot_chat_id — auto-derived via `lark-cli`; if auto-derivation fails the
     wizard falls back to asking for them manually (`ou_xxx` / `oc_xxx`), so warn the
     user they might have to paste those
  9. authorise `search:message` — opens a browser

  **Re-running setup with an existing `~/.lark_listener/config.yaml` skips prompts
  2-7 entirely** — it only re-syncs the appId and derived IDs. To change other
  settings on an installed instance, use `config set` (below) or edit the file;
  don't send the user back through setup for that.
- **`lark-listener uninstall`** — prompts `确认卸载？(y/N)`.
- **`lark-listener config`** — opens a GUI editor; edit `~/.lark_listener/config.yaml` directly instead.

## ✅ Safe for you to run

- `lark-listener doctor [--json] [--deep]` — active self-check (config / service /
  lark-cli / poll freshness / logs / AI backend / special-focus config), each finding carries a `fix`.
  **Start here when something is wrong.** Exit 0 = all pass, 1 = has a fail.
  The shallow check cannot see token expiry — when summaries stop arriving, run
  `doctor --deep` (really probes `search:message` auth + the AI backend).
- `lark-listener status [--json]` — service state + main/listener PIDs + file
  locations + last poll. Exit 0 running / 3 stopped / 4 not installed.
- `lark-listener summarize --start <epoch> --end <epoch> [--quiet]` — on-demand
  summary of a time window (Unix-second timestamps; by default it also pushes the
  Feishu DM card and a macOS desktop notification, `--quiet` returns stdout only).
  Read-only; safe alongside the daemon. **stdout is a unified JSON envelope
  `{code, errorMsg, data}`** — success/empty/error are all valid JSON, exit code =
  `code`. `code: 0` → `data.conversations` is the array (empty array = nothing to
  summarise in that window); `code != 0` → see `errorMsg`. Parse with `json.loads`
  and branch on `code`; don't forward raw stdout as human text.
- `lark-listener config get [KEY] [--json]` — view config (api_key masked).
- `lark-listener config set KEY VALUE [--add|--remove] [--force]` — non-interactive
  edit; dotted paths (`poll_interval`, `keywords`, `ai.model`, …); protected keys
  (`ai`/`notify`/`lark_cli_appid`) need `--force`; also refused without `--force`:
  removing the bot's own chat from `exclude_chats` (anti-feedback-loop guard).
  Takes effect at the next poll cycle — up to one (old) interval away for large
  intervals; chat-side edits follow the same cadence. With `poll_interval=0`
  (auto-polling off) the daemon picks changes up within ~10 min.
  **Exception: `lark_cli_appid` only takes effect after `lark-listener restart`**
  (the bot listener subscribes with the profile captured at startup).
- `lark-listener start | stop | restart` — non-interactive service control.
- `lark-listener agent-skills install | uninstall` — manage on-machine operating skill.
- `lark-cli profile list` — enumerate available bots.
- `tail -n 100 ~/.lark_listener/logs/stderr.log` — logs.

## Operating it (chat, not shell)

Daily use is **natural-language messages sent to the bot inside Feishu** — these are
not shell commands: 「汇总」/「总结」/`summary`, 「汇总最近2小时」, 「当前配置」, 「帮助」,
「轮询间隔改成10分钟」, 「关注关键词 上线」 / 「不要关注 故障」 (reply 「确认」 to apply).
**Owner only**: messages from anyone other than the configured `notify.user_id` are
silently dropped (no reply, no AI call) — the bot is not a shared trigger.
`ai` / `notify` / `lark_cli_appid` are protected — change them by editing the file
or `config set … --force`, not over chat; config edits take effect on the next poll
(no restart; with `poll_interval=0` within ~10 min). Restart is only needed after a
code upgrade or after changing `lark_cli_appid`. Setting `poll_interval` to `0`
disables auto-polling entirely: the service stays online and the bot still answers
「汇总」/ config chat, it just stops pushing on a timer; any positive value restores it.

## Chat classification & special focus

Group chats are classified by the user's Feishu mute setting: **muted groups** only
contribute keyword hits; with `special_focus.enabled=true` every **non-muted group**
becomes "special focus" and is summarised in full (capped at
`special_focus.max_messages` per group per cycle, default 20). Summary cards section
in the order p2p → at_me → at_all → special → keyword. Knobs you may be asked to turn:

- `special_focus.enabled` / `special_focus.max_messages` — `config set` dotted paths.
- `special_focus.chats` (per-chat extra focus keywords) — **not editable via CLI or
  bot chat**; edit `~/.lark_listener/config.yaml` directly. Entries do NOT change a
  chat's classification — they only steer the AI analysis, and they are silently
  inactive while that chat is muted in Feishu (`doctor --deep` flags this).
- `exclude_chats` (excluded from all summaries) — `config set exclude_chats oc_xxx
  --add|--remove` takes a bare chat_id; the name is auto-filled on the next poll.

## Upgrade

```bash
~/.lark_listener/venv/bin/pip install --force-reinstall "git+https://github.com/AltairEven/LarkListener.git"
lark-listener restart   # required — without it the old code keeps running
```

## Troubleshooting

- **Can't fetch messages** → `lark-cli` auth expired → `lark-cli auth login --profile <configured appid> --scope search:message` (appid: `lark-listener config get lark_cli_appid`).
- **Bot silent** → `lark-listener status`; if not running → `lark-listener start`.
- **No desktop notification** → secondary channel only; the bot DM still arrives, safe to ignore.
- First look: `tail -n 100 ~/.lark_listener/logs/stderr.log`.

## On-machine discovery (Claude Code)

Installing LarkListener also drops a Claude Code skill at
`~/.claude/skills/lark-listener/` (when `~/.claude/` exists), so any later Claude
session auto-discovers how to operate the service — no need to re-fetch this file.
The skill defers to `lark-listener --help` / `doctor` as the source of truth.

## Paths

- `~/.lark_listener/` — `config.yaml` · `state.json` · `logs/` · `venv/` ·
  `shim_link` (records where the short-command symlink actually lives)
- short-command symlink `lark-listener` — location recorded in `shim_link`
  (typically `~/.local/bin` or `/opt/homebrew/bin`)
- `~/Library/LaunchAgents/com.larklistener.plist` — launchd config
