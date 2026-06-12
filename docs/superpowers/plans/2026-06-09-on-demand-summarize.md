# On-Demand Summarize (AI Agent 对话入口 v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 AI Agent / 本地程序一个 CLI 入口 `lark-listener summarize --start <epoch> --end <epoch> [--quiet]`，当场汇总指定时间窗的飞书消息并把结果回到 stdout（默认也推飞书）。

**Architecture:** 把 `poll_once` 里「fetch」「analyze」两段抽成 `main.py` 内的纯逻辑 helper（`_fetch_window`/`_analyze_window`），poll_once 行为不变地改调它们；新增 `cmd_summarize` 复用同一对 helper + 现成的 `notifier.build_summary_text` 渲染 stdout。飞书 bot 触发链路（`_handle_message`→`poll_once`）一行不改、透明复用。

**Tech Stack:** Python ≥3.9、argparse、pytest（mock/monkeypatch）。

**Spec:** `docs/superpowers/specs/2026-06-09-on-demand-summarize-design.md`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `lark_listener/main.py` | 新增 `_fetch_window`/`_analyze_window`（**必须在 main.py**，因现有 poll_once 测试 `@patch("lark_listener.main.Fetcher/Analyzer/Notifier")`）；`poll_once` 改调它们（签名/行为不变）；新增 `cmd_summarize`；新增 `summarize` 子命令解析 + `sys.exit` 分发；导入 `build_summary_text` |
| `tests/test_main.py` | helper 接口测试、`cmd_summarize` 测试、`summarize` 分发测试 |
| `AGENTS.md` | ✅ 安全命令列表加 `summarize` |
| `lark_listener/skills/lark-listener/SKILL.md` | ✅ 命令列表加 `summarize` |

**关键约束（CLAUDE.md）**：`poll_once`/`_handle_message`/`_reply_bot`/`_add_reaction`/`_pending_change` 保持原位、原签名；现有 poll_once 测试的 patch 目标不变；best-effort 不可抛。

退出码：`summarize` 成功（含无消息）=0；`start>=end`/config/fetch/analyze 失败=非 0；缺 `--start`/`--end`=argparse 2。

---

## Task 1: 抽出 `_fetch_window` / `_analyze_window` 并重构 `poll_once`（行为保持）

**Files:**
- Modify: `lark_listener/main.py`（`poll_once` 172-261；在其上方新增两个 helper）
- Test: `tests/test_main.py`（追加 helper 接口测试）

- [ ] **Step 1: 写失败测试** — 先在 `tests/test_main.py` **顶部补 `from datetime import datetime`**（现有只 import 了 `datetime as _dt`，新测试用到裸 `datetime`，缺它会 `NameError`；`MagicMock`/`patch`/`pytest`/`main_mod` 均已存在，无需再加）。然后追加：

```python
@patch("lark_listener.main.Fetcher")
def test_fetch_window_returns_categorized_and_fetcher(MockFetcher):
    fetcher = MockFetcher.return_value
    fetcher.fetch.return_value = {"cat": [{"message_id": "m1"}]}
    config = {"keywords": ["k"], "include_at_all": True, "exclude_chat_ids": ["oc_x"]}
    start = datetime(2026, 6, 9, 10, 0, tzinfo=main_mod.TZ)
    end = datetime(2026, 6, 9, 11, 0, tzinfo=main_mod.TZ)
    categorized, returned = main_mod._fetch_window(config, start, end, set())
    assert categorized == {"cat": [{"message_id": "m1"}]}
    assert returned is fetcher
    fetcher.fetch.assert_called_once_with(
        start, end, processed_ids=set(), exclude_chat_ids={"oc_x"})


@patch("lark_listener.main.Analyzer")
def test_analyze_window_returns_analysis(MockAnalyzer):
    fetcher = MagicMock()
    MockAnalyzer.return_value.analyze.return_value = {"oc": "analysis"}
    config = {"context_messages": 0, "keywords": [],
              "ai": {"provider": "claude", "model": "m", "api_key": "k", "base_url": ""}}
    start = datetime(2026, 6, 9, 10, 0, tzinfo=main_mod.TZ)
    end = datetime(2026, 6, 9, 11, 0, tzinfo=main_mod.TZ)
    analysis = main_mod._analyze_window(
        config, fetcher, {"cat": [{"message_id": "m1"}]}, start, end, "ou")
    assert analysis == {"oc": "analysis"}
    MockAnalyzer.return_value.analyze.assert_called_once()
    fetcher.fetch_context.assert_not_called()  # context_messages=0 → 跳过
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_main.py::test_fetch_window_returns_categorized_and_fetcher -v`
Expected: FAIL — `AttributeError: ... has no attribute '_fetch_window'`

- [ ] **Step 3: 实现 helper + 重构 poll_once**

在 `lark_listener/main.py` 中 `def poll_once` **之前**插入：

```python
def _fetch_window(config, start, end, processed_ids):
    """拉取 [start, end) 内的相关消息。返回 (categorized, fetcher)。
    fetcher 一并返回，供 _analyze_window 取上下文（同一实例）。"""
    exclude_ids = set(config.get("exclude_chat_ids", []))
    fetcher = Fetcher(
        keywords=config.get("keywords", []),
        include_at_all=config.get("include_at_all", True),
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
    ai_cfg = config["ai"]
    analyzer = Analyzer(
        provider=ai_cfg["provider"],
        model=ai_cfg["model"],
        api_key=ai_cfg.get("api_key", ""),
        base_url=ai_cfg.get("base_url", ""),
        keywords=config.get("keywords", []),
    )
    return analyzer.analyze(categorized, my_user_id=my_user_id, context=context)
```

然后把现有 `poll_once`（172-261 行）**整体替换**为（行为与原版逐句等价，只是 fetch/analyze 段改调 helper）：

```python
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

    # Manual trigger: report how many relevant messages were found and the
    # rough AI analysis time, so the user knows how long to wait.
    if is_manual:
        est = estimate_ai_seconds(total)
        period = f"{start.strftime('%m-%d %H:%M')} ~ {end.strftime('%H:%M')}"
        _reply_bot(my_user_id, f"📊 {period} 找到 {total} 条相关消息，预计分析约 {format_duration(est)}")

    analysis = _analyze_window(config, fetcher, categorized, start, end, my_user_id)

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
```

- [ ] **Step 4: 跑测试确认通过（含既有 poll_once 回归）**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: 新增 2 个 helper 测试 PASS；**现有 `test_poll_once_*`（full_cycle / no_messages / manual 进度 / manual 无消息 / auto）全部仍 PASS**（证明重构未改 daemon/bot 行为）。

- [ ] **Step 5: 提交**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "refactor(poll): extract _fetch_window/_analyze_window (behavior-preserving)"
```

---

## Task 2: 新增 `cmd_summarize`

**Files:**
- Modify: `lark_listener/main.py`（导入 `build_summary_text`；新增 `cmd_summarize`）
- Test: `tests/test_main.py`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_main.py`：

```python
def test_cmd_summarize_start_after_end_errors():
    assert main_mod.cmd_summarize(2000, 1000) == 1
    assert main_mod.cmd_summarize(1000, 1000) == 1


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_text", return_value="汇总ABC")
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_default_pushes_feishu_and_stdout(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_text, MockNotifier, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000, quiet=False)
    out = capsys.readouterr().out
    assert "汇总ABC" in out
    MockNotifier.return_value.notify.assert_called_once()
    # 时间窗由时间戳换算（+08:00）
    args = mock_fw.call_args.args
    assert args[1] == datetime.fromtimestamp(1000, main_mod.TZ)
    assert args[2] == datetime.fromtimestamp(2000, main_mod.TZ)
    assert code == 0


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_text", return_value="汇总ABC")
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_quiet_skips_feishu(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_text, MockNotifier, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000, quiet=True)
    assert "汇总ABC" in capsys.readouterr().out
    MockNotifier.return_value.notify.assert_not_called()
    assert code == 0


@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_no_messages(mock_cfg, mock_prof, mock_fw, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000)
    assert "没有新消息" in capsys.readouterr().out
    assert code == 0


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_text", return_value="")  # 有消息但无可汇总内容
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_empty_text_no_push(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_text, MockNotifier, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000, quiet=False)
    assert "没有可汇总的内容" in capsys.readouterr().out
    MockNotifier.return_value.notify.assert_not_called()  # 空内容不推飞书
    assert code == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_main.py::test_cmd_summarize_start_after_end_errors -v`
Expected: FAIL — `AttributeError: ... has no attribute 'cmd_summarize'`

- [ ] **Step 3: 实现**

在 `lark_listener/main.py` 顶部把 notifier 导入改为同时引入 `build_summary_text`：

```python
from lark_listener.notifier import Notifier, build_summary_text
```

新增 `cmd_summarize`（放在 `poll_once` 之后、`_handle_message` 之前即可）：

```python
def cmd_summarize(start_ts: int, end_ts: int, quiet: bool = False) -> int:
    """按需汇总 [start_ts, end_ts]（Unix 秒）内的消息到 stdout。
    默认也推飞书 DM + 桌面通知；--quiet 只回 stdout。只读，不碰 state。"""
    if start_ts >= end_ts:
        print("❌ --start 必须早于 --end")
        return 1
    try:
        config = load_config()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 读取配置失败：{e}")
        return 1
    set_lark_profile(config.get("lark_cli_appid"))
    start = datetime.fromtimestamp(start_ts, TZ)
    end = datetime.fromtimestamp(end_ts, TZ)
    period_s = start.strftime("%m-%d %H:%M")
    period_e = end.strftime("%m-%d %H:%M")
    my_user_id = config["notify"]["user_id"]

    try:
        categorized, fetcher = _fetch_window(config, start, end, set())
        total = sum(len(msgs) for msgs in categorized.values())
        if total == 0:
            print(f"📭 {period_s} ~ {period_e} 期间没有新消息")
            return 0
        analysis = _analyze_window(config, fetcher, categorized, start, end, my_user_id)
    except Exception as e:  # noqa: BLE001
        print(f"❌ 汇总失败：{e}")
        return 1

    text = build_summary_text(categorized, analysis, period_s, period_e, my_user_id)
    if not text:
        print(f"📭 {period_s} ~ {period_e} 期间没有可汇总的内容")
        return 0
    print(text)

    if not quiet:
        # best-effort 推飞书 + 桌面通知；失败不影响已给 agent 的 stdout 结果
        try:
            Notifier(
                user_id=my_user_id,
                bot_chat_id=config["notify"]["bot_chat_id"],
            ).notify(categorized, analysis, period_s, period_e, my_user_id=my_user_id)
        except Exception as e:  # noqa: BLE001
            print(f"（飞书推送失败，已忽略：{e}）")
    return 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_main.py -k cmd_summarize -v`
Expected: 5 个 PASS。

- [ ] **Step 5: 提交**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "feat(summarize): on-demand summary to stdout (+feishu by default, --quiet)"
```

---

## Task 3: argparse `summarize` 子命令 + 分发

**Files:**
- Modify: `lark_listener/main.py`（`main()` 加 `summarize` 解析 + 分发 + epilog）
- Test: `tests/test_main.py`

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_main.py`：

```python
def test_main_summarize_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv",
                        ["lark-listener", "summarize", "--start", "1000", "--end", "2000", "--quiet"])
    seen = {}
    monkeypatch.setattr(main_mod, "cmd_summarize",
                        lambda start_ts, end_ts, quiet=False: seen.update(
                            start=start_ts, end=end_ts, quiet=quiet) or 0)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0
    assert seen == {"start": 1000, "end": 2000, "quiet": True}


def test_main_summarize_requires_start_end(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "summarize"])
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 2  # argparse 用法错误：缺 required --start/--end
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_main.py::test_main_summarize_dispatch -v`
Expected: FAIL — argparse 不识别 `summarize`（SystemExit code 2，断言 0 失败）。

- [ ] **Step 3: 实现** — 在 `main()` 中，`p_doctor` 那组 `add_parser` 附近（`uninstall` 之前）加：

```python
    p_sum = sub.add_parser("summarize", help="✅ 按需汇总指定时间窗的消息到 stdout（AI agent 用）")
    p_sum.add_argument("--start", type=int, required=True, help="起始 Unix 时间戳（秒）")
    p_sum.add_argument("--end", type=int, required=True, help="结束 Unix 时间戳（秒）")
    p_sum.add_argument("--quiet", action="store_true", help="只回 stdout，不推飞书/桌面通知")
```

在分发段（`from lark_listener import service` 之后、`parser.print_help()` 之前）加：

```python
    if cmd == "summarize":
        sys.exit(cmd_summarize(args.start, args.end, quiet=args.quiet))
```

并在 epilog 的「✅ 可非交互运行」一行把 `summarize` 加进去，例如改为：

```python
            "✅ 可非交互运行：start/stop/restart/status/doctor/summarize/config get/config set/agent-skills。\n"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: 新增 2 个 + 既有全部 PASS。
再人工验：`python3 -m lark_listener.main summarize 2>&1 | head -3`（应打印 argparse 用法错误、提示缺 --start/--end）。

- [ ] **Step 5: 提交**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "feat(cli): wire summarize subcommand (--start/--end/--quiet)"
```

---

## Task 4: 文档（AGENTS.md + 包内 SKILL.md）

**Files:**
- Modify: `AGENTS.md`、`lark_listener/skills/lark-listener/SKILL.md`

- [ ] **Step 1: AGENTS.md** — 在 `## ✅ Safe for you to run` 列表中、`status` 条目之后插入：

```markdown
- `lark-listener summarize --start <epoch> --end <epoch> [--quiet]` — on-demand
  summary of a time window to stdout (Unix-second timestamps; default also pushes
  the Feishu DM, `--quiet` returns stdout only). Read-only; safe alongside the daemon.
```

- [ ] **Step 2: SKILL.md** — 在 `lark_listener/skills/lark-listener/SKILL.md` 的「✅ 可直接（非交互）运行」块中、`status/doctor` 行之后插入：

```markdown
- `lark-listener summarize --start <epoch> --end <epoch> [--quiet]` — 按需汇总某时间窗到 stdout（Unix 秒时间戳；默认也推飞书，`--quiet` 只回 stdout）
```

- [ ] **Step 3: 确认 markdown 有效**（标题/代码围栏完好），无自动化测试。

- [ ] **Step 4: 提交**

```bash
git add AGENTS.md lark_listener/skills/lark-listener/SKILL.md
git commit -m "docs: document summarize command (AGENTS.md + skill)"
```

> 注：用户机器上 `~/.claude/skills/lark-listener/SKILL.md` 的更新随下次 `agent-skills install`（即升级时 install.sh 触发）生效；本任务只改包内源。

---

## Task 5: 全量回归 + smoke

**Files:** 无（验证）

- [ ] **Step 1: 全量单测**

Run: `python3 -m pytest -q`
Expected: 全绿（仅既有的 `test_setup_wizard.py::test_pip_install_ai_installs_into_venv_python` 在本机环境失败属预期，无新增失败）。

- [ ] **Step 2: smoke + 人工校验参数校验**

Run: `./dev-test.sh smoke 2>&1 | tail -5`（生命周期通过、自清理）
Run: `python3 -m lark_listener.main summarize --start 2000 --end 1000; echo "exit=$?"`
Expected: 打印「--start 必须早于 --end」，`exit=1`。

- [ ] **Step 3: 提交（如有遗留整理）**

```bash
git add -A
git commit -m "test: regression green for on-demand summarize"
```

---

## 自检（写完计划后回看 spec）

- **覆盖**：①核心解耦（`_fetch_window`/`_analyze_window`，留 main.py，fetch/analyze 拆两步、保 poll_once 进度时序）→ T1；②`summarize` start/end 时间戳必填、stdout、默认+飞书/`--quiet`、空消息、退出码、只读 → T2；③argparse 必填校验 + 分发 + help → T3；④复用 `build_summary_text` → T2（导入+调用）；⑤bot 路径不改、经 poll_once 透明复用 → T1（poll_once 行为保持，`_handle_message` 未触）；⑥文档 → T4；⑦回归不破既有 patch 目标 → T1/T5。
- **占位**：无 TBD/TODO；每个 code step 均含完整代码。
- **类型/签名一致**：`_fetch_window(config,start,end,processed_ids)->(categorized,fetcher)`、`_analyze_window(config,fetcher,categorized,start,end,my_user_id)->analysis`、`cmd_summarize(start_ts,end_ts,quiet=False)->int` 在 T1/T2/T3 定义与调用一致；`build_summary_text(categorized,analysis,start_str,end_str,my_user_id)` 与 notifier 现有签名一致；分发 `cmd_summarize(args.start,args.end,quiet=args.quiet)` 与定义一致。
