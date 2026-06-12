# LarkListener 友好分发设计（消除 macOS 安全弹窗）

日期：2026-06-05
状态：已确认，待实现
说明：本方案为 **Python + 标准库 venv** 路线。曾评估「Node/TS 重写」「Go 重写」「pipx」，
最终选标准库 `venv`：零重写、无 Gatekeeper，且**不要求用户额外安装任何工具**（python3
已在、venv 是标准库），比 pipx 路线少一个外部依赖、并彻底回避 PEP 668 引导的脆弱逻辑。

## 背景与问题

当前分发方式：`build.sh` 用 PyInstaller 打出一个约 20MB 的独立二进制 `lark-listener`，
再配一个双击运行的 `LarkListener.command` 管理菜单。两者被下载后都带上
`com.apple.quarantine` 属性，又没有签名/公证，于是 macOS Gatekeeper 弹出
「无法打开，因为无法验证开发者」或「已损坏」，普通用户视为「打不开」。

目标：让工具对**非技术用户**友好分发，安装体验像 `lark-cli`（`npm install -g`）那样
顺畅，全程不触发 Gatekeeper 安全弹窗，且**不让用户为此多装任何工具**。

## 核心思路

彻底放弃「打包独立二进制 + 下载 .command」的分发模式——这两者正是 quarantine
弹窗的根源。改为：用户执行一条 `curl ... | bash`，脚本用 **Python 标准库 `venv`**
建一个隔离虚拟环境（`~/.lark_listener/venv`），用该 venv 的 `pip` 安装本包，再把生成的
可执行入口**软链**到 `~/.local/bin/lark-listener`。代码经由系统已信任的 `python3`
解释器运行，**不产生 quarantine，Gatekeeper 全程不介入**。

为什么是 venv 而非 pipx：pipx 的本质就是「建 venv + 管 shim」，而 venv 是 Python 标准库
自带（CLT 的 python3 也有），**用户无需额外装 pipx**。代价是 `install.sh` 要自己管理
venv 创建、shim 软链、升级/卸载——都是确定性的十几行脚本。换来的好处是：少一个外部依赖、
且**完全没有 PEP 668（externally-managed-environment）问题**（venv 内的 pip 不受系统/brew
Python 的外部管理标记限制），去掉了原 pipx 方案中最易翻车的一整段引导逻辑。

### 排除的备选方案
- **Developer ID 签名 + 公证**：体验最干净，但需 Apple 开发者账号（$99/年）和持续
  配置，用户倾向不折腾。
- **Homebrew tap**：需维护 formula，且要求用户先装 Homebrew。
- **pipx**：能用，但要求用户先 `brew install pipx` 或 `pip install --user pipx`（后者撞
  PEP 668），多一个外部依赖、多一段脆弱引导。venv 用标准库即可达到同样的隔离效果。
- **裸 `pip install --user`**：撞 PEP 668（brew python 直接失败）、依赖污染用户全局、
  命令落在不在 PATH 的 `~/Library/Python/3.9/bin`、卸载脏。不可取。
- **Node/TS 重写 / Go 重写**：要整体重写、丢弃现有测试；收益在 python/node 均免装的前提
  下基本是美学层面。
- 结论：`curl | bash` + 标准库 venv 零重写、零额外工具、零弹窗、零 PEP668。

## 关于 node 依赖（已查清的事实）

lark-cli 的核心是 **Go 编译的独立二进制**，运行时本身不需要 node。但用户通过
`npm install -g @larksuite/cli` 装出来的 `lark-cli` 命令，入口是一个 node 壳
（`scripts/run.js`，`#!/usr/bin/env node`，负责定位/自动下载并 exec 那个 Go 二进制）。
因此**经由 `lark-cli` 命令调用时，每次都需要 node**——node 在机器上「在场」是 npm
安装方式带来的，而非 lark-cli 本质所需。

对本设计的意义：本工具所有飞书操作都 shell 调用 `lark-cli`；只要用户用 npm 装好了
lark-cli（本就是使用本工具的前提），node 即已存在。故 `install.sh` 只需**检测**
lark-cli，不做 node / lark-cli 的自动安装。

## 分发与安装流程

- 托管：GitHub 公开仓库（`AltairEven/LarkListener`），根目录提供 `install.sh`。
- 用户一行命令：
  ```bash
  curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh | bash
  ```
- `install.sh` 步骤（**全程非交互**）：
  1. 确保 `python3` 可用且版本 ≥3.9（缺失时触发 Apple 官方 Command Line Tools 安装——
     系统级、已公证弹窗，非「无法验证开发者」告警；版本过低则明确报错退出）。
  2. 检测 `git`（`pip install git+...` 需要它；macOS 随 CLT 提供。缺失则提示）。
  3. **检测** `lark-cli`：有则视为前提就绪；缺则清晰引导用户按官方方式安装并
     `lark-cli config init` + 授权登录后重跑（不自动安装 node / lark-cli）。
  4. 建隔离环境：`python3 -m venv ~/.lark_listener/venv`（venv 是标准库，无需额外工具；
     已存在则复用，升级路径见下）。
  5. 用该 venv 的 pip 安装本包：
     `~/.lark_listener/venv/bin/pip install --upgrade pip` 然后
     `~/.lark_listener/venv/bin/pip install "git+https://github.com/AltairEven/LarkListener.git"`。
  6. 软链短命令：`mkdir -p ~/.local/bin` 后
     `ln -sf ~/.lark_listener/venv/bin/lark-listener ~/.local/bin/lark-listener`。
  7. 结束：**打印提示让用户在终端手动运行 setup，给出绝对路径**：
     ```
     ✅ 安装完成。现在运行：
        ~/.local/bin/lark-listener setup
     （新开终端后，若 ~/.local/bin 在 PATH，可直接用 lark-listener setup）
     ```
     绝对路径在当前终端立即可用、不依赖 PATH 刷新；括号说明短命令的前提。

### 为什么 venv 路线没有 PEP 668 问题
PEP 668 的 `externally-managed-environment` 报错只发生在向**系统/brew 管理的 Python**
直接 `pip install` 时。`python3 -m venv` 建出的是一个独立、非外部管理的环境，其内部
`pip install` 不受该标记限制。因此 venv 路线无需 `--break-system-packages`、无需多路
回退，`install.sh` 的安装段是平铺直叙的几步。

### 为什么 install.sh 不直接调 setup（关键约束）
`curl ... | bash` 时 bash 的 stdin 是被管道喂入的脚本本身，不是键盘。若在管道里调用
交互式向导（`setup` 满是 `input()`），所有输入会读到 EOF 而崩溃或跳空。故**解耦**：
install.sh 只做非交互安装，向导留给用户在正常终端（真 tty）里手动跑。

此外软链建好后，`~/.local/bin` 是否在当前 shell 的 PATH 不确定；install.sh 内及结尾提示
一律用绝对路径 `~/.local/bin/lark-listener`（由 shell 展开 `~`）。

## CLI 子命令（取代 .command 菜单）

为 `pyproject.toml` 补 `[build-system]`（缺失会导致 `pip install git+...` / `pip install .`
在多数环境构建失败）：
```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"
```
**必须同时显式声明打包范围**：`tests/` 含 `__init__.py`，flat-layout 自动发现会看到
`lark_listener` 和 `tests` 两个顶层包，setuptools≥61 会报
`Multiple top-level packages discovered in a flat-layout` 导致 `pip install` 在构建阶段
失败。故加：
```toml
[tool.setuptools.packages.find]
include = ["lark_listener*"]
```

将入口 `lark_listener.main:main` 改为 argparse 分发器。`[project.scripts]` 仍为
`lark-listener = "lark_listener.main:main"`，但 `main()` 据子命令分发：

| 命令 | 作用 |
|---|---|
| `lark-listener run` | 守护循环（现有 `main()` 主体抽成的 `run()`，launchd 调用它） |
| `lark-listener setup` | 交互安装向导 + 建 shim 软链 + 写 launchd plist + 引导授权 |
| `lark-listener start` / `stop` / `restart` / `status` | launchctl 包装 |
| `lark-listener config` | 打开配置文件（`open -t`） |
| `lark-listener uninstall` | 停服务、删 plist、删 shim 软链、删 `~/.lark_listener`（含 venv），一步到底 |

**重构约束（保测试不破）**：把现有 `main()` 守护循环主体抽成 `run()`，`main()` 仅做
argv 解析与分发；`poll_once` / `_handle_message` / `_reply_bot` / `_add_reaction` 等被
`tests/test_main.py` 依赖的符号保持原位、原签名。

**实现语言：Python（不保留 bash 向导）**。`setup` 与所有管理子命令（`start`/`stop`/
`restart`/`status`/`config`/`uninstall`）一律 Python 实现——dispatcher 本就是 Python
console script，管理命令是 launchctl/`open`/`rm`/软链 的薄 `subprocess`/`pathlib` 封装，
且可纳入"子命令分发"单测。

`setup` 向导内容沿用现有 `LarkListener.command` 的安装逻辑：选择承载服务的 lark-cli
bot（appId）、轮询间隔、关键词、AI 后端配置、自动获取 user_id 与 bot_chat_id、
**创建 `~/.lark_listener/logs/` 目录**（plist 的 stdout/stderr 日志路径指向此处，
launchd 不会自动创建中间目录，缺失则服务启动失败）、写 config.yaml、
**按所选 AI 后端把对应 SDK 装进 venv**（见上「依赖」节；ollama 跳过）、
**幂等确保 shim 软链存在**（`~/.local/bin/lark-listener` → venv 入口，兼容本地 `pip`
直装、未走 install.sh 的情况）、写 launchd plist、
检查并引导 `lark-cli auth login --scope search:message`。
向导结尾提示用户运行 `lark-listener start` 启动（或由 setup 直接启动，二选一在实现时定）。

移植时**用 Python 原生能力替换 bash 里的几处脆弱写法**（移植即清理，非逐行直译）：
- 解析 lark-cli 输出：旧 .command 内联 `python3 -c "import sys,json; ..."`（:87/172/334）
  → 直接 `json.loads(subprocess 输出)`。
- 写 config.yaml：旧 heredoc 拼字符串、关键词手拼 YAML 缩进（:180-211）→ 用 `ruamel.yaml`
  （项目已依赖）构造并 dump，避免缩进/转义出错。
- 改 `lark_cli_appid`：旧 `sed -i ''`（:220）→ 改 dict 再 dump。
- 交互输入 `read -p` → `input()`；`auth login` 等需要真 tty 的调用用 `subprocess.run`
  继承 stdin/stdout（不捕获），保证浏览器授权交互正常。
- 移植风险点：profile 选择、自动取 user_id/bot_chat_id、auth/scope 检测这几段交互流逻辑
  较密，移植后需照旧 .command 的分支逐项核对，防回归。

### 老用户迁移（含本机）
现有旧版以 PyInstaller 二进制 + 旧 plist（指向 `~/.lark_listener/lark-listener`）运行。
`setup` 与同名 Label `com.larklistener` 重装时应：先 `launchctl unload` 旧服务、清理
残留的旧二进制 `~/.lark_listener/lark-listener`，再写指向 venv 入口的新 plist。
（注意：venv 在 `~/.lark_listener/venv`，与旧二进制 `~/.lark_listener/lark-listener`
路径不冲突，可共存于同一目录，互不覆盖。）

### 停止与卸载的进程处理
- **主路径用 `launchctl unload`** 停服务：它发 SIGTERM，`main.py` 现有 signal handler 会
  清掉 `lark-cli event` 子进程并干净退出。这是最可靠的停法。
- **兜底 pkill** 按本实例 **venv 入口的绝对路径**精确匹配（`{VENV_DIR}/bin/lark-listener run`），
  而非泛的 `lark-listener run`——这样开发隔离实例（`LARK_LISTENER_HOME` 指向 /tmp/...）与生产
  实例互不误杀（进程 cmdline 含各自 venv 路径）；`pkill -f "lark-cli event.*--as bot"` 那条不变。

### 开发隔离（环境变量）
`LISTENER_HOME` 与 launchd `LABEL`（及随之派生的 `PLIST_PATH`）支持环境变量覆盖，便于开发时
与生产隔离，不设则行为与默认完全一致：
- `LARK_LISTENER_HOME`：数据目录（config/state/logs/venv），默认 `~/.lark_listener`；
- `LARK_LISTENER_LABEL`：launchd Label，默认 `com.larklistener`（plist 文件名随之）。
开发态（设了 `LARK_LISTENER_HOME`）下 `ensure_shim_link` 跳过，绝不覆盖生产的
`~/.local/bin/lark-listener` 软链。配套 `dev-test.sh` 提供 unit/smoke/full/clean 一键测试。

### launchd plist
`ProgramArguments` 从 `~/.lark_listener/lark-listener` 改为 **venv 入口的绝对路径**加 `run`
子命令：`~/.lark_listener/venv/bin/lark-listener run`（展开为
`/Users/<user>/.lark_listener/venv/bin/lark-listener`）。
**关键：launchd 不是 shell，不展开 `~`/`$HOME`/环境变量，`ProgramArguments` 必须写
展开后的绝对路径**（否则 launchd 会按字面去找带波浪号的文件、静默启动失败）。
venv 路线下这个路径是**确定的**（我们自己建的 venv），故 `shim_path()` 直接返回
`str(LISTENER_HOME / "venv" / "bin" / "lark-listener")` 即可，不需要 which/argv0 兜底。
注意 plist 指向 **venv 内真实入口**而非 `~/.local/bin` 的软链——这样即使软链被删，服务
仍能启动。
`EnvironmentVariables.PATH` 仍含 node/lark-cli 常见目录；`binaries.py` 的 `ensure_path()` /
`resolve_executable()` 保持不变，继续负责运行时定位 node。
- 已知边界：若 node 由 nvm 安装（在 `~/.nvm/versions/node/vX/bin`），不在写死的常见目录里，
  服务可能找不到 lark-cli/node。`setup` 写 plist 时应**动态解析真实 node 目录**
  （`dirname $(command -v node)`）并并入 plist 的 PATH，而非只依赖写死列表。

## 桌面管理菜单

不做。所有管理操作统一通过 `lark-listener` 子命令完成（与 curl|bash 终端安装一致）。

## 桌面通知（去掉 terminal-notifier，砍 brew 依赖）

`notifier.py` 的 `_send_macos_notification` 改为：
- 默认使用系统原生 `osascript -e 'display notification "..." with title "..."'`，零依赖。
  正文虽仅为统计串（如「3个私聊、1个@我」、内容可控），仍对引号/反斜杠做转义，避免破坏
  AppleScript 字符串。
- 若检测到系统已安装 `terminal-notifier`，仍用它（保留点击跳转飞书会话的 `-open` 能力）。

代价：原生 osascript 通知点击**不能直接跳转飞书会话**。可接受——桌面 toast 是次要通道，
主通道是 bot 私聊消息，其中已带可点击的会话链接。通知失败必须保持 best-effort（不得
中断轮询循环），与现有实现一致。

## 依赖：核心精简 + AI SDK 按后端按需装

**核心依赖只有 `pyyaml` + `ruamel.yaml`**（读写 config）。AI SDK 不进必装清单，改为
`[project.optional-dependencies]` 的 extras：
```toml
dependencies = ["pyyaml>=6.0", "ruamel.yaml>=0.18"]
[project.optional-dependencies]
claude = ["anthropic>=0.30.0"]
openai = ["openai>=1.30.0"]
```
可行的前提：`analyzer.py` 与 `intent.py` 调 AI 的代码都是**延迟 import**（`import anthropic`/
`import openai` 写在函数体内，非模块顶层），故只装核心的 venv 也能跑 `setup`/`start`/`run`
等全部命令，只有真正调用 AI 时才需要对应 SDK。三后端的需求：

| 后端 | 需装 | 调用方式 |
|---|---|---|
| claude | `anthropic` | `analyzer._call_claude` / `intent`（lazy import） |
| openai / DeepSeek | `openai` | `analyzer._call_openai` / `intent`（lazy import） |
| ollama | **无** | `analyzer._call_ollama` 用标准库 `urllib` 直连 |

**按需安装时机**：`install.sh` 的 `pip install git+...` 只装核心；`setup` 向导让用户选完
AI 后端后，用 venv 自己的 pip 装对应 SDK：`subprocess.run([sys.executable, "-m", "pip",
"install", <pkg>])`（`sys.executable` 即 venv 的 python，装进同一 venv）。这是给 venv **增装**
一个守护进程 `run` 之后才 import 的依赖，不是「进程重装自己」，无自我覆盖问题。安装失败仅
警告、不中断 setup（用户可手动补装）。ollama 后端零 SDK，跳过。

**版本兼容**：extras 仍是无上限下限，`pip install` 解析到当时最新。风险同前：某天新版 SDK
抬高最低 Python 到 3.10+，而 venv 的 python 是 CLT 的 3.9.6 则可能解析失败。缓解：venv 用
`install.sh` 调用的 `python3` 创建（要更新版本就让 `python3` 指向 brew python）；实现阶段在
目标 Python（含 3.9.6）实测依赖解析，必要时给关键依赖加上限。
注意：把 AI SDK 移成 extras 后，**开发/测试环境**需自行装它们（`pip install -e ".[claude,openai]"`）
才能跑到真正调用 AI 的路径（现有测试多为 mock，不强依赖）。

## 升级 / 更新

venv 装的是 git 源，更新需重拉；且**更新代码后正在跑的守护进程仍是旧代码**，必须 restart
才生效。采 README 文档化的手动两步（不做 `update` 子命令，避免「进程在自己 venv 里重装
自己」的脆弱操作）：
```bash
~/.lark_listener/venv/bin/pip install --force-reinstall "git+https://github.com/AltairEven/LarkListener.git"
lark-listener restart
```

## 删除 / 废弃

- `build.sh`、PyInstaller 依赖、`build/` 内的二进制。
- `run_service.py`（旧 PyInstaller 入口；改用 `lark_listener.main:main`，已废）。
- 被下载分发的旧 `LarkListener.command`。
- `requirements.txt`：与 `pyproject.toml` 的 `dependencies` 重复，pip 装走 pyproject，
  留着会变成无人维护的影子源。删除，或在 README 注明仅供本地 dev（`pip install -e .` 即可）。
- `README.md` 安装段改写为单行 curl 安装 + `lark-listener setup` + 子命令用法 + 升级说明。

## 测试

- `install.sh`：干净环境验证 python 版本校验、git 检测、缺 lark-cli 的引导提示、venv 创建、
  `pip install` 成功、软链建立、结尾正确提示手动 setup。（bash 安装脚本以人工/集成验证为主。）
- CLI 分发器：各子命令的参数解析与分发（`run` / `setup` / `start` / `stop` /
  `status` / `restart` / `config` / `uninstall`）。
- 通知：无 terminal-notifier 走 osascript、有则走 terminal-notifier、两者失败均不抛出。
  **需更新 `tests/test_notifier.py`**：现有用例硬断言「第二个 subprocess 调用以
  terminal-notifier 结尾」，改 osascript 默认后会失效，须改成覆盖新分支。
- 路径/plist：`shim_path()` 返回 venv 内绝对路径；launchd plist 生成内容正确（指向 venv 入口
  + `run`，无 `~/` 字面量，PATH 含动态解析的 node 目录）。
- 沿用现有其余 `tests/` 用例，确保重构 main 入口后守护循环行为不变。

## 影响范围

- 改：`lark_listener/main.py`（拆出 `run()` + argparse 分发 + 子命令实现）、
  `lark_listener/notifier.py`（osascript 通知 + 转义）、`pyproject.toml`（加 build-system
  与 `[tool.setuptools.packages.find]`）、`README.md`、`tests/test_notifier.py`。
- 新增：`install.sh`、`lark_listener/service.py`（路径/plist/shim 软链/管理命令）、
  `lark_listener/setup_wizard.py`（交互向导）。
  **工作量提示**：`setup` 不是薄封装——要把旧 `LarkListener.command` 的 `_install`（约 280 行
  bash）整体移植成 Python（`read -p` → `input()`）。
- 删：`build.sh`、`run_service.py`、`LarkListener.command`、`requirements.txt`、`build/`。
- 不变：`fetcher.py`、`analyzer.py`、`config*.py`、`intent.py`、`state.py`、`binaries.py`。
