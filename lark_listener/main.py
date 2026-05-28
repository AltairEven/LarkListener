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

from lark_listener.analyzer import Analyzer
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
            start_time = datetime.fromisoformat(result["start_time"])
        return is_trigger, start_time
    except Exception:
        logger.exception("Failed to parse trigger from message: %s", message)

    return False, None


def _reply_bot(user_id: str, text: str):
    """Send a text reply to the user via bot."""
    subprocess.run(
        ["lark-cli", "im", "+messages-send",
         "--user-id", user_id, "--text", text, "--as", "bot"],
        capture_output=True, text=True, timeout=10,
    )


def _handle_signal(signum, frame):
    global _running
    logger.info("Received signal %s, shutting down...", signum)
    _running = False
    _trigger_queue.put(None)


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
    _kill_stale_event_subscribers()
    while _running:
        try:
            proc = subprocess.Popen(
                [
                    "lark-cli", "event", "+subscribe",
                    "--event-types", "im.message.receive_v1",
                    "--as", "bot",
                    "--force",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
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
                    msg_content = event.get("event", {}).get("message", {}).get("content", "")
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
    fetcher = Fetcher(keywords=config.get("keywords", []))
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

    ai_cfg = config["ai"]
    analyzer = Analyzer(
        provider=ai_cfg["provider"],
        model=ai_cfg["model"],
        api_key=ai_cfg.get("api_key", ""),
        base_url=ai_cfg.get("base_url", ""),
        keywords=config.get("keywords", []),
    )
    analysis = analyzer.analyze(categorized, my_user_id=my_user_id)

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
            if trigger_msg is None:
                break
            # Use AI to determine intent and extract time
            config = load_config(config_path)
            my_user_id = config["notify"]["user_id"]
            is_trigger, custom_start = _parse_trigger_with_ai(trigger_msg, config)
            if not is_trigger:
                logger.info("Message not a trigger: %s", trigger_msg[:50])
                continue
            now = datetime.now(TZ)
            if custom_start:
                logger.info("Trigger with custom start: %s", custom_start.isoformat())
                _reply_bot(my_user_id, f"⏳ 正在汇总 {custom_start.strftime('%m-%d %H:%M')} ~ {now.strftime('%H:%M')} 的消息...")
            else:
                start = State(state_path).last_poll_time or (now - timedelta(seconds=config.get("poll_interval", 300)))
                logger.info("Trigger with default time range")
                _reply_bot(my_user_id, f"⏳ 正在汇总 {start.strftime('%m-%d %H:%M')} ~ {now.strftime('%H:%M')} 的消息...")
            poll_once(config_path, state_path, custom_start=custom_start, is_manual=True)
        except queue.Empty:
            pass

    # Notify shutdown
    _reply_bot(my_user_id, "🔴 LarkListener 已停止")
    logger.info("LarkListener stopped.")


if __name__ == "__main__":
    main()
