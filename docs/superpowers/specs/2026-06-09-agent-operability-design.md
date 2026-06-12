# LarkListener — 安装后 AI Agent 可操作性设计

日期：2026-06-09
状态：已批准，待实现

## 背景与问题

LarkListener 装完后，用户机器上只剩 `~/.lark_listener/` 和 PATH 里的 `lark-listener` 命令——**没有 AGENTS.md**（它是安装期从 GitHub raw 抓取的产物，装完即失）。于是后续任意一个 AI 会话想帮用户「启停 / 配置 / 查错 / 解决运行时问题」时，面临两重障碍：

1. **不可操作**：`status` 只 print 中文+emoji 且退出码恒为 0；`config`/`setup`/`uninstall` 全是交互式（GUI 编辑器 / `input()` 确认），AI 碰不了；错误只能 `tail stderr.log` 自己脑补。
2. **不可发现**：AI 不知道这工具存在、不知道哪些命令安全、不知道怎么诊断。

## 目标

让**安装后**的服务能被 AI Agent 可靠地启停、配置、查错、解决运行时问题，且 AI 无需预先知道该工具即可发现「该怎么操作」。

## 非目标（本期不做）

- MCP 适配器（只在推送层留可插拔接口，不实现）。
- 跨 agent 的本地 `~/.lark_listener/AGENTS.md` 兜底文件。
- 改动 bot 聊天那套配置入口（复用其底层纯函数，不动交互）。

## 架构原则

**契约的唯一事实源放进二进制**（`--help` + `doctor`）。推送层（skill / 未来的 MCP）一律写**薄**、统统 defer 回 CLI，从根上避免「契约多处维护、版本漂移」。

分两层：

- **地板层（通吃所有能跑 shell 的 agent，拉取式）**：自描述 CLI。
- **推送层（按生态铺适配器，自动推给 AI）**：可插拔适配器注册表，本期只实现 Claude Code skill。

---

## 地板层设计

### 1. `status --json` + 有意义退出码

把 `cmd_status` 拆成两步：纯函数 `collect_status() -> dict` + 渲染（人读文本 / JSON）。

`collect_status()` 返回字段：

| 字段 | 含义 |
|---|---|
| `state` | `not_installed` / `stopped` / `running` |
| `main_pids` | 主进程 `lark-listener run` 的 PID 列表 |
| `event_pids` | 监听子进程 `lark-cli event … --as bot` 的 PID 列表 |
| `files` | `{name: {path, exists}}`：config / state / logs / venv / launchd / shim |
| `last_poll_time` | 读 `state.json`，ISO 字符串或 null |

渲染：默认沿用现有人读格式（◇/●/○ + ✓/—）；`--json` 输出上面的 dict。

**退出码**（经 `main()` 用 `sys.exit` 传递）：

| 码 | 含义 |
|---|---|
| 0 | 运行中 |
| 3 | 已安装未运行 |
| 4 | 未安装 |
| 1 | 异常（如 status 计算本身出错） |

`start` 失败也改为非零退出（现仅 print）。

### 2. `doctor` 命令（查错/解决运行时问题的大脑）

一组主动自检，每项产出结构化结果：`{check: str, status: ok|warn|fail, detail: str, fix: str}`。每个检查抽成可独立测试的纯函数（注入路径/状态，返回结果）。

检查项：

1. **config 合法** —— 复用 `config.load_config` / `_validate`，捕获异常转 fail。
2. **服务已装且在跑** —— `PLIST_PATH.exists()` + `_is_running()`。
3. **lark-cli 可用 + 授权有效** —— 头号故障源（授权过期）。`fix`: `lark-cli auth login --scope search:message`。
4. **上次轮询未过期** —— `state.last_poll_time` vs 轮询间隔×3；超过 → warn，缺失（从未轮询过）→ warn。
5. **近期错误** —— tail `stderr.log` 扫 traceback，有则 warn 并附摘要。
6. **AI 后端** —— 见下两档。

输出：默认人读；`--json` 输出检查项数组。退出码独立于 status：**全过 = 0，有任一 `fail` = 1**（warn 不影响退出码）。

#### AI 后端检查（浅 / 深两档）

**浅检（默认，零网络零副作用）**：
- `provider` ∈ {claude, openai, ollama}、`model` 非空
- `api_key` 该有就有（claude/openai 必填；ollama 可空）
- **对应 SDK 在 venv 里装了**（claude→import anthropic；openai→import openai；ollama 无需）—— 抓「extra 没装上」
- 该有 `base_url` 的有（ollama 端点 / openai 兼容端点）

**深检（`--deep`，真打一次最小请求，有开销+联网）**：
- claude/openai：发极小请求（1 token 补全 / models.list），验 key 有效 + 端点可达
- ollama：戳 `base_url` 验本地服务在跑 + 模型存在
- 抓浅检抓不到的「key 被吊销 / base_url 写错 / ollama 没开 / 模型名拼错」

理由：默认档快且无副作用（合安全命令调性）；「浅检全过但汇总不出来」时再 `--deep`。

### 3. `config get / set`（非交互，薄实现，**不复用 `apply_changes`/`render_config`**）

> ⚠️ 设计修正：`config_editor` 那套（`_plan_changes`/`apply_changes`/`render_config`）服务于 bot 聊天，无法满足 CLI 需求：
> - `_plan_changes` 对 PROTECTED 字段**无条件硬拒**，`--force` 无从实现；
> - `apply_changes` 只做**顶层** `data[field]=value`，而 `ai`/`notify` 是**嵌套 map**（需写 `ai.model`），不支持路径；
> - `render_config` **故意排除** protected 块，AI 诊断看不到 provider/model。
>
> 故 `config get/set` 写**自己的薄实现**，只复用底层：`load_roundtrip`/`dump_roundtrip`、`config.load_config`/`_validate`、`config_editor._coerce_scalar`（标量类型转换）、`config_editor._apply_list_op`（列表增/减）。

- `config get [KEY] [--json]`：输出**完整**有效配置（含 protected），但 **`api_key` 脱敏为 `***`**；`KEY` 支持点号路径取单值。
- `config set KEY VALUE [--add|--remove] [--force]`：
  - `KEY` 支持**点号路径**：`poll_interval`、`keywords`、`lark_cli_appid`、`ai.model`、`ai.provider`、`notify.user_id` 等。
  - **列表字段（如 `keywords`）三种操作**（复用 `_apply_list_op`）：
    - 整体替换（无标志）：`config set keywords 上线,故障` → 逗号拆分整体设置；
    - 增量加：`config set keywords 上线 --add`；
    - 增量减：`config set keywords 故障 --remove`。
    - `--add`/`--remove` 仅对列表字段有效，用于标量则报错。
  - 标量字段：直接整体设置（`--add`/`--remove` 不适用）。
  - 在 ruamel roundtrip 数据上按路径写入（保留注释）。
  - 写后用 `config.load_config`/`_validate` **复验**，非法则**回滚**（不落盘）并报错。
  - 命中 PROTECTED 顶层前缀（`ai` / `notify` / `lark_cli_appid`）需 `--force`，否则拒绝。
  - 下次轮询生效（不重启）。
- 裸 `config`（无参）保持现状 = `open -t` 开 GUI 给人用；`get`/`set` 是新增的非交互子操作。

CLI 形态：`config` 子解析器接受可选位置参数 `op`（`get`/`set`）+ 余参；无 `op` 走原 GUI 行为。

### 4. 自描述 `--help`

- 每个子命令 help 标注 `✅ AI 可直接跑` / `🚫 交互式·交给用户`：
  - ✅ start / stop / restart / status / doctor / config get / config set
  - 🚫 setup / config（裸，GUI）/ uninstall
- epilog 指向 `status --json` 和 `doctor` 作为 AI 操作入口。
- 这是通吃所有 agent 的拉取式发现面，也是契约文字版。

---

## 推送层设计

### 适配器抽象（可插拔注册表）

极小的 adapter 协议，每个 adapter 知道三件事：

- `name`
- `detect() -> bool`：该 agent 是否在本机
- `install() -> None`：把发现物装进该 agent 约定位置
- `uninstall() -> None`：移除

一个注册表（list）。安装/卸载流程遍历**已 detect 命中**的 adapter。将来加 MCP = 往注册表加 `McpAdapter`，其余不动 —— 这就是「框架适配多 Agent」。

### 唯一实现：`ClaudeCodeAdapter`

- `detect()`：`~/.claude/` 存在（即用户确实在用 Claude Code，非侵入前提）。
- `install()`：把 skill 拷进 `~/.claude/skills/lark-listener/`。
- `uninstall()`：删该目录。
- **skills 目录路径可注入**（默认 `Path.home()/".claude"/"skills"`），用于 tmp 隔离测试、避免 dev 跑污染真 `~/.claude`。

**skill 源文件位置（打包修正）**：分发是 `pip install git+`，仓库根目录的文件不会进 venv。故 SKILL.md 必须放在**包内** `lark_listener/skills/lark-listener/SKILL.md`，并在 `pyproject.toml` 加 `[tool.setuptools.package-data]`（或 `include-package-data` + `MANIFEST.in`）确保随 wheel 安装；`install()` 用 `importlib.resources` 读取。

**skill 内容（写薄）**：
- `description` 触发词覆盖：LarkListener / 飞书汇总服务 / 启停 / 配置 / 诊断 / 不工作 —— 让任意 Claude 会话 metadata 命中。
- 正文 = 安装后运维契约：安全 vs 交互命令、「先跑 `lark-listener doctor --json`」、怎么 `config set`、别无人值守跑 setup/config/uninstall、文件路径。
- 明确 **defer 到 `lark-listener --help` / `doctor --json` 为准**，不重复维护内容。

### 接入点

- 新增非交互命令 `lark-listener agent-skills install|uninstall`（AI 可安全跑、可测）。
- `install.sh` 装完服务后调 `"$VENV/bin/lark-listener" agent-skills install`（**venv 绝对路径**，因软链可能尚未进 PATH；**best-effort**，写 `~/.claude` 失败不阻断安装，合 install.sh 既有调性）。当前 install.sh **不调用任何 `lark-listener`**，这是新增的首个调用。
- 服务 `uninstall` 流程里调 `agent-skills uninstall`（删独立目录 `~/.claude/skills/lark-listener`，与 `rmtree(LISTENER_HOME)` 无顺序耦合）。
- **非侵入纪律**：只在 `detect()` 命中时写入，并明确告知用户「已为 Claude Code 安装操作 skill」。

---

## 受影响模块

| 模块 | 改动 |
|---|---|
| `main.py` | **打破现有 `add_parser(name,help)` 统一循环 + `getattr(service,f"cmd_{cmd}")()` 统一分发**：各命令单独定义参数（`status --json`、`doctor --json/--deep`、`config get/set [--json/--force]`、`agent-skills install/uninstall`）；`cmd_*` 改为返回退出码，`main()` 用 `sys.exit(code)` 传递；help 文本标注 ✅安全/🚫交互。`test_main.py` 不直接调 `main()`，退出码改造不破现有测试 |
| `service.py` | `cmd_status` 拆 `collect_status()`+渲染；`cmd_start` 失败返回非零；`cmd_uninstall` 调 agent-skills 清理 |
| 新 `doctor.py` | 各检查纯函数 + 汇总 + 渲染 |
| 新 `config_cli.py`（或并入 service） | `config get/set` 非交互，**薄实现**（点号路径 + `--force` + 写后 `_validate` 回滚 + api_key 脱敏）；只复用底层 `load_roundtrip`/`dump_roundtrip`/`config._validate`/`_coerce_scalar`，**不复用 `apply_changes`/`render_config`** |
| 新 `agent_adapters.py` | adapter 协议 + 注册表 + `ClaudeCodeAdapter`（skills 目录路径可注入）+ `agent-skills` 命令 |
| 新 `lark_listener/skills/lark-listener/SKILL.md` | **包内**资源，随 wheel 安装；install 时用 `importlib.resources` 读出拷入 `~/.claude/skills/` |
| `pyproject.toml` | 加 `[tool.setuptools.package-data]`（或 `include-package-data`+`MANIFEST.in`）打包 SKILL.md |
| `install.sh` | 装完 best-effort 调 `"$VENV/bin/lark-listener" agent-skills install`（首个对本命令的调用） |
| `AGENTS.md` | 小幅更新：提新增 floor 命令 + 会装 skill |

## 测试策略（遵守 CLAUDE.md）

- 纯函数 TDD：`collect_status`、各 doctor 检查项、`ClaudeCodeAdapter.install/uninstall`、退出码映射。
- subprocess / launchctl / 真 fs 用 mock 或 tmp 隔离；`~/.claude` 用 tmp 目录隔离测 adapter。
- `config get/set` 复用已测的 `config_editor`，只测 CLI 包装与 `--force` 门禁。
- 收尾 `python3 -m pytest -q` 全绿；`./dev-test.sh` smoke 通过。

## 已确认的取舍

1. 退出码：status = 0 运行 / 3 停 / 4 未装 / 1 错；doctor = 0 全过 / 1 有 fail。
2. `config set` 受保护键：`--force` 放行。
3. doctor AI 后端检查：浅检默认（无副作用）/ `--deep` 真连。

## 二次 review 修正记录（2026-06-09，对照代码核查）

- **冲突①** skill 不能放仓库根（`pip install git+` 不含非包文件）→ 移入包内 + `package-data` + `importlib.resources`。
- **冲突②** `config get/set` 不能复用 `apply_changes`/`render_config`（PROTECTED 硬拒、仅顶层、排除 protected）→ 改薄实现：点号路径 + `--force` + 写后 `_validate` 回滚 + api_key 脱敏。
- **细节** argparse 统一循环需重构、`cmd_*` 返回退出码（`test_main.py` 不调 `main()`，不破测试）；install.sh 首次调用本命令、用 venv 绝对路径且 best-effort；adapter skills 目录可注入；`dev-test.sh` 为 `set -uo pipefail`（无 `set -e`），新退出码不破 smoke/full；uninstall 加 agent-skills 清理。
