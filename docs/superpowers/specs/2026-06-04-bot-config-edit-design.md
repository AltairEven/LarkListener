# 通过 Bot 修改配置 — 设计文档

日期：2026-06-04

## 背景

LarkListener 的 bot 已经能监听私聊消息，并用 AI 识别"汇总/总结"意图触发一次消息汇总
（`main.py` 的 `_bot_listener` → `_parse_trigger_with_ai`）。配置存于
`~/.lark_listener/config.yaml`，每个轮询周期重新加载，改配置无需重启。

目前改配置要手动编辑 YAML 文件。本设计让用户通过给 bot 发**自然语言**消息来查看和修改配置。

## 目标

用户给 bot 发自然语言消息，AI 解析意图后：

- **查看配置**：回复当前配置值。
- **修改配置**：先回复待确认的变更摘要，用户确认后写入 `config.yaml`（下次轮询自动生效）。
- **帮助**：列出可改项与示例。

## 约束与决策

- **黑名单保护**：`ai` 和 `notify` 两个顶层字段受保护，不可通过 bot 修改；其余顶层字段默认都可改。不维护可改字段白名单/注册表。
- **类型从生效配置推断**：字段类型（int / bool / list）从**生效配置**（`config.py` 的 `DEFAULTS` 合并 config.yaml，即 `load_config` 的结果）推断，**而非**原始文件——因为用户可能省略某字段靠默认值生效（如 `context_messages`），原始文件里并无该 key。list 字段（如 `keywords`、`exclude_chat_ids`）支持 set/add/remove；标量字段（如 `poll_interval`、`include_at_all`、`context_messages`）支持 set。写回时若该 key 不在原始文件中，则新增。
- **先确认再写入**：AI 解析出变更后，bot 回复变更摘要，用户回复"确认"后才写入。
- **保留注释**：引入 `ruamel.yaml` 依赖做 round-trip 读写，保留用户手写的注释与字段顺序。（config.yaml 是手工带注释维护的。）
- **sender 校验**：仅 `notify.user_id` 本人可执行配置类操作（查看/修改/帮助/确认/取消）。`summary` 汇总触发保持原样、不限制。
- 不新增任何配置文件，继续只用 `~/.lark_listener/config.yaml`。

## 架构与模块

新增两个模块，主循环（`main.py`）做调度，职责隔离。

### `lark_listener/config_editor.py` — 配置编辑领域逻辑（无 AI、无 IM）

- `PROTECTED = {"ai", "notify"}`。
- `load_roundtrip(path)` / `dump_roundtrip(path, data)`：用 `ruamel.yaml`（`YAML(typ="rt")`）round-trip
  读写，保留注释与字段顺序。写入采用原子方式：写临时文件再 `os.replace`（复用 `state.py` 的模式）。
- `apply_changes(path, changes) -> ApplyResult`：
  1. 逐条校验：拒绝 `PROTECTED` 字段。
  2. 按生效配置里该字段的类型校验：list 字段允许 op ∈ {set, add, remove}；标量字段仅 set。
  3. **值类型强制转换**：AI 返回的 `value` 可能是字符串（如 `"600"`）或原生类型，先按字段类型 coerce（int / bool / str），转换失败则回错误，不写入。
  4. 标量 sanity 校验：`poll_interval` 为正整数；`context_messages` 为非负整数；`include_at_all` 为 bool。
     （这些按字段名做轻量 sanity 检查，非白名单；未知标量字段按"能转成现值类型"校验。）
  5. list 操作：add 去重追加，remove 不存在则忽略，set 整体替换。
  6. 计算 human-readable diff，原子写回（若 key 不在原始文件中则新增）。
  - 返回值含：是否成功、diff 文本（成功时）、错误信息（失败时）。
- `compute_diff(path, changes) -> str`：仅计算变更摘要文本，不写入（供"待确认"提示用）。
- `render_config(path) -> str`：渲染当前配置文本（脱敏：不展示 `ai.api_key`）。
- `render_help() -> str`：渲染可改项 + 示例文本，并说明 `ai`、`notify` 不可改。

### `lark_listener/intent.py` — 统一 AI 意图识别

取代现有 `main._parse_trigger_with_ai`。

- `parse(message, config) -> Intent`：单次 AI 调用，把消息分类为：
  - `summary`：原汇总触发，含可选 `start_time`（保留现有时间解析逻辑，行为不变）。
  - `config_view`：查看配置。
  - `config_modify`：含 `changes: [{field, op, value}]`。
  - `config_help`：帮助。
  - `confirm` / `cancel`：确认 / 取消待定变更。
  - `none`：无匹配。
- prompt 内嵌当前 config（脱敏，不含 `ai.api_key`），让 AI 能解析相对操作
  （"加个关键词上线" → `{field: keywords, op: add, value: 上线}`），并明确告知 `ai`/`notify` 不可改。
- 复用现有三家 provider（claude / openai / ollama）的调用方式。

### `main.py` — 调度 + 内存待确认状态

- 模块级 `_pending_change`（单用户工具，无需 per-user map）。
- 队列改为传 `(content, sender_id)`：`_bot_listener` 从事件取
  `event.event.sender.sender_id.open_id`。
- 收到消息 → `intent.parse()` → 分派：
  - `summary` → 原 `poll_once`（不限制 sender）。
  - 其余配置类（view/help/modify/confirm/cancel）→ 先校验 `sender_id == config.notify.user_id`，
    非本人回复"仅本人可修改配置"并忽略。
  - `config_view` / `config_help` → 直接回复 `render_config` / `render_help`。
  - `config_modify` → `compute_diff`，存 `_pending_change`，回复"将 …，回复'确认'生效"。
  - `confirm`（仅当有 pending）→ `apply_changes`，回复结果，清 pending。
  - `cancel` → 清 pending，回复"已取消"。
  - 新的 `config_modify` 覆盖旧 pending。

## 数据流（修改配置的一次完整交互）

```
用户: "轮询间隔改成10分钟"
  → _bot_listener 收到 → 加 GET reaction → 入队 (content, sender_id)
  → intent.parse() → {type: config_modify, changes:[{field:poll_interval, op:set, value:600}]}
  → sender 校验通过
  → config_editor.compute_diff → "轮询间隔: 300 → 600 秒"
  → 存 _pending_change，bot 回复 "将修改 轮询间隔: 300 → 600 秒，回复"确认"生效"
用户: "确认"
  → intent.parse() → {type: confirm}（有 pending）
  → config_editor.apply_changes 原子写回 → bot 回复 "✅ 已更新，下次轮询生效"
```

## 错误处理

- 解析到修改 `ai`/`notify` → 回复"ai / notify 配置受保护，无法通过 bot 修改"。
- 非本人发起配置操作 → 回复"仅本人可修改配置"，忽略。
- 校验失败（非数字、负数、类型不符）→ 回复具体原因，不写入。
- AI 解析异常 / JSON 损坏 → 沿用现有 best-effort：记日志，回复"没听懂，可发'帮助'查看用法"。
- `confirm` 但无 pending → 回复"当前没有待确认的修改"。
- 写文件失败 → 回复错误信息；整个 trigger 处理包在 try/except 中，保证服务不崩
  （避免 launchd KeepAlive 触发重启循环，沿用现有处理）。

## 测试

- `config_editor` 单测：protected 字段拒绝；list set/add/remove（含去重、remove 不存在）；
  标量校验（poll_interval 负数 / 非数字、include_at_all 非 bool）；ruamel 注释保留
  （写回后断言原注释仍在）；原子写。
- `intent` 单测：mock AI 返回各类型 JSON，断言分类与 `changes` 结构解析正确；
  `summary` 时间解析行为与现状一致。
- **迁移现有测试**：`tests/test_main.py:81-117` 现有 3 个 `_parse_trigger_with_ai` 用例
  （invalid/valid start_time、非触发）随逻辑迁入 `intent.py`，保证 summary 行为不回归。
- 遵循 `tests/` 现有风格。

## 可发现性

- 启动消息（`main.py` 的 `✅ LarkListener 已启动`）追加一句提示：可发"帮助"查看/修改配置。
- README 补一行说明 bot 改配置的用法。

## 不在范围内（YAGNI）

- 修改 `ai` / `notify`（明确受保护）。
- 多用户 / per-user 待确认状态（本工具单用户）。
- 待确认变更的超时过期（新 modify 覆盖即可，保持简单）。
