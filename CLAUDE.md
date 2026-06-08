# LarkListener — 给 AI 的测试规范与开发要点

本文件供 AI/开发者参考。**用户面向的说明在 README.md。**

## 一句话

本地后台服务（macOS launchd）：定时用 `lark-cli` 拉飞书未读消息 → AI 分析 → Bot 私聊推汇总 + 桌面通知。Python 包，标准库 `venv` 分发，无 Gatekeeper 弹窗。

## 架构速览（改代码前先认路）

| 模块 | 职责 |
|---|---|
| `main.py` | `main()` 是 argparse 子命令分发器；`run()` 是守护循环（launchd 调用）；`poll_once`/`_handle_message`/`_bot_listener` 等守护逻辑 |
| `service.py` | launchd 管理：路径/plist 生成（`shim_path`/`node_bin_dir`/`build_plist`）、`stop_service`、`cmd_start/stop/restart/status/config/uninstall`、`ensure_shim_link` |
| `setup_wizard.py` | 交互安装向导 `cmd_setup`；纯函数 `build_config_dict`/`write_config_file`/`ai_packages_for`/`_pip_install_ai` |
| `analyzer.py` / `intent.py` | 调 AI（**延迟 import** anthropic/openai；ollama 走标准库 urllib） |
| `fetcher.py` | 调 `lark-cli` 搜消息、取上下文 |
| `notifier.py` | Bot 消息 + macOS 通知（osascript 默认，terminal-notifier 可选） |
| `config.py` / `config_editor.py` | 读/改 config.yaml（ruamel 保留注释；`ai`/`notify` 受保护不可经 bot 改） |
| `state.py` | 去重与上次轮询时间 |

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

7. **守护循环符号被测试依赖**：`poll_once`/`_handle_message`/`_reply_bot`/`_add_reaction`/`_pending_change` 保持原位、原签名。

8. **每次隔离真跑后清理**：`./dev-test.sh clean` 或手动删 `/tmp/ll-*` 与对应 dev plist + `launchctl unload`。

## 依赖

核心仅 `pyyaml` + `ruamel.yaml`。AI SDK 是 extras（`anthropic`=claude，`openai`=openai/deepseek，ollama 无需），由 `setup` 按所选后端 `pip install` 进 venv。开发跑 AI 路径需 `pip install -e ".[claude,openai]"`。

## 分发 / 升级 / 卸载

- 安装：`curl … install.sh | bash` → `venv` + `pip install git+…` + 软链 `~/.local/bin/lark-listener`。
- 升级：`~/.lark_listener/venv/bin/pip install --force-reinstall "git+…"` + `lark-listener restart`（不重启跑的还是旧代码）。
- 卸载：`lark-listener uninstall`（停服务、删 plist/软链/`~/.lark_listener`）。

## 提交约定

未经用户明确要求，**不要 `git commit` / `git push`**。`docs/` 被 gitignore（设计文档/计划不入库）。
