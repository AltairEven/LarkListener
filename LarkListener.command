#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LISTENER_HOME="$HOME/.lark_listener"
PLIST_PATH="$HOME/Library/LaunchAgents/com.larklistener.plist"
BINARY="$SCRIPT_DIR/lark-listener"

_is_installed() {
    [ -f "$LISTENER_HOME/lark-listener" ] && [ -f "$PLIST_PATH" ]
}

_is_running() {
    launchctl list 2>/dev/null | grep -q com.larklistener
}

_status() {
    if ! _is_installed; then
        echo "◇ 未安装"
    elif _is_running; then
        echo "● 服务运行中"
    else
        echo "○ 服务已安装，未运行"
    fi
}

_install() {
    echo ""
    echo "=== LarkListener 安装 ==="
    echo ""

    # Check binary
    if [ ! -f "$BINARY" ]; then
        echo "❌ lark-listener 可执行文件不存在"
        return
    fi

    # Check dependencies
    echo "检查依赖..."
    HAS_ERROR=false

    if ! command -v lark-cli &>/dev/null; then
        echo "❌ lark-cli 未安装"
        echo "   请运行: npm install -g @larksuite/cli"
        HAS_ERROR=true
    else
        echo "✓ lark-cli"
    fi

    if ! command -v terminal-notifier &>/dev/null; then
        echo "❌ terminal-notifier 未安装"
        echo "   请运行: brew install terminal-notifier"
        HAS_ERROR=true
    else
        echo "✓ terminal-notifier"
    fi

    if [ "$HAS_ERROR" = true ]; then
        echo ""
        echo "请先安装缺失的依赖，然后重试"
        return
    fi

    echo ""

    # Stop a running instance before replacing the binary: overwriting a live
    # executable on macOS fails with "Text file busy" (and set -e would abort
    # the script), and risks corrupting the running process. No-op on first install.
    if _is_running; then
        echo "停止运行中的服务..."
        _stop
        echo ""
    fi

    # Copy binary
    mkdir -p "$LISTENER_HOME/logs"
    cp "$BINARY" "$LISTENER_HOME/lark-listener"
    chmod +x "$LISTENER_HOME/lark-listener"
    echo "✓ 已安装 lark-listener → $LISTENER_HOME/"

    # Which lark-cli bot (appId) carries the service — a required field chosen at
    # every install/reinstall. Two explicit options: (1) use the current active
    # lark-cli profile (shown with its appId + brand + logged-in user), or (2)
    # type a different appId by hand. No silent default. The chosen value is
    # reused for the wizard's own lark-cli calls and the auth check, and synced
    # back into the config.
    ACTIVE_INFO=$(lark-cli profile list 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); p=next((x for x in d if x.get('active')), None); print('{}|{}|{}'.format(p.get('appId',''), p.get('user',''), p.get('brand','')) if p else '')" 2>/dev/null || echo "")
    ACTIVE_APPID="${ACTIVE_INFO%%|*}"
    _rest="${ACTIVE_INFO#*|}"
    ACTIVE_USER="${_rest%%|*}"
    ACTIVE_BRAND="${_rest##*|}"

    echo ""
    echo "=== 选择承载服务的 lark-cli bot（必填）==="
    APP_ID=""
    if [ -n "$ACTIVE_APPID" ]; then
        echo "  1) 使用当前 active bot：${ACTIVE_APPID}（${ACTIVE_BRAND} / 登录用户: ${ACTIVE_USER}）"
        echo "  2) 手动输入其他 appId"
        read -p "请选择 (1/2): " BOT_CHOICE
        if [ "$BOT_CHOICE" = "1" ]; then
            APP_ID="$ACTIVE_APPID"
        fi
    else
        echo "（未检测到 active profile，请手动输入；如未配置过请先运行 lark-cli config init）"
    fi
    # Manual entry: chose option 2, no active profile, or invalid choice.
    while [ -z "$APP_ID" ]; do
        read -p "请输入承载服务的 lark-cli appId（cli_xxx）: " APP_ID
        APP_ID=$(echo "$APP_ID" | xargs)
    done
    echo "✓ 服务将使用 bot: $APP_ID"

    # Config wizard
    if [ ! -f "$LISTENER_HOME/config.yaml" ]; then
        echo ""
        echo "=== 配置向导 ==="
        echo ""

        # Poll interval
        read -p "轮询间隔（秒，默认 300）: " POLL_INTERVAL
        POLL_INTERVAL=${POLL_INTERVAL:-300}

        # Keywords
        read -p "关注的关键词（逗号分隔，如 部署,故障,发版）: " KEYWORDS_RAW
        KEYWORDS_YAML=""
        if [ -n "$KEYWORDS_RAW" ]; then
            IFS=',' read -ra KW_ARR <<< "$KEYWORDS_RAW"
            for kw in "${KW_ARR[@]}"; do
                kw=$(echo "$kw" | xargs)
                KEYWORDS_YAML="$KEYWORDS_YAML\n  - $kw"
            done
        fi

        # AI config
        echo ""
        echo "AI 模型配置："
        echo "  1) openai（兼容 DeepSeek 等）"
        echo "  2) claude"
        echo "  3) ollama（本地模型）"
        read -p "选择 AI 后端（1/2/3，默认 1）: " AI_CHOICE
        AI_CHOICE=${AI_CHOICE:-1}

        case $AI_CHOICE in
            2) AI_PROVIDER="claude" ;;
            3) AI_PROVIDER="ollama" ;;
            *) AI_PROVIDER="openai" ;;
        esac

        read -p "模型名称（如 gpt-4o, claude-sonnet-4-6, qwen2.5:7b）: " AI_MODEL
        AI_MODEL=${AI_MODEL:-gpt-4o}

        read -p "API Key（ollama 可留空）: " AI_KEY
        AI_KEY=${AI_KEY:-}

        read -p "API Base URL（留空用默认）: " AI_BASE_URL
        AI_BASE_URL=${AI_BASE_URL:-}

        # Get user_id
        echo ""
        echo "获取你的飞书 user_id..."
        USER_ID=$(lark-cli contact +get-user --jq '.data.user.open_id' --profile "$APP_ID" 2>/dev/null | tr -d '"' || echo "")
        if [ -n "$USER_ID" ] && [ "$USER_ID" != "null" ]; then
            echo "✓ 你的 user_id: $USER_ID"
        else
            read -p "无法自动获取，请手动输入 user_id (ou_xxx): " USER_ID
        fi

        # Get bot_chat_id
        echo ""
        echo "获取 Bot chat_id（将发送一条测试消息）..."
        BOT_SEND_RESULT=$(lark-cli im +messages-send --user-id "$USER_ID" --text "LarkListener 安装测试 ✅" --as bot --profile "$APP_ID" 2>&1 || echo "")
        BOT_CHAT_ID=$(echo "$BOT_SEND_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('chat_id',''))" 2>/dev/null || echo "")
        if [ -n "$BOT_CHAT_ID" ]; then
            echo "✓ Bot chat_id: $BOT_CHAT_ID"
        else
            read -p "无法自动获取，请手动输入 bot_chat_id (oc_xxx): " BOT_CHAT_ID
        fi

        # Write config
        cat > "$LISTENER_HOME/config.yaml" <<CONF
# 轮询间隔（秒）
poll_interval: $POLL_INTERVAL

# 【必填】承载本服务的 lark-cli bot 的 appId（见 \`lark-cli profile list\`）
lark_cli_appid: $APP_ID

# 是否汇总 @所有人 的消息（关键词命中的仍会汇总）
include_at_all: true

# AI 分析时拉取的上下文消息数（0 则不拉取）
context_messages: 20

# 关注的关键词
keywords:$(echo -e "$KEYWORDS_YAML")

# AI 模型配置
ai:
  provider: $AI_PROVIDER
  model: $AI_MODEL
  api_key: $AI_KEY
  base_url: "$AI_BASE_URL"

# 屏蔽的聊天（这些聊天的消息不会被汇总）
exclude_chat_ids:
  - $BOT_CHAT_ID    # LarkListener Bot

# 推送目标
notify:
  user_id: $USER_ID
  bot_chat_id: $BOT_CHAT_ID
CONF
        echo ""
        echo "✓ 配置文件已生成"
    else
        echo "✓ 配置文件已存在，保留其余配置"
        # Sync the required lark_cli_appid to the chosen value: replace the
        # existing key's value in place (preserving its comment), or append it
        # as a top-level key if the file predates this field.
        if grep -q '^lark_cli_appid:' "$LISTENER_HOME/config.yaml"; then
            /usr/bin/sed -i '' "s|^lark_cli_appid:.*|lark_cli_appid: $APP_ID|" "$LISTENER_HOME/config.yaml"
        else
            printf '\n# 【必填】承载本服务的 lark-cli bot 的 appId\nlark_cli_appid: %s\n' "$APP_ID" >> "$LISTENER_HOME/config.yaml"
        fi
        echo "✓ lark_cli_appid = $APP_ID"
    fi

    # Write launchd plist
    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.larklistener</string>
    <key>ProgramArguments</key>
    <array>
        <string>$LISTENER_HOME/lark-listener</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$LISTENER_HOME</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$LISTENER_HOME/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LISTENER_HOME/logs/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
    echo "✓ 已写入 launchd 配置"

    # lark-cli authorization, targeted at the bot that carries the service via
    # --profile "$APP_ID". The bot identity (send/react/listen) needs no login;
    # message search needs the user-identity `search:message` scope, so that's
    # what we authorize here. Login is initiated in-place when missing.
    REQUIRED_SCOPE="search:message"
    echo ""
    echo "检查 lark-cli 登录状态（bot: ${APP_ID}）..."
    NEEDS_LOGIN=false
    if ! lark-cli contact +get-user --jq '.data.user.name' --profile "$APP_ID" 2>/dev/null | grep -q .; then
        echo "○ 该 bot 尚未登录 user 身份"
        NEEDS_LOGIN=true
    else
        USER_NAME=$(lark-cli contact +get-user --jq '.data.user.name' --profile "$APP_ID" 2>/dev/null || echo "")
        echo "✓ 已登录: $USER_NAME"
        if ! lark-cli im +messages-search --chat-type p2p --start "2020-01-01T00:00:00+08:00" --end "2020-01-01T00:01:00+08:00" --format json --profile "$APP_ID" 2>&1 | grep -q '"ok": true'; then
            echo "○ 缺少 $REQUIRED_SCOPE 权限"
            NEEDS_LOGIN=true
        else
            echo "✓ $REQUIRED_SCOPE 权限正常"
        fi
    fi

    if [ "$NEEDS_LOGIN" = true ]; then
        echo ""
        echo "需要为该 bot 授权 user 身份（浏览器打开链接完成授权）："
        echo "   lark-cli auth login --profile $APP_ID --scope \"$REQUIRED_SCOPE\""
        echo ""
        read -p "现在发起授权登录？(Y/n) " DO_LOGIN
        DO_LOGIN=${DO_LOGIN:-Y}
        if [ "$DO_LOGIN" = "Y" ] || [ "$DO_LOGIN" = "y" ]; then
            if lark-cli auth login --profile "$APP_ID" --scope "$REQUIRED_SCOPE"; then
                echo "✓ 授权完成"
            else
                echo "⚠️  授权未完成，稍后可手动重试上面的命令"
            fi
        else
            echo "已跳过。服务需要该权限才能拉取消息，稍后请手动运行上面的命令。"
        fi
    fi

    echo ""
    echo "=== 安装完成 ==="
    echo ""
    echo "使用方式："
    echo "  • 回到菜单选择「启动服务」"
    echo "  • 给 Bot 发送「汇总」可立即触发"
    echo "  • 给 Bot 发送「汇总最近2小时」可指定时间范围"
}

_stop() {
    if _is_running; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        sleep 1
    fi
    # Kill all related processes: main process + lark-cli event subprocess
    pkill -f "$LISTENER_HOME/lark-listener" 2>/dev/null || true
    pkill -f "lark-cli event.*--as bot" 2>/dev/null || true
    sleep 1
    # Force kill if still alive
    pkill -9 -f "$LISTENER_HOME/lark-listener" 2>/dev/null || true
    pkill -9 -f "lark-cli event.*--as bot" 2>/dev/null || true
    echo "✓ 服务已停止"
}

_start() {
    if ! _is_installed; then
        echo "❌ 未安装，请先选择「安装」"
        return
    fi

    # Guarantee the service binds to the configured bot before starting. The
    # binary pins every lark-cli call to lark_cli_appid via --profile, but only
    # if that value is present — an empty one would fail validation each poll.
    APP_ID=$(python3 -c "import yaml; print((yaml.safe_load(open('$LISTENER_HOME/config.yaml')) or {}).get('lark_cli_appid','') or '')" 2>/dev/null || echo "")
    if [ -z "$APP_ID" ]; then
        echo "❌ 配置缺少 lark_cli_appid，无法确定承载服务的 bot。"
        echo "   请选择「5) 重新安装」设置，或编辑 ${LISTENER_HOME}/config.yaml"
        return
    fi
    echo "本次服务将连接 bot: ${APP_ID}"

    # Verify that bot is authorized for message search (user identity). Warn but
    # still start — auth can be completed later and bot-side features still work.
    if lark-cli im +messages-search --chat-type p2p --start "2020-01-01T00:00:00+08:00" --end "2020-01-01T00:01:00+08:00" --format json --profile "$APP_ID" 2>&1 | grep -q '"ok": true'; then
        echo "✓ 该 bot 已授权 search:message"
    else
        echo "⚠️  该 bot 尚未授权 search:message（或登录已过期），可能拉不到消息："
        echo "   lark-cli auth login --profile ${APP_ID} --scope \"search:message\""
    fi

    if _is_running; then
        echo "正在重启..."
        _stop
    fi
    launchctl load "$PLIST_PATH"
    sleep 3
    if _is_running; then
        echo "✓ 服务已启动（已绑定 bot: ${APP_ID}）"
    else
        echo "❌ 启动失败，请查看日志:"
        echo "  cat $LISTENER_HOME/logs/stderr.log"
    fi
}

_config() {
    CONFIG="$LISTENER_HOME/config.yaml"
    if [ ! -f "$CONFIG" ]; then
        echo "❌ 配置文件不存在，请先安装"
        return
    fi
    open -t "$CONFIG"
    echo "✓ 已打开配置文件（修改后下次轮询自动生效）"
}

_uninstall() {
    echo ""
    echo "⚠️  即将卸载 LarkListener，这将删除："
    echo "  - 服务进程"
    echo "  - launchd 配置"
    echo "  - ${LISTENER_HOME}（含配置、日志、可执行文件）"
    echo ""
    read -p "确认卸载？(y/N) " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "已取消"
        return
    fi
    _stop
    rm -f "$PLIST_PATH"
    rm -rf "$LISTENER_HOME"
    echo "✓ 已卸载完成"
    echo ""
    read -p "按回车退出..." _
    exit 0
}

_menu() {
    clear
    echo "╔══════════════════════════════╗"
    echo "║      LarkListener 管理       ║"
    echo "╚══════════════════════════════╝"
    echo ""
    _status
    echo ""
    if ! _is_installed; then
        echo "  1) 安装"
    else
        echo "  1) 启动 / 重启服务"
        echo "  2) 停止服务"
        echo "  3) 修改配置"
        echo "  4) 查看日志"
        echo "  5) 重新安装"
        echo "  6) 卸载"
    fi
    echo "  0) 退出"
    echo ""
    read -p "请选择: " CHOICE

    if ! _is_installed; then
        case "$CHOICE" in
            1) _install ;;
            0) exit 0 ;;
            *) echo "无效选项" ;;
        esac
    else
        case "$CHOICE" in
            1) _start ;;
            2) _stop ;;
            3) _config ;;
            4)
                echo ""
                echo "--- 最近 20 条日志 ---"
                tail -20 "$LISTENER_HOME/logs/stderr.log" 2>/dev/null || echo "暂无日志"
                ;;
            5) _install ;;
            6) _uninstall ;;
            0) exit 0 ;;
            *) echo "无效选项" ;;
        esac
    fi

    echo ""
    read -rp "按回车返回菜单..." _
}

while true; do
    _menu
done
