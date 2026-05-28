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

TRIGGER_KEYWORDS = {"汇总", "总结", "summary"}

TIME_PARSE_PROMPT = """\
当前时间：{now}

用户发送了以下消息，请从中提取"从什么时间开始汇总"的起始时间。
用户消息："{message}"

请严格按以下规则输出 JSON，不要输出其他内容：
- 如果能识别出时间，输出：{{"ok": true, "start_time": "ISO 8601 格式，带 +08:00 时区"}}
- 如果无法识别时间（只是简单的"汇总"等），输出：{{"ok": false}}

示例：
- "汇总今天上午的消息" → {{"ok": true, "start_time": "2026-05-28T00:00:00+08:00"}}
- "汇总最近2小时" → {{"ok": true, "start_time": "2026-05-28T07:00:00+08:00"}}
- "总结昨天下午3点以后的" → {{"ok": true, "start_time": "2026-05-27T15:00:00+08:00"}}
- "汇总" → {{"ok": false}}"""


def _parse_time_with_ai(message: str, config: dict) -> Optional[datetime]:
    """Use AI to parse natural language time from user message."""
    ai_cfg = config["ai"]
    api_key = ai_cfg.get("api_key", "")
    now = datetime.now(TZ)
    prompt = TIME_PARSE_PROMPT.format(now=now.isoformat(), message=message)

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
            return None

        if result.get("ok") and result.get("start_time"):
            return datetime.fromisoformat(result["start_time"])
    except Exception:
        logger.exception("Failed to parse time from message: %s", message)

    return None


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


def _bot_listener():
    """Background thread: listen for bot messages via WebSocket, trigger poll on command."""
    while _running:
        try:
            proc = subprocess.Popen(
                [
                    "lark-cli", "event", "+subscribe",
                    "--event-types", "im.message.receive_v1",
                    "--compact",
                    "--as", "bot",
                    "--quiet",
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
                try:
                    event = json.loads(line)
                    content = event.get("content", "").strip()
                    if not content:
                        continue
                    # Check if message starts with a trigger keyword
                    content_lower = content.lower()
                    is_trigger = any(content_lower.startswith(kw) for kw in TRIGGER_KEYWORDS)
                    if is_trigger:
                        logger.info("Received trigger command: %s", content)
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
            # Parse time from trigger message
            config = load_config(config_path)
            my_user_id = config["notify"]["user_id"]
            custom_start = _parse_time_with_ai(trigger_msg, config)
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
