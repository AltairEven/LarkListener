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
        echo "   请运行: npm install -g @nicholaschen/lark-cli"
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

    # Copy binary
    mkdir -p "$LISTENER_HOME/logs"
    cp "$BINARY" "$LISTENER_HOME/lark-listener"
    chmod +x "$LISTENER_HOME/lark-listener"
    echo "✓ 已安装 lark-listener → $LISTENER_HOME/"

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
        USER_ID=$(lark-cli contact +get-user --jq '.data.user.open_id' 2>/dev/null | tr -d '"' || echo "")
        if [ -n "$USER_ID" ] && [ "$USER_ID" != "null" ]; then
            echo "✓ 你的 user_id: $USER_ID"
        else
            read -p "无法自动获取，请手动输入 user_id (ou_xxx): " USER_ID
        fi

        # Get bot_chat_id
        echo ""
        echo "获取 Bot chat_id（将发送一条测试消息）..."
        BOT_SEND_RESULT=$(lark-cli im +messages-send --user-id "$USER_ID" --text "LarkListener 安装测试 ✅" --as bot 2>&1 || echo "")
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
        echo "✓ 配置文件已存在，跳过"
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

    # Check lark-cli auth
    echo ""
    echo "检查 lark-cli 登录状态..."
    if ! lark-cli contact +get-user --jq '.data.user.name' 2>/dev/null | grep -q .; then
        echo "⚠️  lark-cli 未登录，请先完成登录："
        echo "   lark-cli auth login --scope \"search:message\""
    else
        USER_NAME=$(lark-cli contact +get-user --jq '.data.user.name' 2>/dev/null)
        echo "✓ 已登录: $USER_NAME"
        if ! lark-cli im +messages-search --chat-type p2p --start "2020-01-01T00:00:00+08:00" --end "2020-01-01T00:01:00+08:00" --format json 2>&1 | grep -q '"ok": true'; then
            echo "⚠️  缺少 search:message 权限，请运行："
            echo "   lark-cli auth login --scope \"search:message\""
        else
            echo "✓ search:message 权限正常"
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
    if _is_running; then
        echo "正在重启..."
        _stop
    fi
    launchctl load "$PLIST_PATH"
    sleep 3
    if _is_running; then
        echo "✓ 服务已启动"
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
