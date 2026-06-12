# LarkListener — AI Agent 对话入口 v1：按需汇总（summarize）

日期：2026-06-09
状态：设计待批准

## 背景与问题

LarkListener 的「汇总」能力目前只有两个触发口、且输出都锁死在飞书：
- **定时轮询**（daemon）：后台每 `poll_interval` 自动汇总 → 推飞书 DM + 桌面通知。
- **飞书 bot 对话**：用户在飞书发「汇总最近1小时」→ `_handle_message` → `poll_once(custom_start=…, is_manual=True)` → 推飞书。

缺口：**本机 AI Agent / 程序无法「当场要一份汇总并拿到结果」**。它能 operate/diagnose（doctor/status/config），但触发汇总只能让用户去飞书手打，且结果进飞书、agent 看不到。

## 目标

给「按需汇总」开一个 **CLI 入口**，让 AI Agent / 本地程序当场触发一次汇总、**结果直接回到 stdout**；同时不破坏既有飞书投递。

## 设计哲学（来自 brainstorming 收敛）

- **request → response**：调用方发请求（要汇总哪个时间窗），同步拿回响应（汇总文本）。不做订阅者/回调/pub-sub。
- **核心传输无关**：把「产出汇总」与「投递」解耦——核心只产出 `Digest`，投递（飞书 / 桌面 / stdout）由调用方（传输层）决定。调用方是本机 AI 还是远端，对核心无差别。
- **CLI-first**：v1 只做 CLI 传输（本机调用方，零新增基础设施）。HTTP/远端传输留作后续适配器——因核心传输无关，加它不动核心。

## 架构

```
   触发/传输                         传输无关核心                 投递
 ┌─ daemon 轮询(run) ──┐      _fetch_window → _analyze_window
 ┼─ 飞书 bot 对话 ─────┼──→  (categorized, analysis) ──→ 飞书DM / 桌面通知 / stdout
 └─ CLI summarize(新) ─┘      stdout 文本 = build_summary_text   （由各传输层挑选）
```

### 1. 核心：产出与投递解耦

现状 `poll_once` 把 fetch→analyze→`notifier.notify(飞书)` 串在一起。重构：把「产出」抽成**两步纯逻辑 helper（留在 `main.py`）**，投递交给各传输层。

- 抽取（位置：**必须留在 `main.py`**，见下「关键约束」）：
  - `_fetch_window(config, start, end, processed_ids, exclude_ids) -> categorized`（fetch + 内部 set_lark_profile 已在 poll_once 上游处理）
  - `_analyze_window(config, categorized, start, end, my_user_id, context) -> analysis`
  - **拆成两步、不可合成黑盒**：因为 poll_once 的 manual 进度（`📊 找到 N 条，预计 X`）必须在 **fetch 之后、analyze 之前**发出。poll_once 走 fetch→（空/状态）→进度(manual)→analyze→notify→状态；summarize 走 fetch→（空提示）→analyze。
- **stdout 文本直接复用 `notifier.build_summary_text(categorized, analysis, start_str, end_str, my_user_id) -> str`**（已存在、模块级、返回 markdown）。飞书 DM 与 stdout 用**同一份文本**，无需新写渲染器；`build_summary_text` 返回空串即「无可汇总内容」。
- 投递仍由 `Notifier.notify(...)`（飞书 DM + 桌面通知）承担；stdout 由 `summarize` 命令 `print`。

### 2. 各传输层 = 核心 + 选定投递（行为保持/新增）

- **daemon 轮询 `poll_once`**（签名/行为不变，CLAUDE.md #7）：内部改为调 `_fetch_window`/`_analyze_window`，再做 `notifier.notify`（飞书+通知）+ 状态更新 + 空消息处理 + manual 进度回复。**对外观察行为与现状一致**（现有 `test_main.py` 的 poll_once 用例须仍绿）。
- **飞书 bot 对话**：`_handle_message` 路径不变（仍走 `poll_once(is_manual=True)`）。
- **CLI `summarize`（新增）**：
  - `lark-listener summarize --start <epoch> --end <epoch> [--quiet]`
  - `--start` / `--end` 均为 **Unix 时间戳（整数秒）**，均**必填**；`start = datetime.fromtimestamp(start_ts, TZ)`、`end = datetime.fromtimestamp(end_ts, TZ)`（TZ=+08:00，与 state/poll 一致）
  - 调 `_fetch_window(config, start, end, processed_ids=set(), exclude_ids)` →（有消息则）`_analyze_window(...)`
  - 无消息（fetch 为空 / `build_summary_text` 返回空串）→ stdout 打印「该时间窗内没有新消息」，退出 0
  - 有 → `notifier.build_summary_text(...)` 打印到 **stdout**；**默认同时 `notifier.notify(...)` 推飞书 + 桌面通知**（兑现「默认任何情况都输出飞书」），`--quiet` 则只回 stdout、不推飞书/不弹通知
  - **只读**：`processed_ids=set()`、不写 state、不动轮询游标——与正在跑的 daemon 并行无冲突
  - 退出码：0 成功（含无消息）；非 0 出错（缺参→argparse 2；`start>=end` / 非整数 / config 非法 / fetch / analyze 失败）

> 注（YAGNI）：v1 **不**建正式的 Sink 类注册表——「解耦产出与投递」是本质，足够。多 sink 注册表 / `ask` 全 NL 透传 / 本地 digest 存储 / HTTP 传输均**不在 v1**，留待真有需求。

### 3. 时间窗与参数

- `--start <epoch>` / `--end <epoch>`：**均必填，Unix 时间戳（整数秒），无默认值**（调用方显式传入起止）。窗口 = `[fromtimestamp(start, TZ), fromtimestamp(end, TZ)]`。
- argparse `type=int, required=True`：任一缺失 → 用法错误（argparse 退出码 2）；非整数 → argparse 报错；`start >= end` → 自校验报错并退出非 0。无 duration 解析器。
- `--quiet`：抑制飞书/桌面投递，只回 stdout。

## 受影响模块

| 模块 | 改动 |
|---|---|
| `main.py` | 抽出 `_fetch_window` / `_analyze_window`（**留在 main.py**）；`poll_once` 改为调用它们（签名/行为不变，进度仍在 fetch 与 analyze 之间）；新增 `cmd_summarize(start_ts, end_ts, quiet)`；argparse 加 `summarize`（`--start`/`--end` `type=int,required=True` / `--quiet`）+ `sys.exit` 分发；help 标 ✅。无需 duration 解析器 |
| `notifier.py` | **不改**——复用现有 `build_summary_text(...)` 渲染 stdout 文本 |
| `AGENTS.md` / 包内 `SKILL.md` | 增加 `summarize` 说明：AI agent 按需汇总到 stdout |

**关键约束**：
- `_fetch_window`/`_analyze_window`/`cmd_summarize` 等**必须定义在 `main.py`**——现有 poll_once 测试 `@patch("lark_listener.main.Fetcher/Analyzer/Notifier")`，挪去新模块会让这些 patch 失效。
- `poll_once`/`_handle_message`/`_reply_bot`/`_add_reaction`/`_pending_change` 守护符号保持原位、原签名（CLAUDE.md #7）。
- `set_lark_profile(config["lark_cli_appid"])` 必须在 fetch 前调用（poll_once 已做；`cmd_summarize` 也要做）。
- start/end/processed_ids 由各传输层算好传入：poll_once 用 `state`（非 custom 路径）或 `custom_start`+`now`；summarize 用 `start=fromtimestamp(--start,TZ)`、`end=fromtimestamp(--end,TZ)`、`processed_ids=set()`（只读，不碰 state）。

## 测试策略（遵守 CLAUDE.md）

- `cmd_summarize`：`@patch("lark_listener.main.Fetcher"/"Analyzer"/"Notifier")`（与现有 poll_once 测试同款 patch 目标），验：有消息→stdout 含汇总文本 + 默认调 `Notifier().notify` + fetch 收到的 start/end 等于传入时间戳换算值；`--quiet`→只 stdout、不调 notify；无消息→提示 + 退出 0；`start >= end`→退出非 0；缺 `--start`/`--end`→argparse 退出 2；只读（不写 state）。
- **回归（最重要）**：现有 `poll_once` 全部用例（full_cycle / manual 进度 / 无消息 / auto）须**仍绿且不改 patch 目标**——证明重构未改 daemon/bot 行为、helper 仍在 main.py。
- stdout 渲染复用 `notifier.build_summary_text`，其本身已有覆盖，不重复测。
- 收尾 `python3 -m pytest -q` 全绿；`./dev-test.sh smoke` 通过。

## 已确认取舍
1. v1 仅 `summarize` 一个命令；`ask` / 本地存储 / HTTP / 正式 sink 注册表均不做。
2. on-demand 默认也推飞书，`--quiet` 抑制。
3. 时间窗用 `--start`/`--end` **Unix 时间戳（整数秒）、均必填、无默认、须 `start < end`**（无 duration 解析器）。

## 不在本期范围
- `ask "<自然语言>"` 全 NL 透传（含改配置的「确认」两步交互）
- 本地 digest 存储 + `digest --last` pull 回读
- HTTP/远端传输、对外 webhook/订阅者 callback
- 正式 Sink 类注册表

## 二次 review 修正记录（对照代码）
- **不新写 `render_text`**：`notifier.build_summary_text(...)` 已现成（`notify` 内部就用它），stdout 直接复用；飞书与 stdout 同一份文本。
- **helper 必须留 `main.py`**：现有 poll_once 测试 `@patch("lark_listener.main.Fetcher/Analyzer/Notifier")`，挪去 `digest.py` 会让 patch 失效、破坏既有测试。开放问题 #3 据此定为「main.py」。
- **fetch 与 analyze 拆两步、非黑盒**：poll_once 的 manual 进度必须在 fetch 与 analyze 之间发出，合成黑盒会破坏其时序语义。
- 补充：`set_lark_profile` 须在 fetch 前调；start/end/processed_ids 由传输层算好传入（summarize 恒只读、不碰 state）。
