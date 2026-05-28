#!/bin/bash
set -euo pipefail

LISTENER_HOME="$HOME/.lark_listener"
PLIST_PATH="$HOME/Library/LaunchAgents/com.larklistener.plist"

_is_running() {
    launchctl list 2>/dev/null | grep -q com.larklistener
}

_stop() {
    if _is_running; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        pkill -f "lark-cli event" 2>/dev/null || true
        sleep 2
        echo "✓ 服务已停止"
    else
        echo "服务未在运行"
    fi
}

_start() {
    if [ ! -f "$PLIST_PATH" ]; then
        echo "❌ 未安装，请先运行 ./install.sh"
        exit 1
    fi
    if _is_running; then
        echo "服务正在运行，正在重启..."
        _stop
    fi
    launchctl load "$PLIST_PATH"
    sleep 3
    if _is_running; then
        echo "✓ 服务已启动"
        echo "  查看日志: tail -f $LISTENER_HOME/logs/stderr.log"
    else
        echo "❌ 启动失败，请检查日志:"
        echo "  cat $LISTENER_HOME/logs/stderr.log"
    fi
}

_config() {
    CONFIG="$LISTENER_HOME/config.yaml"
    if [ ! -f "$CONFIG" ]; then
        echo "❌ 配置文件不存在，请先运行 ./install.sh"
        exit 1
    fi
    # Prefer user's EDITOR, fallback to vim/nano
    EDITOR=${EDITOR:-$(command -v vim || command -v nano || echo vi)}
    echo "正在打开配置文件..."
    "$EDITOR" "$CONFIG"
    echo "✓ 配置已保存（下次轮询时自动生效）"
}

_uninstall() {
    echo "⚠️  即将卸载 LarkListener，这将删除："
    echo "  - 服务进程"
    echo "  - $PLIST_PATH"
    echo "  - $LISTENER_HOME（含配置、日志、可执行文件）"
    echo ""
    read -p "确认卸载？(y/N) " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "已取消"
        exit 0
    fi

    _stop
    rm -f "$PLIST_PATH"
    rm -rf "$LISTENER_HOME"
    echo "✓ 已卸载完成"
}

_usage() {
    echo "LarkListener 服务管理"
    echo ""
    echo "用法: $0 <命令>"
    echo ""
    echo "命令:"
    echo "  start      启动服务（如已运行则重启）"
    echo "  stop       停止服务"
    echo "  config     编辑配置文件"
    echo "  uninstall  卸载服务"
}

case "${1:-}" in
    start)     _start ;;
    stop)      _stop ;;
    config)    _config ;;
    uninstall) _uninstall ;;
    *)         _usage ;;
esac
