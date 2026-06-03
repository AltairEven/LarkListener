# 触发表情反馈 + fetch 进度通知 设计

日期：2026-06-03

## 目标

提升手动汇总的即时反馈体验：

1. **收到表情反馈**：bot 收到任何用户消息时，立刻给该消息加一个 `Get` 表情，作为"已收到"的即时反馈。
2. **fetch 进度通知**：汇总流程在 fetch 完成后，告知用户找到了多少条相关消息，以及预估的 AI 分析时间，让用户知道还要等多久。

## 背景

当前手动触发的流程（`main.py`）：

```
收到触发消息 → 回复「⏳ 正在汇总 X~Y 的消息...」 → fetch → 拉上下文 → AI 分析 → 发汇总
```

问题：从"正在汇总"到最终汇总之间没有任何进度反馈，而消息量大时 AI 分析可达约 1 分钟（实测 74 条消息 + 71 条上下文 ≈ 55 秒），用户不知道还要等多久。

技术可行性已确认：`lark-cli im reactions create` 支持以 bot 身份给消息加表情，参数为 `message_id` 与 `reaction_type.emoji_type`。

## 设计

### 组件 1：收到消息立刻加 Get 表情

加表情放在 `_bot_listener` 线程收到事件的那一刻（而非等主循环 AI 判断意图），以实现"立刻"反馈。因此 `message_id` 只在 listener 线程内使用，不需要传入主循环。

- `_bot_listener` 解析事件时额外提取 `event.event.message.message_id`。
- 新增 best-effort 函数 `_add_reaction(message_id, emoji_type="Get")`：
  ```
  lark-cli im reactions create --as bot \
    --params '{"message_id":"om_..."}' \
    --data '{"reaction_type":{"emoji_type":"Get"}}'
  ```
  失败（消息已撤回、权限不足、网络等）仅记日志，不影响后续流程。
- 流程：收到消息 → 先加表情 → 再把内容入队列。

**已确认的取舍**：用户选择"收到即加"，因此**任何**发给 bot 的消息（包括非汇总意图的，如"你好"）都会被加 Get 表情。实测确认：正确的 `emoji_type` 是 `Get`（驼峰，非全大写 `GET` —— 后者返回 231001 reaction type is invalid）。

### 组件 2：去掉"正在汇总"，fetch 后发"数量 + 预估"

- `main.py` 手动触发分支：**删除**两处 `_reply_bot(..."⏳ 正在汇总 X~Y 的消息...")`（custom_start 与默认时间范围两个分支）。Get 表情已承担"已收到"语义。
- `poll_once`：fetch 得到 `categorized` 后，若 `is_manual` 且相关消息数 `N > 0`，发一条：
  ```
  📊 {start} ~ {end} 找到 N 条相关消息，预计分析约 {时长}
  ```
- `N` = 各分类（P2P / AT_ME / KEYWORD / AT_ALL）匹配消息总数，**不含**上下文消息。
- 找到 0 条时：保留现有的 `📭 {start} ~ {end} 期间没有新消息`，不发进度通知。
- 此通知**仅手动触发**（`is_manual=True`）时发送；定时轮询不发。

### 组件 3：预估 AI 分析时间（新 helper，放 `analyzer.py`）

- `estimate_ai_seconds(n: int) -> int`：线性近似 `round(10 + 0.6 * n)`，实测拟合（1 条 ≈ 10s、74 条 ≈ 55s），并封顶 180s 以防极端值。
- `format_duration(seconds: int) -> str`（不自带"约"，由文案前缀统一加）：
  - `< 60` → `"{seconds} 秒"`
  - 否则 → `"{ceil(seconds/60)} 分钟"`
- 通知文案为 `f"预计分析约 {format_duration(s)}"`，最终形如「预计分析约 1 分钟」「预计分析约 45 秒」。

放在 `analyzer.py`（与 AI 调用同模块），由 `main.py` 的 `poll_once` import 使用。

## 数据流

```
用户发消息
  → [bot listener 线程] 提取 message_id → _add_reaction(Get) → content 入队列
  → [主循环] 取出 content → _parse_trigger_with_ai 判断意图
     → 若是触发: poll_once(is_manual=True)
        → fetch → N 条相关消息
        → 若 N>0: _reply_bot("📊 {范围} 找到 N 条，预计分析约 X")
        → fetch_context → AI 分析 → notify(发汇总)
     → 若非触发: 忽略（表情已加，保留）
```

## 错误处理

- `_add_reaction`：best-effort，捕获所有异常仅记日志（与现有 `_reply_bot` 一致）。
- 进度通知 `_reply_bot`：已是 best-effort（前序改动已加 try/except）。
- 预估函数为纯计算，输入恒为非负整数，无外部依赖。

## 测试

- `estimate_ai_seconds` / `format_duration`：单测覆盖边界（0、拟合点 1 与 74、超大值封顶）与秒/分钟格式切换。
- `_add_reaction`：mock `subprocess.run`，断言命令含正确的 `message_id` 与 `emoji_type=Get`、`--as bot`；并验证异常被吞。
- `poll_once(is_manual=True)`：mock `_reply_bot`，验证 `N>0` 时发进度通知（含数量与"约"）、`N=0` 时只发"没有新消息"、定时轮询（`is_manual=False`）时不发进度通知。
- bot listener 提取 message_id 并调用 `_add_reaction`（若可在不重构线程的前提下测到）。

## 范围与非目标

- 不改动定时轮询的行为（除 `poll_once` 内新增的 `is_manual` 分支）。
- 不做 reaction 的去重/撤销（非触发消息的表情保留，不删除）。
- 预估仅为粗略近似，不追求精确；不引入运行时自适应学习。
