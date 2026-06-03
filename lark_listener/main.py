from __future__ import annotations

import json
import logging
import queue
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from lark_listener.analyzer import Analyzer, estimate_ai_seconds, format_duration
from lark_listener.binaries import ensure_path, resolve_executable
from lark_listener.config import load_config
from lark_listener.fetcher import Fetcher, MessageCategory
from lark_listener.notifier import Notifier
from lark_listener.state import State

TZ = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("lark_listener")

_running = True
_listener_proc: Optional[subprocess.Popen] = None
_trigger_queue: queue.Queue[Optional[str]] = queue.Queue()

TRIGGER_PROMPT = """\
当前时间：{now}

你是消息助手的意图识别模块。用户给 Bot 发了一条消息，请判断用户是否想要汇总/总结消息。

用户消息："{message}"

请严格输出 JSON，不要输出其他内容：
- 如果用户想汇总消息且指定了时间范围，输出：{{"is_trigger": true, "start_time": "ISO 8601 格式，带 +08:00 时区"}}
- 如果用户想汇总消息但没指定时间，输出：{{"is_trigger": true, "start_time": null}}
- 如果用户不是想汇总消息，输出：{{"is_trigger": false, "start_time": null}}

示例：
- "汇总今天上午的消息" → {{"is_trigger": true, "start_time": "{today}T00:00:00+08:00"}}
- "帮我看看最近2小时有什么消息" → {{"is_trigger": true, "start_time": "{two_hours_ago}"}}
- "总结一下" → {{"is_trigger": true, "start_time": null}}
- "你好" → {{"is_trigger": false, "start_time": null}}"""


def _parse_trigger_with_ai(message: str, config: dict) -> tuple[bool, Optional[datetime]]:
    """Use AI to determine if message is a summary trigger and extract time."""
    ai_cfg = config["ai"]
    api_key = ai_cfg.get("api_key", "")
    now = datetime.now(TZ)
    prompt = TRIGGER_PROMPT.format(
        now=now.isoformat(),
        message=message,
        today=now.strftime("%Y-%m-%d"),
        two_hours_ago=(now - timedelta(hours=2)).isoformat(),
    )

    try:
        if ai_cfg["provider"] == "openai":
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=ai_cfg.get("base_url") or None)
            response = client.chat.completions.create(
                model=ai_cfg["model"],
                messages=[{"role": "user", "content": prompt}],
            )
            result = json.loads(response.choices[0].message.content)
        elif ai_cfg["provider"] == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=ai_cfg["model"],
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            result = json.loads(response.content[0].text)
        elif ai_cfg["provider"] == "ollama":
            import urllib.request
            url = (ai_cfg.get("base_url") or "http://localhost:11434") + "/api/chat"
            payload = json.dumps({
                "model": ai_cfg["model"], "stream": False,
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            result = json.loads(data["message"]["content"])
        else:
            return False, None

        is_trigger = result.get("is_trigger", False)
        start_time = None
        if is_trigger and result.get("start_time"):
            try:
                start_time = datetime.fromisoformat(result["start_time"])
            except (ValueError, TypeError):
                # Keep the trigger; just fall back to the default time range
                # instead of silently dropping the user's request.
                logger.warning("Invalid start_time from AI: %r", result.get("start_time"))
        return is_trigger, start_time
    except Exception:
        logger.exception("Failed to parse trigger from message: %s", message)

    return False, None


def _reply_bot(user_id: str, text: str):
    """Send a text reply to the user via bot.

    Best-effort: a failed notification (lark-cli missing, timeout, network) must
    never crash the service or, under launchd KeepAlive, trigger a restart loop.
    """
    try:
        subprocess.run(
            [resolve_executable("lark-cli"), "im", "+messages-send",
             "--user-id", user_id, "--text", text, "--as", "bot"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        logger.exception("Failed to send bot reply")


def _add_reaction(message_id: str, emoji_type: str = "Get"):
    """Add an emoji reaction to a message via bot. Best-effort (failures logged)."""
    try:
        subprocess.run(
            [resolve_executable("lark-cli"), "im", "reactions", "create",
             "--as", "bot",
             "--params", json.dumps({"message_id": message_id}),
             "--data", json.dumps({"reaction_type": {"emoji_type": emoji_type}})],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        logger.exception("Failed to add reaction to %s", message_id)


def _handle_signal(signum, frame):
    global _running
    logger.info("Received signal %s, shutting down...", signum)
    _running = False
    _trigger_queue.put(None)
    # Terminate the blocking `lark-cli event` subprocess so the listener thread
    # unblocks from `for line in proc.stdout` and exits cleanly (no orphan).
    if _listener_proc and _listener_proc.poll() is None:
        try:
            _listener_proc.terminate()
        except Exception:
            pass


def _kill_stale_event_subscribers():
    """Kill any leftover lark-cli event subscribe processes."""
    try:
        subprocess.run(
            ["pkill", "-f", "lark-cli event.*subscribe.*--as bot"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _bot_listener():
    """Background thread: listen for bot messages via WebSocket, trigger poll on command."""
    global _listener_proc
    _kill_stale_event_subscribers()
    while _running:
        try:
            proc = subprocess.Popen(
                [
                    resolve_executable("lark-cli"), "event", "+subscribe",
                    "--event-types", "im.message.receive_v1",
                    "--as", "bot",
                    "--force",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            _listener_proc = proc
            logger.info("Bot listener started")

            for line in proc.stdout:
                if not _running:
                    break
                line = line.strip()
                if not line:
                    continue
                logger.debug("Bot listener raw line: %s", line[:500])
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    # Raw event format: event.message.content = '{"text":"..."}'
                    message = event.get("event", {}).get("message", {})
                    # Immediately add a GET reaction as a "received" acknowledgement
                    message_id = message.get("message_id", "")
                    if message_id:
                        _add_reaction(message_id)
                    msg_content = message.get("content", "")
                    try:
                        content = json.loads(msg_content).get("text", "")
                    except (json.JSONDecodeError, AttributeError):
                        content = msg_content
                    content = content.strip()
                    if not content:
                        continue
                    logger.info("Bot received message: %s", content[:100])
                    _trigger_queue.put(content)
                except json.JSONDecodeError:
                    continue

            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            logger.exception("Bot listener error, restarting in 10s...")
            time.sleep(10)


def poll_once(
    config_path: Optional[str] = None,
    state_path: Optional[str] = None,
    custom_start: Optional[datetime] = None,
    is_manual: bool = False,
):
    config = load_config(config_path)
    state = State(state_path)

    now = datetime.now(TZ)
    if custom_start:
        start = custom_start
    else:
        start = state.last_poll_time or (now - timedelta(seconds=config["poll_interval"]))
    end = now

    notify_cfg = config["notify"]
    my_user_id = notify_cfg["user_id"]

    exclude_ids = set(config.get("exclude_chat_ids", []))
    fetcher = Fetcher(
        keywords=config.get("keywords", []),
        include_at_all=config.get("include_at_all", True),
    )
    categorized = fetcher.fetch(
        start, end,
        processed_ids=set() if custom_start else state.processed_message_ids,
        exclude_chat_ids=exclude_ids or None,
    )

    total = sum(len(msgs) for msgs in categorized.values())
    logger.info("Fetched %d new messages (from %s)", total, start.strftime("%m-%d %H:%M"))

    if total == 0:
        if is_manual:
            _reply_bot(my_user_id, f"📭 {start.strftime('%m-%d %H:%M')} ~ {end.strftime('%H:%M')} 期间没有新消息")
        if not custom_start:
            state.last_poll_time = now
            state.save()
        return

    # Manual trigger: report how many relevant messages were found and the
    # rough AI analysis time, so the user knows how long to wait.
    if is_manual:
        est = estimate_ai_seconds(total)
        period = f"{start.strftime('%m-%d %H:%M')} ~ {end.strftime('%H:%M')}"
        _reply_bot(my_user_id, f"📊 {period} 找到 {total} 条相关消息，预计分析约 {format_duration(est)}")

    # Fetch context messages for richer AI analysis
    context_limit = config.get("context_messages", 20)
    context = {}
    if context_limit > 0:
        context = fetcher.fetch_context(categorized, start, end, limit=context_limit)
        ctx_total = sum(len(msgs) for msgs in context.values())
        if ctx_total:
            logger.info("Fetched %d context messages for %d chats", ctx_total, len(context))

    ai_cfg = config["ai"]
    analyzer = Analyzer(
        provider=ai_cfg["provider"],
        model=ai_cfg["model"],
        api_key=ai_cfg.get("api_key", ""),
        base_url=ai_cfg.get("base_url", ""),
        keywords=config.get("keywords", []),
    )
    analysis = analyzer.analyze(categorized, my_user_id=my_user_id, context=context)

    notifier = Notifier(
        user_id=my_user_id,
        bot_chat_id=notify_cfg["bot_chat_id"],
    )
    notifier.notify(
        categorized,
        analysis,
        start.strftime("%m-%d %H:%M"),
        end.strftime("%H:%M"),
        my_user_id=my_user_id,
    )

    # Update state only for regular polls (not custom time range)
    if not custom_start:
        all_ids = []
        for msgs in categorized.values():
            all_ids.extend(m["message_id"] for m in msgs)
        state.add_processed_ids(all_ids)
        state.last_poll_time = now
        state.save()

    logger.info("Summary sent successfully")


def main():
    ensure_path()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    home = Path.home() / ".lark_listener"
    config_path = str(home / "config.yaml")
    state_path = str(home / "state.json")

    logger.info("LarkListener starting...")

    # Load config for user_id
    config = load_config(config_path)
    my_user_id = config["notify"]["user_id"]
    interval = config.get("poll_interval", 300)

    # Notify startup
    _reply_bot(my_user_id, f"✅ LarkListener 已启动（轮询间隔 {interval} 秒）")

    # Start bot listener in background thread
    listener_thread = threading.Thread(target=_bot_listener, daemon=True)
    listener_thread.start()

    error_count = 0
    MAX_ERRORS = 3

    while _running:
        try:
            config = load_config(config_path)
            interval = config.get("poll_interval", 300)
            my_user_id = config["notify"]["user_id"]
            poll_once(config_path, state_path)
            error_count = 0  # Reset on success
        except Exception:
            logger.exception("Error during poll cycle")
            error_count += 1
            if error_count == MAX_ERRORS:
                _reply_bot(my_user_id, f"⚠️ LarkListener 已连续出错 {MAX_ERRORS} 次，请检查日志：\ntail -f ~/.lark_listener/logs/stderr.log")

        # Wait for interval or trigger
        try:
            trigger_msg = _trigger_queue.get(timeout=interval)
        except queue.Empty:
            continue
        if trigger_msg is None:
            break

        # Handle the trigger. A failure here (AI, network, lark-cli, bad config)
        # must NOT crash the service — otherwise launchd KeepAlive restarts it
        # into a "trigger → crash → restart" loop. Mirror the poll-cycle handling.
        try:
            # Use AI to determine intent and extract time
            config = load_config(config_path)
            my_user_id = config["notify"]["user_id"]
            is_trigger, custom_start = _parse_trigger_with_ai(trigger_msg, config)
            if not is_trigger:
                logger.info("Message not a trigger: %s", trigger_msg[:50])
                continue
            # The GET reaction (added on receipt) already acknowledges the
            # request; poll_once then sends the "found N, est X" progress note.
            if custom_start:
                logger.info("Trigger with custom start: %s", custom_start.isoformat())
            else:
                logger.info("Trigger with default time range")
            poll_once(config_path, state_path, custom_start=custom_start, is_manual=True)
        except Exception:
            logger.exception("Error handling trigger: %s", trigger_msg[:50])
            _reply_bot(my_user_id, "⚠️ 处理触发请求时出错，请查看日志：\ntail -f ~/.lark_listener/logs/stderr.log")

    # Notify shutdown
    _reply_bot(my_user_id, "🔴 LarkListener 已停止")
    logger.info("LarkListener stopped.")


if __name__ == "__main__":
    main()
