# 会话分类（勿扰/普通/特别关注）与请求合并 — 设计文档

日期：2026-06-11
状态：已与用户逐项确认
版本目标：0.2.0

## 背景与目标

现状所有群一视同仁：@我/@所有人/关键词命中才收，`include_at_all` 全局开关控制 @所有人。
用户的真实使用习惯是「绝大多数群免打扰，少数重点群不免打扰」，希望服务按飞书自身的
免打扰状态自动分级处理：

1. **勿扰群**（已免打扰）：@所有人 仅在命中普通关键词时才收。
2. **普通群**（未免打扰）：@所有人 无条件收。
3. **特别关注群**（未免打扰的群，且总开关开启）：窗口内**全量**消息都收；AI 摘要
   除常规概括外，还须围绕「普通关键词 + 关注关键词」展开分析。
4. 顺带优化 poll 流水线：把随会话数线性增长的请求合并为常数次命令。

## 1. 会话分类模型

分类在**每次产出汇总时**现算（poll 开始 / 手动 summarize 时刷新一次未免打扰列表）。
mute 状态没有消息级消费点，两次汇总之间的变化无需感知——产出时刷新即等效实时。

| 会话类型 | 判定 | @我 | @所有人 | 关键词命中 | 其它消息 |
|---|---|---|---|---|---|
| 勿扰群 | 群聊 且 ∉ 未免打扰集合 | 收 | 仅命中关键词才收（归关键词区） | 收 | 不收 |
| 普通群 | 群聊 且 ∈ 未免打扰集合（special_focus 关闭时） | 收 | 收 | 收 | 不收 |
| 特别关注群 | `special_focus.enabled` 且 群聊 且 ∈ 未免打扰集合 | 收 | 收 | 收（归特别关注区） | **全收** |
| 私聊 | chat_type=p2p | — | — | — | 全收（现状不变，不受 mute 影响） |

**归类优先级**（一条消息只归一类；卡片展示顺序与此完全一致）：

```
私聊 > @我 > @所有人 > 特别关注 > 关键词命中
```

- @我 / @所有人 在特别关注群中仍单列（用户确认）。
- 特别关注群中命中关键词的消息**不进关键词区**，由特别关注区统一认领
  （实现：关键词搜索结果中属于特别关注群的消息直接跳过）。
- 特别关注区是该群剩余消息的兜底；勿扰群 @所有人 在 @我搜索阶段跳过且不标
  seen，留给关键词搜索捞（命中即归关键词区，沿用现有机制）。
- @所有人 检测沿用文本启发式，含 `@_all` 占位符（0.1.3 修复）。
- `special_focus.enabled=true` 时「普通群」类型实际不存在——所有未免打扰群都升级为
  特别关注群；表中普通群一行仅在开关关闭时生效。
- **排除优先**：`exclude_chats` 中的群在每一步都被过滤，即使未免打扰也不会成为
  特别关注群。
- **绑定不改变分类**：把群写进 `special_focus.chats` 只是为它叠加关注关键词，
  **不会**使其成为特别关注群（分类只由 is_muted 决定）；绑定的群若被免打扰，
  其关注词静默失效（`doctor --deep` 会检查并提示，见 §7）。

## 2. mute 状态获取

- 唯一数据源：`lark-cli im +chat-list --exclude-muted`（user 身份，默认仅群聊）。
  免打扰是用户维度设置，消息搜索结果与 `im chats get`（已实测 29 个字段）均不携带，
  无法在 fetch 消息时顺带获取。
- 不需要全量群列表：勿扰判定 = `chat_type == "group" 且 chat_id ∉ 未免打扰集合`
  （chat_type 来自消息自身）。每轮 `⌈未免打扰群数/100⌉` 次请求（refresh 内部按
  page_token 翻页；用户当前 15 个未免打扰群 = 1 次，实测约 1~2 秒）。
- **降级**：本轮拉取失败 → 用上一轮结果；首轮即失败 → 所有群按勿扰处理（宁可少收
  不误收）+ 特别关注全量抓取跳过 + 日志 warning。不抛异常（best-effort 铁律）。

## 3. 配置格式（保持 YAML，不换格式）

评估结论：YAML + ruamel round-trip 是本项目刚需（bot/CLI 程序化改配置保留用户注释），
JSON 无注释、TOML 嵌套对象列表编辑笨拙，换格式纯成本无收益。**不换**。

```yaml
keywords: [SDK, PARK, ...]        # 普通关键词，不变；对特别关注群兼任全局关注词
exclude_chats:                     # 替代 exclude_chat_ids（结构升级）
  - chat_id: oc_xxx
    name: 某某群                   # 服务自动补全；手填则保留不覆盖
special_focus:
  enabled: false                   # 总开关，默认关闭
  max_messages: 20                 # 每个特别关注群单轮消息上限，缺省 20
  chats:                           # 可选：绑定单群的关注关键词（与普通关键词叠加）
    - chat_id: oc_yyy
      name: 某某群
      keywords: [扩容]
```

`special_focus` 下**不单设全局关注关键词**：顶层 `keywords`（普通关键词）即全局
关注词，对特别关注群的 AI 分析天然生效；`chats[].keywords` 仅为单群叠加专属词。

- **`include_at_all` 删除**：行为被分类规则取代。load_config 读到旧键时忽略并
  log 提示；setup 向导、config.example.yaml、文档同步移除；含 `config_editor.py`
  中该字段的 bool 解析特例代码。
- **兼容迁移**：旧 `exclude_chat_ids`（纯 id 列表）load 时兼容读取（内存归一为
  新结构）；首次回写配置时自动迁移为 `exclude_chats` 格式（ruamel 保留注释、原子写、
  保持 0600）。
- **自动补名**：每轮加载配置后，对 `exclude_chats` / `special_focus.chats` 中缺
  `name` 的条目经 `im chats get` 查名（仅缺名时发生、每条一次），best-effort 原子
  回写；失败留空下轮再试，不影响轮询。
- **钳制**（防 KeepAlive 崩溃循环）：`special_focus.enabled` 非 bool → false；
  `max_messages` 非负 int（非法 → 20）；`chats` 强制列表（条目须含 str 的
  chat_id，`keywords` 强制 list[str]）、坏条目丢弃；`exclude_chats` 兼容两种
  形状、坏值不崩。
- **保护键**：`special_focus`、`exclude_chats` 不进受保护键（bot/CLI 可改）；
  `removes_bot_chat` 防自反馈守卫适配新结构。

## 4. 抓取流水线（新模块 chats.py + fetcher 改造）

新模块 `chats.py`：

- `ChatRegistry.refresh()` — 1 次 `--exclude-muted` 调用 → `{chat_id: name}`；
  内部保存上一轮结果用于降级。
- `ChatRegistry.classify(chat_id, chat_type)` → MUTED / NORMAL / SPECIAL（纯函数核，
  单测直测）。
- `ChatRegistry.name_of(chat_id)` — 供配置补名（未命中时回落 `im chats get`）。

Fetcher 注入 registry，每轮请求序列与次数公式：

```
每轮命令数 = 3 + K + ⌈S/10⌉ + ⌈M/10⌉   （另：分页与一次性补名，见下）
```

| 步骤 | 次数 | 固定/变动 |
|---|---|---|
| 1. 未免打扰列表 | 1 | 固定 |
| 2. p2p 搜索 | 1 | 固定 |
| 3. @我 搜索（@all 按所在群分类分流） | 1 | 固定 |
| 4. 关键词搜索（query 多词为 AND 语义，实测无法合并为 OR） | K=关键词数 | 随配置固定（当前 8） |
| 5. 特别关注全量抓取（chat_id 逗号分隔合并，每批 10 群） | ⌈S/10⌉ | 随特别关注群数；关闭/为 0 时 0 次 |
| 6. 上下文合并抓取（命中会话合并，特别关注群跳过——全量已含） | ⌈M/10⌉ | 随命中会话数；无命中 0 次 |
| 补名（仅缺 name 条目，补完不再发） | 每条 1 次 | 一次性 |

说明：

- 「合并 1 次」指 1 个 lark-cli 命令；`--page-all` 时每页（50 条）仍是一次底层
  HTTP 请求，消息量大时 HTTP 数随条数增长——与现状相同且为 API 强制，合并消除的
  是**命令数随会话数的线性增长**。
- 步骤 5/6 拉回后本地按 chat_id 分组：特别关注每群截最近 `max_messages` 条、上下文
  每群截最近 `context_messages` 条；截断时 log 丢弃数量（不静默）。
- 排除过滤（exclude_chats）与 seen_ids 去重在每步照旧；特别关注消息照常进
  `processed_ids`。

## 5. AI 分析（analyzer）

- 每个特别关注会话块头部标注 `[特别关注]`，有绑定词时附
  `（本群关注关键词：绑定词）`；普通关键词已在 prompt 全局段落，无需重复。
- 要求：特别关注会话的 summary 除常规概括外，须围绕普通关键词（+ 该群绑定词，
  如有）展开分析；relevance 按合并后的词集评估。
- 消息量由 `max_messages`（默认 20）与现有 `context_messages` 控制。

## 6. 卡片与通知（notifier）

- 封套新增 `category: "special"`；`_CATEGORY_ORDER` 调整为与归类优先级一致：
  **私聊 → @我 → @所有人 → 特别关注 → 关键词命中**（注意 @所有人 从现末位前移）。
- 卡片新增「🟪 特别关注」表格区；macOS 通知短名同步加「特别关注」。
- Markdown 回退与 stdout JSON 同源（`build_summary_response` 唯一事实源）自动获得新区。

## 7. 周边同步

- **doctor**：校验 `special_focus` 配置形状；`--deep` 真探 `chat-list --exclude-muted`，
  并检查 `special_focus.chats` 绑定的群当前是否处于免打扰（是则提示关注词不生效）。
- **setup**：不新增提问（special_focus 默认关闭，example 注释引导）。
- **config_cli / config_editor**：支持 `special_focus.*` 嵌套路径；`exclude_chats`
  按 chat_id 增删。
- **intent.py（bot 自然语言改配置）**：意图解析 prompt 中 `exclude_chat_ids` 的
  引用升级为 `exclude_chats` 新结构，并纳入 `special_focus.*` 可改字段，否则 bot
  指令会生成旧键。
- **文档**：README、CLAUDE.md 模块表、agent skill（机读封套的 category 枚举）同步。

## 8. 失败与降级汇总

| 失败点 | 行为 |
|---|---|
| mute 列表拉取失败 | 用上轮缓存；无缓存全按勿扰 + 跳过特别关注抓取 + warning |
| 特别关注/上下文合并抓取失败 | 该轮该部分缺失，不崩（返回空，沿用 `_search` 现有兜底） |
| 配置补名失败 | name 留空，下轮再试 |
| 旧配置 / 坏配置 | 兼容读取 + 钳制，绝不让消费点 TypeError |

## 9. 测试策略（仓库规范：纯函数优先 + TDD）

- `classify` 分类矩阵全覆盖（含 enabled 开关、降级态）。
- @all 分流：勿扰群跳过不标 seen / 普通群收 / 特别关注群收；`@_all` 回归不破坏。
- 关键词搜索跳过特别关注群消息（优先级 3 的行为）。
- 合并抓取：分组、按群截断、丢弃日志、分块（>10 群/会话）。
- 配置：新格式读取、旧 `exclude_chat_ids` 兼容与回写迁移、钳制、`include_at_all` 忽略。
- analyzer prompt：特别关注标注与绑定词注入（有/无绑定两种形态）。
- notifier：special 类别行、新顺序、空特别关注不渲染区块。
- mock subprocess / 隔离 env，照 dev-test 约束，不真发飞书。

## 非目标

- 不换配置文件格式（保持 YAML）。
- p2p 会话的 mute 状态不影响行为（仅群聊参与分类）。
- 不做 mute 变化的事件订阅实时响应（产出时刷新已等效实时）。
- 不合并关键词搜索（API AND 语义限制）。
