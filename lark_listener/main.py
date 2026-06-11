from __future__ import annotations

import argparse
import json
import logging
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from lark_listener.analyzer import Analyzer, estimate_ai_seconds, format_duration
from lark_listener.binaries import ensure_path, lark_cli, set_lark_profile
from lark_listener import config_editor, intent
from lark_listener.chats import ChatRegistry
from lark_listener.config import load_config, exclude_chat_id_set
from lark_listener.fetcher import Fetcher
from lark_listener.notifier import Notifier, build_summary_response, error_response
from lark_listener.common import TZ
from lark_listener.state import State

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("lark_listener")

_running = True
_listener_proc: Optional[subprocess.Popen] = None
# 触发项：(content, sender_id, message_id)；None 为关停哨兵。
_trigger_queue: queue.Queue[Optional[tuple[str, str, str]]] = queue.Queue()
_pending_change: Optional[dict] = None

# 连续轮询出错达到此阈值即告警（见 _note_poll_error）。
MAX_ERRORS = 3

# 自动轮询关闭（poll_interval<=0）时手动「汇总」的窗口兜底（见 poll_once）：
# 无 last_poll_time 基准则回溯 30 分钟；基准太旧则封顶 24 小时。
MANUAL_LOOKBACK_SECONDS = 1800
MANUAL_WINDOW_CAP_SECONDS = 86400

# 自动轮询关闭时主循环的等待上限：config_cli 从另一进程改写 config.yaml 无法
# 唤醒队列，必须定期醒来 reload 才能让「config set poll_interval 300」重新生效。
IDLE_RELOAD_SECONDS = 600

# 启动期配置加载失败时退出前的等待：把 launchd KeepAlive（ThrottleInterval=10）
# 的崩溃重启循环从 10 秒级降到分钟级，避免刷爆 stderr.log。
STARTUP_FAILURE_BACKOFF_SECONDS = 60


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
    """Kill leftover lark-cli event subscribe processes — 仅限本实例。

    模式必须带 --profile <appid>（lark_cli() 会给每次调用追加该参数）：全局的
    `lark-cli event.*--as bot` 会误杀 dev/prod 对方实例、以及本机其它无关的
    lark-cli 订阅进程（lark-event 等 agent 场景真实存在）。profile 未知时宁可
    不杀——孤儿会在下次带 profile 的启动时被清掉。
    """
    from lark_listener import binaries
    profile = binaries.get_lark_profile()
    if not profile:
        return
    try:
        subprocess.run(
            ["pkill", "-f", binaries.event_subscriber_pkill_pattern(profile)],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _bot_listener():
    """Background thread: listen for bot messages via WebSocket, trigger poll on command."""
    global _listener_proc
    _kill_stale_event_subscribers()
    while _running:
        proc = None
        try:
            proc = subprocess.Popen(
                lark_cli(
                    "event", "+subscribe",
                    "--event-types", "im.message.receive_v1",
                    "--as", "bot",
                    "--force",
                ),
                stdout=subprocess.PIPE,
                # stderr 不能 PIPE（循环只读 stdout，长驻子进程写满 64KB 管道
                # 缓冲后会阻塞，事件流静默冻结）；继承父进程 stderr（默认 None）
                # 则零管道零死锁，且订阅失败原因经 launchd 落进 stderr.log。
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
                    # reaction 延后到 _handle_message：仅对将真正处理的消息回执，
                    # 避免给陌生人/无意义消息加表情误导「命令已接受」。
                    _trigger_queue.put((content, sender_id, message_id))
                except json.JSONDecodeError:
                    continue
        except Exception:
            logger.exception("Bot listener error")
        finally:
            # 无论正常退出还是异常路径，都回收 event 子进程（node 壳 + Go 二进制）。
            # 否则异常时 proc.terminate 被跳过，每次重连泄漏一个孤儿订阅进程。
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        # event 子进程退出（连接正常结束、网络断开、或被拒如授权失效）。若服务仍在
        # 运行，等待后再重连——否则 `lark-cli event` 立即失败时（profile 失效/授权
        # 过期）for 循环瞬间结束，while 会无间隔 busy-loop 狂开子进程。
        if _running:
            logger.info("Bot listener exited, reconnecting in 5s...")
            time.sleep(5)


# 守护进程内跨轮复用同一 registry：refresh 失败时保留上一轮 mute 结果
# （spec §2 降级）。cmd_summarize 子进程各自新建，首刷失败则全按勿扰。
_chat_registry: Optional[ChatRegistry] = None


def _get_chat_registry(special_enabled: bool) -> ChatRegistry:
    global _chat_registry
    if _chat_registry is None:
        _chat_registry = ChatRegistry(special_enabled=special_enabled)
    _chat_registry.special_enabled = special_enabled
    return _chat_registry


def _fetch_window(config, start, end, processed_ids):
    """拉取 [start, end) 内的相关消息。返回 (categorized, fetcher)。
    fetcher 一并返回，供 _analyze_window 取上下文与特别关注判定（同一实例）。"""
    exclude_ids = exclude_chat_id_set(config)
    sf = config.get("special_focus") or {}
    registry = _get_chat_registry(bool(sf.get("enabled")))
    registry.refresh()
    fetcher = Fetcher(
        keywords=config.get("keywords", []),
        registry=registry,
        special_max_messages=sf.get("max_messages", 20),
    )
    categorized = fetcher.fetch(
        start, end,
        processed_ids=processed_ids,
        exclude_chat_ids=exclude_ids or None,
    )
    return categorized, fetcher


def _analyze_window(config, fetcher, categorized, start, end, my_user_id):
    """取上下文 + 调 AI 分析。返回 analysis。"""
    context = {}
    context_limit = config.get("context_messages", 20)
    if context_limit > 0:
        context = fetcher.fetch_context(categorized, start, end, limit=context_limit)
        ctx_total = sum(len(msgs) for msgs in context.values())
        if ctx_total:
            logger.info("Fetched %d context messages for %d chats", ctx_total, len(context))
    sf = config.get("special_focus") or {}
    bound = {c["chat_id"]: c.get("keywords", [])
             for c in sf.get("chats", [])
             if isinstance(c, dict) and c.get("chat_id")}
    registry = getattr(fetcher, "registry", None)
    special_set = set(registry.special_chat_ids()) if registry else set()
    all_chat_ids = {m.get("chat_id") for msgs in categorized.values()
                    for m in msgs if m.get("chat_id")}
    special_chats = {cid: bound.get(cid, []) for cid in all_chat_ids & special_set}
    ai_cfg = config["ai"]
    analyzer = Analyzer(
        provider=ai_cfg["provider"],
        model=ai_cfg["model"],
        api_key=ai_cfg.get("api_key", ""),
        base_url=ai_cfg.get("base_url", ""),
        keywords=config.get("keywords", []),
    )
    return analyzer.analyze(categorized, my_user_id=my_user_id, context=context,
                            special_chats=special_chats or None)


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
    interval = config["poll_interval"]
    if custom_start:
        start = custom_start
    elif interval > 0:
        start = state.last_poll_time or (now - timedelta(seconds=interval))
    else:
        # 自动轮询关闭（interval<=0）时，手动「汇总」的窗口不能再用 interval 兜底
        # （回溯 0 秒＝零宽窗口必然为空）：无基准则回溯 30 分钟；有基准但太旧
        # （如刚从轮询模式切过来）则封顶 24h，防止一次拉取数周消息爆 AI 成本。
        start = state.last_poll_time or (now - timedelta(seconds=MANUAL_LOOKBACK_SECONDS))
        start = max(start, now - timedelta(seconds=MANUAL_WINDOW_CAP_SECONDS))
    end = now

    notify_cfg = config["notify"]
    my_user_id = notify_cfg["user_id"]

    processed_ids = set() if custom_start else state.processed_message_ids
    categorized, fetcher = _fetch_window(config, start, end, processed_ids)

    total = sum(len(msgs) for msgs in categorized.values())
    logger.info("Fetched %d new messages (from %s)", total, start.strftime("%m-%d %H:%M"))

    if total == 0:
        if is_manual:
            _reply_bot(my_user_id, f"📭 {start.strftime('%m-%d %H:%M')} ~ {end.strftime('%H:%M')} 期间没有新消息")
        if not custom_start:
            state.last_poll_time = now
            state.save()
        return

    if is_manual:
        est = estimate_ai_seconds(total)
        period = f"{start.strftime('%m-%d %H:%M')} ~ {end.strftime('%H:%M')}"
        _reply_bot(my_user_id, f"📊 {period} 找到 {total} 条相关消息，预计分析约 {format_duration(est)}")

    analysis = _analyze_window(config, fetcher, categorized, start, end, my_user_id)

    notifier = Notifier(
        user_id=my_user_id,
        bot_chat_id=notify_cfg["bot_chat_id"],
    )
    try:
        notifier.notify(
            categorized,
            analysis,
            start.strftime("%m-%d %H:%M"),
            end.strftime("%H:%M"),
            my_user_id=my_user_id,
        )
        logger.info("Summary sent successfully")
    except Exception:  # noqa: BLE001 — 脏数据兜底
        # notify 内部发送已 best-effort，能抛到这里的是封套/卡片构建遇到脏数据
        # （如 chat_id 为 null）。绝不能阻断下方 state 推进：否则 last_poll_time
        # 冻结，同一条毒消息每轮重新拉取、每轮必炸，汇总永久中断且重启无法自愈。
        # 该轮汇总因此被丢弃——必须给 owner 一条 best-effort 告警，不能零感知。
        logger.exception("Notify failed (dirty data?); advancing state anyway")
        _reply_bot(my_user_id,
                   "⚠️ 本轮汇总构建/推送失败，该时间窗已跳过，详见日志：\n"
                   "tail -n 100 ~/.lark_listener/logs/stderr.log")

    # Update state only for regular polls (not custom time range)
    if not custom_start:
        all_ids = []
        for msgs in categorized.values():
            # .get 过滤：缺 message_id 的脏消息若硬下标，会恰在 notify 兜底
            # 之后、state.save 之前抛 KeyError——毒消息循环换个位置复活。
            all_ids.extend(m.get("message_id") for m in msgs if m.get("message_id"))
        state.add_processed_ids(all_ids)
        state.last_poll_time = now
        state.save()


def _emit_response(resp: dict) -> int:
    """Print the unified envelope as JSON to stdout and return its code as the
    command exit code. stdout stays pure JSON so AI agents can always parse it;
    human-facing notes (e.g. push failures) go to stderr."""
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    return resp["code"]


def cmd_summarize(start_ts: int, end_ts: int, quiet: bool = False) -> int:
    """按需汇总 [start_ts, end_ts]（Unix 秒）内的消息。
    stdout 一律输出统一封套 {code, errorMsg, data}（成功/空/错误都是合法 JSON，
    退出码＝code）；默认也推飞书 DM + 桌面通知（卡片），--quiet 只回 stdout。只读，不碰 state。"""
    if start_ts >= end_ts:
        return _emit_response(error_response("--start 必须早于 --end"))
    try:
        config = load_config()
    except Exception as e:  # noqa: BLE001
        return _emit_response(error_response(f"读取配置失败：{e}"))
    set_lark_profile(config.get("lark_cli_appid"))
    try:
        start = datetime.fromtimestamp(start_ts, TZ)
        end = datetime.fromtimestamp(end_ts, TZ)
    except (ValueError, OverflowError, OSError) as e:
        return _emit_response(error_response(f"时间戳无效（应为 Unix 秒，注意不是毫秒）：{e}"))
    period_s = start.strftime("%m-%d %H:%M")
    period_e = end.strftime("%m-%d %H:%M")
    my_user_id = config["notify"]["user_id"]

    try:
        categorized, fetcher = _fetch_window(config, start, end, set())
        total = sum(len(msgs) for msgs in categorized.values())
        analysis = (_analyze_window(config, fetcher, categorized, start, end, my_user_id)
                    if total else {})
        # 封套构建也在 try 内：spec 保证 stdout 永远是合法封套（含「汇总异常」），
        # 任何脏数据（如 chat_id 为 null）都不能以裸 traceback 收场。
        resp = build_summary_response(categorized, analysis, period_s, period_e, my_user_id)
    except Exception as e:  # noqa: BLE001
        return _emit_response(error_response(f"汇总失败：{e}"))

    code = _emit_response(resp)

    if not quiet:
        # 是否值得推送由 notify 统一裁决（空封套 → 卡片为 None → 不发），
        # 不在这里重复判空；传入已构建的封套避免重建、保证 stdout 与推送同源。
        try:
            Notifier(
                user_id=my_user_id,
                bot_chat_id=config["notify"]["bot_chat_id"],
            ).notify(categorized, analysis, period_s, period_e, my_user_id=my_user_id, resp=resp)
        except Exception as e:  # noqa: BLE001
            print(f"（飞书推送失败，已忽略：{e}）", file=sys.stderr)
    return code


def _handle_message(content: str, sender_id: str, config_path: str, state_path: str,
                    message_id: str = ""):
    """Dispatch a bot message: summary trigger or config operation — owner only.

    Non-owner messages are dropped before intent parsing (no AI call, no reply).
    A "Get" reaction is added only once the message is determined actionable —
    not on receipt — so non-actionable messages don't get a misleading
    acknowledgement.
    """
    global _pending_change
    config = load_config(config_path)
    my_user_id = config["notify"]["user_id"]
    # 身份检查必须在 intent.parse 之前：parse 会打一次 AI，否则任何能私聊
    # bot 的租户内用户都能刷 owner 的 AI 配额、触发汇总扰动轮询窗口。
    # 静默忽略（不回复）——回复本身也是可被刷的出口。
    if sender_id != my_user_id:
        logger.info("Ignoring message from non-owner: %s", sender_id or "unknown")
        return
    parsed = intent.parse(content, config)

    if parsed.type == "summary":
        if message_id:
            _add_reaction(message_id)
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
        # AI parse failed (outage / bad JSON) — let the sender know it wasn't
        # understood. sender 已通过前置 owner 校验，必为非空的本人。
        _reply_bot(sender_id, "🤔 没太听懂，发「帮助」可查看用法。")
        return

    # Remaining types are config operations（owner 身份已在 intent.parse 前校验）.
    # Acknowledge with a reaction before processing.
    if message_id:
        _add_reaction(message_id)

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


def _note_poll_error(error_count: int, my_user_id: str) -> int:
    """Increment the consecutive-error count; alert and reset at the threshold.

    Resetting after the alert (rather than alerting once forever) avoids both
    every-cycle spam and the original `== MAX_ERRORS` bug where a persistent
    failure alerted only once and then ran silently. Returns the new count.
    """
    error_count += 1
    if error_count >= MAX_ERRORS:
        _reply_bot(my_user_id, f"⚠️ LarkListener 已连续出错 {MAX_ERRORS} 次，请检查日志：\ntail -f ~/.lark_listener/logs/stderr.log")
        return 0
    return error_count


def _dispatch_trigger(item: tuple[str, str, str], config_path: str, state_path: str, my_user_id: str):
    """Handle one trigger item. A failure (AI, network, lark-cli, bad config) must
    NOT crash the service — otherwise launchd KeepAlive restarts into a crash loop."""
    content, sender_id, message_id = item
    try:
        _handle_message(content, sender_id, config_path, state_path, message_id)
    except Exception:
        logger.exception("Error handling message: %s", content[:50])
        _reply_bot(my_user_id, "⚠️ 处理请求时出错，请查看日志：\ntail -f ~/.lark_listener/logs/stderr.log")


def _poll_wait_timeout(interval: int) -> int:
    """Queue wait timeout. With auto-poll disabled (interval<=0) we still wake
    every IDLE_RELOAD_SECONDS to reload config — file edits from another process
    (lark-listener config set) can't wake the queue, and re-enabling polling
    must take effect without a restart. The wake is reload-only: poll_once
    still doesn't run while interval<=0."""
    return interval if interval > 0 else IDLE_RELOAD_SECONDS


def _startup_message(interval: int) -> str:
    if interval > 0:
        return f"✅ LarkListener 已启动（轮询间隔 {interval} 秒）。发「帮助」可查看或修改配置。"
    return "✅ LarkListener 已启动（自动轮询已关闭，仅按需汇总）。发「帮助」可查看或修改配置。"


def run():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from lark_listener import service
    home = service.LISTENER_HOME  # 支持 LARK_LISTENER_HOME 覆盖（开发隔离）
    config_path = str(home / "config.yaml")
    state_path = str(home / "state.json")

    logger.info("LarkListener starting...")

    # Load config for user_id
    try:
        config = load_config(config_path)
    except Exception:  # noqa: BLE001
        # 启动期配置坏掉（手编损坏/缺必填项）绝不能裸崩：launchd KeepAlive +
        # ThrottleInterval=10 会进入每 10 秒无限重启循环刷爆日志。慢退后再退出，
        # 把重启频率降到分钟级，等用户按 doctor 指引修复。
        logger.exception(
            "启动失败：配置无法加载（%s）。请修复后再 start，可运行 "
            "`lark-listener doctor` 查看具体问题。%d 秒后退出等待重启。",
            config_path, STARTUP_FAILURE_BACKOFF_SECONDS,
        )
        # 分片睡：裸 time.sleep(60) 因 PEP 475 被 SIGTERM 打断后自动续睡，
        # `stop` 要等 launchd SIGKILL 才能收尾。
        for _ in range(STARTUP_FAILURE_BACKOFF_SECONDS):
            if not _running:
                break
            time.sleep(1)
        return
    # Pin every lark-cli call to the configured bot before the listener thread
    # starts or any startup message is sent.
    set_lark_profile(config.get("lark_cli_appid"))
    my_user_id = config["notify"]["user_id"]
    interval = config.get("poll_interval", 300)

    # Notify startup
    _reply_bot(my_user_id, _startup_message(interval))

    # Start bot listener in background thread
    listener_thread = threading.Thread(target=_bot_listener, daemon=True)
    listener_thread.start()

    error_count = 0
    next_cycle_due = 0.0  # monotonic 秒；0 = 立即执行首轮

    while _running:
        if time.monotonic() >= next_cycle_due:
            try:
                config = load_config(config_path)
                interval = config.get("poll_interval", 300)
                my_user_id = config["notify"]["user_id"]
                # interval<=0 关闭自动轮询：只保留 bot 监听/按需汇总/改配置，不再定时拉取。
                if interval > 0:
                    poll_once(config_path, state_path)
                # 任何健康迭代都重置（不只 interval>0）：否则汇总-only 模式下相隔数周
                # 的孤立瞬时错误会累计成虚假的「连续出错」告警。
                error_count = 0
            except Exception:
                logger.exception("Error during poll cycle")
                error_count = _note_poll_error(error_count, my_user_id)
            next_cycle_due = time.monotonic() + _poll_wait_timeout(interval)

        # 等到下一轮到期，或提前被 bot 消息唤醒。trigger 处理完**不重置节拍**：
        # 否则任何人发任意消息（含陌生人闲聊）都会立即多跑一轮 poll_once，
        # 轮询节奏可被外人扰动。interval<=0 时到期动作只是 reload 配置。
        # 注意不对称：config reload 也只在到期发生——interval 很大（如 3600）
        # 时 `config set` 最长要等一个旧 interval 才被感知（interval<=0 反而
        # 有 IDLE_RELOAD_SECONDS=600 上限）；bot 改配置走 _handle_message
        # 自己 load，不受此影响，restart 亦可立即生效。
        timeout = max(0.0, next_cycle_due - time.monotonic())
        try:
            item = _trigger_queue.get(timeout=timeout)
        except queue.Empty:
            continue
        if item is None:
            break
        _dispatch_trigger(item, config_path, state_path, my_user_id)

    # Notify shutdown
    _reply_bot(my_user_id, "🔴 LarkListener 已停止")
    logger.info("LarkListener stopped.")


def main():
    ensure_path()
    parser = argparse.ArgumentParser(
        prog="lark-listener",
        description="飞书消息汇总后台服务：定时拉取未读消息 → AI 分析 → Bot 私聊推送汇总 + macOS 通知。",
        epilog=(
            "AI agent 操作入口：`lark-listener doctor --json`（自检）与 "
            "`lark-listener status --json`（状态）是排查起点。\n"
            "✅ 可非交互运行：start/stop/restart/status/doctor/summarize/config get/config set/agent-skills。\n"
            "🚫 交互式·交给用户：setup、uninstall、config（无参开 GUI）。\n"
            "\n"
            "配置文件：~/.lark_listener/config.yaml；日志：~/.lark_listener/logs/stderr.log\n"
            "首次安装后请先运行 `lark-listener setup`。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("run", help="运行守护循环（launchd 调用，一般无需手动跑）")
    sub.add_parser("setup", help="🚫 交互式·交给用户：安装向导（选 Bot/AI/轮询/授权）")
    sub.add_parser("start", help="✅ 启动后台服务")
    sub.add_parser("stop", help="✅ 停止后台服务")
    sub.add_parser("restart", help="✅ 重启服务（升级或改代码后需要）")

    p_status = sub.add_parser("status", help="✅ 查看服务运行状态")
    p_status.add_argument("--json", action="store_true", help="机读 JSON 输出")

    p_doctor = sub.add_parser("doctor", help="✅ 主动自检诊断（排查起点）")
    p_doctor.add_argument("--json", action="store_true", help="机读 JSON 输出")
    p_doctor.add_argument("--deep", action="store_true", help="真探 lark-cli search:message 授权 + 对 AI 后端发真实最小请求")

    p_config = sub.add_parser(
        "config", help="✅ get/set 非交互改配置；🚫 无参=打开编辑器（人用）")
    csub = p_config.add_subparsers(dest="op")
    p_cget = csub.add_parser("get", help="✅ 查看配置（api_key 脱敏）")
    p_cget.add_argument("key", nargs="?", help="点号路径，如 ai.provider；省略=全部")
    p_cget.add_argument("--json", action="store_true")
    p_cset = csub.add_parser("set", help="✅ 改配置（点号路径）")
    p_cset.add_argument("key")
    p_cset.add_argument("value")
    grp = p_cset.add_mutually_exclusive_group()
    grp.add_argument("--add", action="store_true", help="列表：增一项")
    grp.add_argument("--remove", action="store_true", help="列表：减一项")
    p_cset.add_argument("--force", action="store_true",
                        help=f"放行受保护项 {'/'.join(sorted(config_editor.PROTECTED))}")

    p_as = sub.add_parser("agent-skills", help="✅ 安装/卸载 AI Agent 操作 skill")
    p_as.add_argument("op", choices=["install", "uninstall"])

    p_sum = sub.add_parser("summarize", help="✅ 按需汇总指定时间窗的消息到 stdout（AI agent 用）")
    p_sum.add_argument("--start", type=int, required=True, help="起始 Unix 时间戳（秒）")
    p_sum.add_argument("--end", type=int, required=True, help="结束 Unix 时间戳（秒）")
    p_sum.add_argument("--quiet", action="store_true", help="只回 stdout，不推飞书/桌面通知")

    sub.add_parser("uninstall", help="🚫 交互式·交给用户：卸载（二次确认）")

    args = parser.parse_args()
    cmd = args.command

    if cmd == "run":
        run()
        return
    if cmd == "setup":
        from lark_listener.setup_wizard import cmd_setup
        sys.exit(cmd_setup())

    from lark_listener import service
    if cmd == "start":
        sys.exit(service.cmd_start())
    if cmd == "stop":
        sys.exit(service.cmd_stop())
    if cmd == "restart":
        sys.exit(service.cmd_restart())
    if cmd == "status":
        sys.exit(service.cmd_status(as_json=args.json))
    if cmd == "uninstall":
        sys.exit(service.cmd_uninstall())
    if cmd == "doctor":
        from lark_listener import doctor
        sys.exit(doctor.cmd_doctor(as_json=args.json, deep=args.deep))
    if cmd == "config":
        if not args.op:
            sys.exit(service.cmd_config())
        from lark_listener import config_cli
        if args.op == "get":
            sys.exit(config_cli.config_get(args.key, as_json=args.json))
        if args.op == "set":
            sys.exit(config_cli.config_set(args.key, args.value, add=args.add,
                                           remove=args.remove, force=args.force))
    if cmd == "agent-skills":
        from lark_listener import agent_adapters
        if args.op == "install":
            sys.exit(agent_adapters.install_agent_skills())
        sys.exit(agent_adapters.uninstall_agent_skills())
    if cmd == "summarize":
        sys.exit(cmd_summarize(args.start, args.end, quiet=args.quiet))

    parser.print_help()


if __name__ == "__main__":
    main()
