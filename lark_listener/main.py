from __future__ import annotations

import argparse
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
from lark_listener.binaries import ensure_path, lark_cli, set_lark_profile
from lark_listener import config_editor, intent
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
_trigger_queue: queue.Queue[Optional[tuple[str, str]]] = queue.Queue()
_pending_change: Optional[dict] = None


def _reply_bot(user_id: str, text: str, markdown: bool = False):
    """Send a reply to the user via bot.

    markdown=True sends as a post message so fenced code blocks render (used for
    the config view); otherwise plain text.

    Best-effort: a failed notification (lark-cli missing, timeout, network) must
    never crash the service or, under launchd KeepAlive, trigger a restart loop.
    """
    content_flag = "--markdown" if markdown else "--text"
    try:
        subprocess.run(
            lark_cli("im", "+messages-send",
                     "--user-id", user_id, content_flag, text, "--as", "bot"),
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        logger.exception("Failed to send bot reply")


def _add_reaction(message_id: str, emoji_type: str = "Get"):
    """Add an emoji reaction to a message via bot. Best-effort (failures logged)."""
    try:
        subprocess.run(
            lark_cli("im", "reactions", "create",
                     "--as", "bot",
                     "--params", json.dumps({"message_id": message_id}),
                     "--data", json.dumps({"reaction_type": {"emoji_type": emoji_type}})),
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
                lark_cli(
                    "event", "+subscribe",
                    "--event-types", "im.message.receive_v1",
                    "--as", "bot",
                    "--force",
                ),
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
                    message_id = message.get("message_id", "")
                    if message_id:
                        _add_reaction(message_id)
                    sender = event.get("event", {}).get("sender", {})
                    sender_id = sender.get("sender_id", {}).get("open_id", "")
                    msg_content = message.get("content", "")
                    try:
                        content = json.loads(msg_content).get("text", "")
                    except (json.JSONDecodeError, AttributeError):
                        content = msg_content
                    content = content.strip()
                    if not content:
                        continue
                    logger.info("Bot received message: %s", content[:100])
                    _trigger_queue.put((content, sender_id))
                except json.JSONDecodeError:
                    continue

            proc.terminate()
            proc.wait(timeout=5)
            # event 子进程退出（连接正常结束、网络断开、或被拒如授权失效）。若服务
            # 仍在运行，等待后再重连——否则当 `lark-cli event` 立即失败时（profile
            # 失效/授权过期），for 循环瞬间结束，while 会无间隔 busy-loop 狂开子进程。
            if _running:
                logger.info("Bot listener exited, reconnecting in 5s...")
                time.sleep(5)
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
    set_lark_profile(config.get("lark_cli_appid"))
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


def _handle_message(content: str, sender_id: str, config_path: str, state_path: str):
    """Dispatch a bot message: summary trigger, or owner-only config operation."""
    global _pending_change
    config = load_config(config_path)
    my_user_id = config["notify"]["user_id"]
    parsed = intent.parse(content, config)

    if parsed.type == "summary":
        if parsed.start_time:
            logger.info("Trigger with custom start: %s", parsed.start_time.isoformat())
        else:
            logger.info("Trigger with default time range")
        poll_once(config_path, state_path, custom_start=parsed.start_time, is_manual=True)
        return

    if parsed.type == "none":
        logger.info("Message not actionable: %s", content[:50])
        return

    if parsed.type == "error":
        # AI parse failed (outage / bad JSON) — let the sender know it wasn't understood.
        if sender_id:
            _reply_bot(sender_id, "🤔 没太听懂，发「帮助」可查看用法。")
        return

    # Remaining types are config operations — owner only.
    if sender_id != my_user_id:
        if sender_id:
            _reply_bot(sender_id, "⚠️ 仅本人可查看或修改配置")
        else:
            # No sender id to route a reply to — drop silently (already ack'd via reaction).
            logger.info("Config op from unknown sender ignored")
        return

    if parsed.type == "config_view":
        _reply_bot(my_user_id, config_editor.render_config(config), markdown=True)
        return

    if parsed.type == "config_help":
        _reply_bot(my_user_id, config_editor.render_help())
        return

    if parsed.type == "config_modify":
        diff, error = config_editor.compute_diff(parsed.changes or [], config)
        if error:
            _reply_bot(my_user_id, f"⚠️ {error}")
            return
        if not diff:
            _reply_bot(my_user_id, "没有可修改的内容")
            return
        _pending_change = {"changes": parsed.changes, "diff": diff}
        _reply_bot(my_user_id, f"将修改：\n{diff}\n回复「确认」生效，「取消」放弃。")
        return

    if parsed.type == "confirm":
        if not _pending_change:
            _reply_bot(my_user_id, "当前没有待确认的修改")
            return
        result = config_editor.apply_changes(config_path, _pending_change["changes"], config)
        _pending_change = None
        if result.ok:
            _reply_bot(my_user_id, f"✅ 已更新，下次轮询生效：\n{result.diff}")
        else:
            _reply_bot(my_user_id, f"⚠️ 修改失败：{result.error}")
        return

    if parsed.type == "cancel":
        if _pending_change:
            _pending_change = None
            _reply_bot(my_user_id, "已取消修改")
        else:
            _reply_bot(my_user_id, "当前没有待确认的修改")
        return


def run():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from lark_listener import service
    home = service.LISTENER_HOME  # 支持 LARK_LISTENER_HOME 覆盖（开发隔离）
    config_path = str(home / "config.yaml")
    state_path = str(home / "state.json")

    logger.info("LarkListener starting...")

    # Load config for user_id
    config = load_config(config_path)
    # Pin every lark-cli call to the configured bot before the listener thread
    # starts or any startup message is sent.
    set_lark_profile(config.get("lark_cli_appid"))
    my_user_id = config["notify"]["user_id"]
    interval = config.get("poll_interval", 300)

    # Notify startup
    _reply_bot(my_user_id, f"✅ LarkListener 已启动（轮询间隔 {interval} 秒）。发「帮助」可查看或修改配置。")

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
            item = _trigger_queue.get(timeout=interval)
        except queue.Empty:
            continue
        if item is None:
            break
        content, sender_id = item

        # A failure here (AI, network, lark-cli, bad config) must NOT crash the
        # service — otherwise launchd KeepAlive restarts it into a crash loop.
        try:
            _handle_message(content, sender_id, config_path, state_path)
        except Exception:
            logger.exception("Error handling message: %s", content[:50])
            _reply_bot(my_user_id, "⚠️ 处理请求时出错，请查看日志：\ntail -f ~/.lark_listener/logs/stderr.log")

    # Notify shutdown
    _reply_bot(my_user_id, "🔴 LarkListener 已停止")
    logger.info("LarkListener stopped.")


def main():
    ensure_path()
    parser = argparse.ArgumentParser(
        prog="lark-listener",
        description="飞书消息汇总后台服务：定时拉取未读消息 → AI 分析 → Bot 私聊推送汇总 + macOS 通知。",
        epilog=(
            "日常使用（核心）：给 LarkListener Bot 发「汇总」/「总结」立即汇总一次；\n"
            "发「帮助」查看用法、或用自然语言改配置（如「轮询间隔改成10分钟」「关注关键词 上线」）。\n"
            "\n"
            "配置文件：~/.lark_listener/config.yaml（或运行 `lark-listener config` 打开）。\n"
            "日志：    ~/.lark_listener/logs/stderr.log\n"
            "首次安装后请先运行 `lark-listener setup`。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    _CMDS = {
        "run": "运行守护循环（launchd 调用，一般无需手动跑）",
        "setup": "交互安装向导：选 Bot、配置轮询/关键词/AI、写 launchd、引导授权",
        "start": "启动后台服务",
        "stop": "停止后台服务",
        "restart": "重启服务（升级或改代码后需要）",
        "status": "查看服务运行状态",
        "config": "打开配置文件进行编辑",
        "uninstall": "停止服务并删除全部配置与数据",
    }
    for name, help_text in _CMDS.items():
        sub.add_parser(name, help=help_text, description=help_text)
    args = parser.parse_args()

    if args.command == "run":
        run()
    elif args.command == "setup":
        from lark_listener.setup_wizard import cmd_setup
        cmd_setup()
    elif args.command in ("start", "stop", "restart", "status", "config", "uninstall"):
        from lark_listener import service
        getattr(service, f"cmd_{args.command}")()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
