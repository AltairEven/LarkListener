# LarkListener — 给 AI 的测试规范与开发要点

本文件供 AI/开发者参考。**用户面向的说明在 README.md。**

## 一句话

本地后台服务（macOS launchd）：定时用 `lark-cli` 拉飞书未读消息 → AI 分析 → Bot 私聊推汇总 + 桌面通知。Python 包，标准库 `venv` 分发，无 Gatekeeper 弹窗。

## 架构速览（改代码前先认路）

| 模块 | 职责 |
|---|---|
| `main.py` | `main()` argparse 分发器（子命令均返回退出码经 `sys.exit`，仅 `run` 除外：status/doctor/config get-set/summarize/agent-skills/setup/start/stop/restart/…）；`run()` 守护循环（monotonic 节拍 `next_cycle_due`：**trigger 不重置节拍、config reload 仅到期发生**——大 interval 下 `config set` 最长等一个旧 interval；`poll_interval=0`=关自动轮询：跳过 poll_once、节拍上限 `IDLE_RELOAD_SECONDS`；启动期 load_config 失败分片慢退 60s 防 KeepAlive 风暴）；`poll_once`/`_handle_message`/`_bot_listener` 守护逻辑（interval<=0 时手动汇总窗口回溯 30min/封顶 24h）；`_fetch_window`/`_analyze_window`（poll_once 与 summarize 共用的「产出」核心）；poll_once 的 notify 失败兜底（log+告警 owner 后仍推进 state，防毒消息冻结窗口）；`cmd_summarize`（按需汇总→stdout 输出统一封套 `{code,errorMsg,data}` JSON，退出码=code，默认也推飞书卡片） |
| `service.py` | launchd 管理：`shim_path`/`node_bin_dir`/`build_plist`/`stop_service`/`ensure_shim_link`；`collect_status`（机读状态 dict）+ `cmd_start/stop/restart/status/config/uninstall`（均返回退出码；`cmd_uninstall` 兼清理 agent skill、EOF/Ctrl-C 取消返回 1，dev 隔离态跳过；`stop_service` 的 event 子进程清理按配置 appid 隔离） |
| `setup_wizard.py` | 交互安装向导 `cmd_setup`（返回退出码 0/1，EOF/Ctrl-C 干净取消）；纯函数 `build_config_dict`/`write_config_file`（经 dump_roundtrip 原子写+0600）/`_parse_poll`/`ai_packages_for`（委托 providers）/`_pip_install_ai` |
| `providers.py` | **AI provider 注册表（唯一事实源）**：每后端一个对象（`complete`/`deep_probe`/`sdk_import`/`pip_packages`）+ `extract_json`；SDK 在方法体内**延迟 import**；新增 AI 后端主要改这个文件（analyzer._call_ai 分发、doctor provider 白名单、setup 菜单仍需各加一行） |
| `analyzer.py` / `intent.py` | 调 AI：prompt 构造与结果归一在本层，三后端调用委托 `providers.complete`（`_call_claude/_call_openai/_call_ollama` 与 `_call_ai` 保留原名原签名供单测直测） |
| `common.py` | 跨模块常量/路径唯一事实源：`TZ`（+08:00）、`listener_home()`（惰性读 `LARK_LISTENER_HOME`；service 例外地 import 时冻结一次） |
| `chats.py` | 未免打扰探测与会话分类唯一事实源：`classify_chat`/`ChatRegistry`（每轮 refresh、失败沿用上轮、首刷失败全按勿扰；`special_chat_ids`/`name_of` 供抓取与补名） |
| `fetcher.py` | 调 `lark-cli` 搜消息、取上下文；@all 按分类分流（勿扰群仅关键词命中才收、普通群全收）；特别关注群全量合并抓取（每群上限 `special_focus.max_messages`）；上下文合并抓取（去重、单次调用） |
| `binaries.py` | lark-cli 路径/调用封装：`lark_cli`/`resolve_executable`/`ensure_path`/`set_lark_profile`/`event_subscriber_pkill_pattern`（event 订阅 pkill 模式唯一事实源，按 profile 隔离防误杀 dev/prod 对方与其它 agent 的订阅进程；被 main/service/fetcher/notifier/setup 依赖） |
| `notifier.py` | 统一封套唯一事实源 `build_summary_response`/`error_response`；卡片 `build_summary_card`（飞书 table 卡片，2026-06-10 真发逐版定稿：仅两列——会话列 38% 宽、纯名称无🔴、冒号后直接接带链接原文片段（`_short_snippet` 缩到 20 字内，命中关键词时以关键词为中心截取）+ 摘要列仅 AI 摘要；分类 emoji+数量入表头、row_height auto；表头背景实测仅支持 none/grey；**特别关注区 🟪**，顺序统一 p2p→at_me→at_all→special→keyword；分组键 `(category,chat_id)` 去重）→ 失败回退 `build_summary_text`（Markdown，保留🔴 既有样式）；`Notifier.notify(…, resp=None)` 可直接消费调用方已建封套；macOS 通知（osascript 默认，terminal-notifier 可选） |
| `config.py` / `config_editor.py` | 读/改 config.yaml（ruamel 保留注释；`ai`/`notify`/`lark_cli_appid` 受保护不可经 bot 改）；`load_config` 钳制：`poll_interval`/`context_messages` 非负 int（负→0/非法→默认）、`keywords` 强制 list[str]、`exclude_chats` 强制 list[dict]、`special_focus` 子树钳制（`enabled` bool、`max_messages` 正 int、`chats` list[dict]；坏值绝不让消费点 TypeError 崩进 KeepAlive 重启循环）；旧键 `include_at_all`/`exclude_chat_ids` 迁移兼容（读时静默升级）；`dump_roundtrip` 0600 原子写（保持原 mode）；`removes_bot_chat` 防自反馈守卫（bot 与 CLI 路径共用） |
| `doctor.py` | `lark-listener doctor` 主动自检：`check_config/service/lark_cli/last_poll/recent_errors/ai_backend`（浅检零副作用、兼校验 appid 在 profile 列表但**不验授权时效**；`--deep` 经 `probe_messages_search`（与 setup 共用）真探 search:message + AI 真连；`SEARCH_SCOPE` 唯一事实源；日志只读尾部 64KB）；`run_doctor`/`cmd_doctor`（退出 0 全过/1 有 fail，每项带 `fix`） |
| `config_cli.py` | `config get/set` 非交互：点号路径、列表增/减/整体替换、`--force` 放行受保护键、写后 `_validate` 失败回滚、api_key 脱敏；只复用 `config_editor` 底层（不复用其 bot 调度） |
| `agent_adapters.py` | 可插拔 adapter 注册表 + `ClaudeCodeAdapter`（装/卸 `~/.claude/skills/lark-listener` 操作 skill，包内资源经 importlib.resources 读）；`install/uninstall_agent_skills`（best-effort） |
| `state.py` | 去重与上次轮询时间（坏文件任意形状不崩、原子写；默认路径经 `listener_home()` 尊重 `LARK_LISTENER_HOME`） |

## 测试规范（核心）

### 1. 改完代码必跑单测
```bash
python3 -m pytest -q       # 全绿才算改完
```
覆盖了分发器、`shim_path`/`build_plist`、config 构造、通知分支、env 隔离等。新增逻辑**优先抽成纯函数并 TDD**；交互/launchctl/subprocess 用 mock 或隔离环境，不要真跑。

### 2. 一键脚本 `dev-test.sh`
```bash
./dev-test.sh          # = unit + smoke（安全，无副作用，反复用）
./dev-test.sh unit     # 仅单测
./dev-test.sh smoke    # 安装文件层→状态→卸载，自我清理（不发飞书/不 load）
./dev-test.sh full     # 完整真跑：建 venv→setup→start→更新→卸载（★发真飞书、真 launchctl）
./dev-test.sh clean    # 清理 dev 残留
```

### 3. 三层策略（从快到慢）
- **单测**：日常 80%，零副作用。
- **editable venv**：`python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -e ".[claude,openai]"`，改代码即时生效，跑命令验行为。
- **隔离真跑**：建真 venv + 真 setup/start，验 launchd/分发。

## 测试时必须守的约束（踩过的坑）

1. **永远用 dev 隔离测 `setup`/`start`/`uninstall`，绝不碰生产。**
   ```bash
   LARK_LISTENER_HOME=/tmp/ll-dev LARK_LISTENER_LABEL=com.larklistener.dev <cmd>
   ```
   `LISTENER_HOME`/`LABEL`/`PLIST_PATH`/`VENV_DIR` 都随这两个 env 派生；不设则用生产默认。

2. **CLT python（3.9.6）自带 pip 太老（21.2.4），editable（`pip install -e`，PEP 660）会失败。** 建 venv 后必须先 `pip install --upgrade pip`。

3. **launchd 起的进程不继承 shell 环境变量。** dev 态的 plist 必须把 `LARK_LISTENER_HOME`/`LABEL` 写进 `EnvironmentVariables`（`build_plist` 已处理），否则服务回退生产路径崩溃。

4. **`build_plist` 的 `ProgramArguments` 必须是绝对路径**（launchd 不展开 `~`），指向 venv 内真实入口 `…/venv/bin/lark-listener` + `run`。测试有 `assert "~/" not in xml` 守这条。

5. **不污染真飞书**：隔离测 `start` 时用假 `appId`（如 `cli_fake`），`lark-cli --profile 假` 会调用失败，服务有 best-effort 兜底不崩，也不会真发消息。`full` 用真 setup 则会真发飞书测试消息。

6. **best-effort 不可抛**：`notifier` 通知失败、`_reply_bot`、AI/网络调用失败都不能让轮询循环崩溃（launchd KeepAlive 会陷入重启循环）。

7. **守护循环符号被测试依赖**：`poll_once`/`_handle_message`/`_reply_bot`/`_add_reaction`/`_pending_change` 保持原位、原签名。`poll_once` 已把 fetch/analyze 拆到 `_fetch_window`/`_analyze_window`（与 `cmd_summarize` 共用）——改动时保持其行为与这两个 helper 的签名（单测直接依赖，且 poll_once 测试 `@patch("lark_listener.main.Fetcher/Analyzer/Notifier")` 要求它们留在 main.py）。

8. **每次隔离真跑后清理**：`./dev-test.sh clean` 或手动删 `/tmp/ll-*` 与对应 dev plist + `launchctl unload`。

## 依赖

核心仅 `pyyaml` + `ruamel.yaml`。AI SDK 是 extras（`anthropic`=claude，`openai`=openai/deepseek，ollama 无需），由 `setup` 按所选后端 `pip install` 进 venv。开发跑 AI 路径需 `pip install -e ".[claude,openai]"`。

## 分发 / 升级 / 卸载

- 安装：`curl … install.sh | bash` → `venv` + `pip install git+…` + 软链短命令。软链目录按
  ensurepath 策略选：优先「可写+已在 PATH」的目录（`~/.local/bin`→`/opt/homebrew/bin`→
  `/usr/local/bin`，brew 用户免改配置）；否则用 `~/.local/bin` 并幂等注入 shell rc 的 PATH。
  实际软链位置记录在 `~/.lark_listener/shim_link`，`uninstall` 据此精确清理。软链全程 best-effort，
  失败也不影响安装（服务/命令用 venv 绝对路径仍可运行）。安装末尾 best-effort 调
  `agent-skills install`：检测到 Claude Code（`~/.claude/` 存在）时，把操作 skill 拷进
  `~/.claude/skills/lark-listener/`，供任意 Claude 会话自动发现如何操作本服务。
- 升级：`~/.lark_listener/venv/bin/pip install --force-reinstall "git+…"` + `lark-listener restart`（不重启跑的还是旧代码）。**skill 在 `cmd_start` 里自愈刷新**：每次 `restart` 静默把 `~/.claude/skills/lark-listener` 同步到当前包版本（detect 命中才写、dev 隔离跳过），故升级 restart 即与二进制对齐、文件缺失也会补回——pip 单独装不刷新，但只要按约定 restart 就对齐。
- 卸载：`lark-listener uninstall`（停服务、删 plist/软链/`~/.lark_listener`，并清理 `~/.claude/skills/lark-listener`；dev 隔离态不碰真机 `~/.claude`）。

## 运行情况 & 文件布局（排查/清理用）

`lark-listener doctor` 是主动自检入口（config/lark-cli 授权/轮询时效/日志/AI 后端，每项带 `fix`，`--deep` 真探 lark-cli search:message 授权 + AI 后端真连）；`lark-listener status` 输出服务三态 + 主进程/监听子进程 PID + 全部文件位置（带 ✓/—）。二者均支持 `--json` 供 AI agent 机读；退出码：status 0 运行/3 停/4 未装，doctor 0 全过/1 有 fail。

- 进程构成：1 个主进程 `… lark-listener run`（launchd KeepAlive 守护）+ `lark-cli event +subscribe … --as bot` 监听子进程（node 壳 + Go 二进制，由监听线程拉起，断开会按间隔重连）。
- 文件布局：
  - `~/.lark_listener/`：`config.yaml`、`state.json`、`logs/`、`venv/`、`shim_link`（记录软链实际位置）
  - `~/Library/LaunchAgents/com.larklistener.plist`：launchd 配置
  - 短命令软链：位置见 `shim_link`（可能在 `~/.local/bin` 或 `/opt/homebrew/bin` 等）

**手动清理**（`uninstall` 命令失效，或旧版无 `shim_link` 记录导致软链残留时）：
```bash
launchctl unload ~/Library/LaunchAgents/com.larklistener.plist 2>/dev/null
pkill -f "/.lark_listener/venv/bin/lark-listener run"
# 注意：下面这条会波及本机所有 lark-cli bot 订阅进程（含其它 agent 的），仅彻底清理时用
pkill -f "lark-cli event.*subscribe.*--as bot"
rm -f ~/Library/LaunchAgents/com.larklistener.plist
rm -f ~/.local/bin/lark-listener /opt/homebrew/bin/lark-listener /usr/local/bin/lark-listener
rm -rf ~/.lark_listener
```

## 提交约定

未经用户明确要求，**不要 `git commit` / `git push`**。`docs/` 被 gitignore（设计文档/计划不入库）。
