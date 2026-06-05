# LarkListener

定时从飞书获取未读消息，AI 分析后通过 Bot 私聊推送汇总 + macOS 桌面通知。本地后台服务（launchd），装进隔离 venv（标准库，无需额外工具），无需签名、不触发 macOS 安全弹窗。

## 前置

- 已安装并登录 `lark-cli`（本工具的前提）：
  ```bash
  npm install -g @larksuite/cli
  lark-cli config init
  lark-cli auth login --scope search:message
  ```

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh | bash
```

安装完成后按提示运行向导（首次需在普通终端里跑，不能在管道里）：

```bash
~/.local/bin/lark-listener setup     # 新开终端后可直接用 lark-listener setup
lark-listener start
```

> 核心安装只含读写配置的依赖。AI SDK 由 `setup` **按你选的后端**自动安装：claude → `anthropic`，
> openai / DeepSeek → `openai`，ollama → 无需（标准库直连）。切换后端重跑 `setup` 即可补装。

## 管理命令

| 命令 | 作用 |
|---|---|
| `lark-listener setup` | 交互安装向导（选 bot、配置、写 launchd、引导授权） |
| `lark-listener start` / `stop` / `restart` / `status` | 启停 / 查看服务 |
| `lark-listener config` | 打开配置文件 |
| `lark-listener uninstall` | 停服务、删 launchd、删短命令软链、删 `~/.lark_listener`（含 venv），一步到底 |

## 使用

给 Bot 发 **「汇总」/「总结」/「summary」** 可立即触发一次；**「汇总最近2小时」** 可指定时间范围。
发 **「当前配置」/「帮助」** 可查看或自然语言修改配置（仅本人；改动需回复「确认」生效，下次轮询自动应用）。

## 升级

```bash
~/.lark_listener/venv/bin/pip install --force-reinstall "git+https://github.com/AltairEven/LarkListener.git"
lark-listener restart
```
（更新代码后正在跑的守护进程仍是旧代码，必须 `restart` 才生效。）

## 日志

```bash
tail -f ~/.lark_listener/logs/stderr.log
```
