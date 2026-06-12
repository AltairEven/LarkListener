# LarkListener 交互优化设计：间隔 0 关闭轮询 + 卡片/JSON 输出

日期：2026-06-10
状态：待实现（轻量 spec → TDD）

## 目标

1. `poll_interval = 0` 视为「关闭自动轮询」：服务继续运行，bot 在线、按需 `summarize`、改配置照常，仅不再定时拉取推送。
2. 所有汇总产出基于**统一响应封套** `{code, errorMsg, data}`（便于外部解析），每个消费者先解析封套再格式化：
   - `summarize` 命令 stdout → 直接打印封套 JSON（成功/空/错误都是合法封套）。
   - 飞书 bot 私聊推送 → **先解析封套**，再用 `data` 渲染**飞书交互卡片**（`table` 组件）。

非目标：macOS 桌面通知格式不变（仍为计数文本）；不做无关重构。

## 改动一：`poll_interval = 0` ＝ 关闭自动轮询

| 位置 | 改动 |
|---|---|
| `config_editor.py` `_coerce_scalar`（L80-81） | `poll_interval` 校验由 `n <= 0` 改为 `n < 0`；错误文案改为「poll_interval 需为非负整数（0=关闭自动轮询）」。**此函数同时被 bot 改配置路径与 `config_cli` 复用，改这一处即覆盖两条路径** |
| `config_cli.py` | 复用 `config_editor._coerce_scalar`，无独立 `poll_interval` 校验，自动放行 0；无需改动 |
| `setup_wizard.py`（~L169） | 向导轮询间隔输入允许 0（校验/提示放行） |
| `main.py` `run()` 循环（L462-480） | 仅当 `interval > 0` 才调用 `poll_once` 并重置 `error_count`；等待用 `timeout = interval if interval > 0 else None`，间隔 0 时阻塞等待 bot 触发，不空转忙轮询。**运行时切换可行**：改间隔的 bot 消息也经 `_bot_listener` 进 `_trigger_queue`（L150），会唤醒 `timeout=None` 的阻塞——下一轮 reload 到 interval>0 即恢复轮询，反之亦然 |
| `main.py` 启动消息（L454） | 间隔 0 时改为「✅ LarkListener 已启动（自动轮询已关闭，仅按需汇总）。发「帮助」可查看或修改配置。」 |
| `doctor.py` `check_last_poll`（L68） | `poll_interval == 0` 时跳过轮询时效检查，返回 ok 并标注「自动轮询已关闭（poll_interval=0）」 |

约束守护：
- `poll_once`/`_handle_message`/`_reply_bot` 等守护符号保持原位、原签名（CLAUDE.md 约束7）。
- run 循环重构后，`poll_once` 仍在 main.py，三件套 `@patch` 不受影响。

## 改动二：统一响应封套 `{code, errorMsg, data}` 作为唯一契约

核心：先把汇总归一成**一份统一响应封套**，stdout、bot 卡片、Markdown 兜底**全部先解析这份封套再格式化**。以后新增任何输出/消费方都基于它。

### 封套结构

```
{
  "code": 0,            # 0 成功；非 0 失败（与命令退出码一致，1=通用错误）
  "errorMsg": "",       # code != 0 时的人读错误信息；成功为空串
  "data": {             # code != 0 时为 null
    "period": {"start": "06-10 15:00", "end": "15:30"},
    "conversations": [  # 按分类顺序 P2P→@我→关键词→@所有人，各类内紧急优先；空汇总为 []
      {
        "category": "p2p" | "at_me" | "keyword" | "at_all",
        "label": "私聊消息" | "@我" | "关键词命中" | "@所有人",
        "title": "对方名/群名",
        "chat_id": "...",
        "link": "https://applink.feishu.cn/...",
        "urgency": "urgent" | "normal" | "low",
        "relevance": "high" | "medium" | "low",
        "matched_keyword": "",   # 仅 keyword 类别非空
        "summary": "AI 摘要，可能为空",
        "snippet": "最相关/最后一条非我消息的截断内容（≤80）",
        "count": 3               # 该会话消息数
      }
    ]
  }
}
```
**成功但无可汇总内容** = `code:0, errorMsg:"", data:{period, conversations:[]}`（沿用现有 has_others 逻辑）。

### 1）封套构建（唯一事实源，新增于 `notifier.py`）

```
build_summary_response(categorized, analysis, start, end, my_user_id) -> dict   # 成功封套，code=0
error_response(msg, code=1) -> dict                                              # {code, errorMsg:msg, data:None}
```
**实现要点**：`build_summary_response` 需吸收现有 `_format_conversation` 的核心逻辑——私聊对方名解析（取首条非我消息 sender.name）、群名回退 `群聊(chat_id[-8:])`、相关消息选取（`relevant_message_id` 优先，否则最后一条非我消息）、`format_msg_content(m, for_display=True)` + 80 字截断得 snippet。这些逻辑从 `_format_conversation` 迁入，`_format_conversation` 随旧 `build_summary_text` 一并退役或重写。

### 2）格式化器：输入是封套 dict，先解析 code 再渲染

| 函数 | 行为 |
|---|---|
| `build_summary_card(resp: dict) -> dict \| None` | 解析封套：`code != 0` 或 `data` 无会话 → 返回 `None`（bot 不推空/错误）。否则用 `data` 建飞书卡片 2.0：header「📬 消息汇总（period.start - period.end）」；每个非空分类 = 一个 `markdown`「**━━ 私聊消息（N）━━**」+ 一个 `table`。列：`会话`(🔴 前缀/`（命中：kw）`) / `摘要`(summary 优先否则「"snippet"」) / `原文`(`[查看](link)`)。每会话一行。 |
| `build_summary_text(resp: dict) -> str` | 卡片发送失败时的 Markdown 兜底，从封套 `data` 渲染。`code != 0` 或无会话 → 空串。**注意**：现有 `build_summary_text(categorized, analysis, ...)` 签名变更；既有 11 个 `test_build_summary_*` 改为先 `build_summary_response(...)` 再传入，断言子串不变。 |

### 发送链路 `Notifier`

- `notify()`：`resp = build_summary_response(...)`；`card = build_summary_card(resp)`；`card` 为 `None` 则不发；否则 `_send_bot_card(card)`。
- `_send_bot_card(card)`：`lark-cli im +messages-send --user-id <id> --content <json.dumps(card)> --msg-type interactive --as bot`。
- **best-effort**：卡片 subprocess 失败 → 回退 `_send_bot_message(build_summary_text(resp))` 发 Markdown → 再失败才 warning 放弃。**绝不抛**（CLAUDE.md 约束6），否则冻结 `last_poll_time` 造成重复推送循环。
- macOS 通知 `_send_macos_notification` 不变。

### `cmd_summarize`（`main.py`）

- 成功：`resp = build_summary_response(...)`；错误（参数错、配置读失败、时间戳非法、汇总异常）：`resp = error_response(msg)`。
- stdout 一律 `print(json.dumps(resp, ensure_ascii=False, indent=2))`——成功/空/错误都是合法封套，不再有 `📭`/`❌` 裸中文文本。
- 退出码 = `resp["code"]`（0 成功，1 错误），与封套一致。
- 默认仍推飞书（走 `Notifier.notify` → 卡片，同样基于这份封套）；`--quiet` 只回 stdout。
- `main.py` 顶部 import 从 `build_summary_text` 改为 `build_summary_response`/`error_response`（`build_summary_text` 此后仅 `Notifier` 内部兜底用）。

## 测试（TDD，纯函数优先）

新增/修改单测：
- `build_summary_response`（唯一事实源）：封套 `code=0/errorMsg=""/data`，`data` 含 `period` 与 `conversations`；分类顺序、紧急优先、keyword 携带 matched_keyword、snippet 截断、count 正确；has_others 为空时 `conversations: []`。
- `error_response`：`{code: 1, errorMsg: msg, data: None}`。
- `build_summary_card(resp)`：`code=0` 且有会话 → dict 含 `table` 元素、列数、行数＝会话数、🔴 出现在紧急会话、链接进入「原文」列；`code != 0` 或空会话 → `None`。
- `build_summary_text(resp)`：从封套 `data` 渲染；既有 11 个 `test_build_summary_*` 改为先 `build_summary_response` 再传入，断言子串不变。
- `cmd_summarize` stdout：成功/空/错误均输出可 `json.loads` 的封套；退出码＝`code`。
- `Notifier.notify`：卡片成功路径调用 `--msg-type interactive`；卡片失败回退 Markdown；两路均不抛。
- run 循环：`interval == 0` 不调用 `poll_once`、`queue.get(timeout=None)`（可抽小 helper 便于测，或断言 sleep/queue 调用）。
- `config_editor`：**改造既有 `test_poll_interval_rejects_non_positive`（test_config_editor.py:72）**——`poll_interval=0` 现应通过、负数（-1）拒绝并含新文案。
- `doctor.check_last_poll`：`poll_interval=0` 跳过并标注。

### 实施顺序（先消风险）

**第 0 步先 spike 卡片 schema**：用 `lark-cli im +messages-send --msg-type interactive --content <最小 table 卡片 JSON> --dry-run`（必要时给用户自己 DM 真发一条）确认：① `interactive` 接受自定义卡片 JSON；② `table` 元素与列 `data_type` 的确切键名（`lark_md` vs `markdown`）；③ 链接/🔴 在单元格内渲染正常。**schema 验通后再建格式化器**，避免在错误假设上构建。兜底 Markdown 能防服务崩，但防不住「渲染丑」。

收尾：`python3 -m pytest -q` 全绿；`./dev-test.sh` smoke 通过。文档（README/SKILL.md/CLAUDE.md）按需补「poll_interval=0」与「summarize stdout 现为 JSON」说明。
