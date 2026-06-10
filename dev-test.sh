#!/bin/bash
# LarkListener 开发测试脚本 —— 可反复使用，全程 dev 隔离（不碰生产 ~/.lark_listener
# 与 launchd com.larklistener）。
#
# 用法：
#   ./dev-test.sh           # 默认 = unit + smoke（安全，无外部副作用）
#   ./dev-test.sh unit      # 仅单元测试
#   ./dev-test.sh smoke     # 隔离生命周期冒烟（安装文件层→状态→卸载，自我清理，不发飞书/不load）
#   ./dev-test.sh full      # 完整真跑（建 venv→setup→start→更新→卸载；★会发真实飞书消息、真起 dev 服务）
#   ./dev-test.sh clean     # 清理所有 dev 残留
#
# dev 隔离环境（可被外部 env 覆盖）：
export LARK_LISTENER_HOME="${LARK_LISTENER_HOME:-/tmp/ll-dev}"
export LARK_LISTENER_LABEL="${LARK_LISTENER_LABEL:-com.larklistener.dev}"

set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LARK_LISTENER_LABEL.plist"

# 跑 CLI/import 用的 python：优先项目 .venv（editable，见 CLAUDE.md「三层策略」），否则系统 python3。
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PY="$PROJECT_DIR/.venv/bin/python"
else
    PY="python3"
fi

_unit() {
    echo "=== 单元测试 ==="
    ( cd "$PROJECT_DIR" && python3 -m pytest -q )
}

_smoke() {
    echo "=== 隔离生命周期冒烟（文件层，自我清理；HOME=${LARK_LISTENER_HOME}）==="
    rm -rf "$LARK_LISTENER_HOME"; rm -f "$PLIST"

    echo "[1] 安装前 status（应：未安装）"
    ( cd "$PROJECT_DIR" && "$PY" -m lark_listener.main status )

    echo "[2] 写 config + plist（不 load / 不发飞书）"
    ( cd "$PROJECT_DIR" && "$PY" -c "
from lark_listener import service, setup_wizard
service.LISTENER_HOME.mkdir(parents=True, exist_ok=True)
(service.LISTENER_HOME/'logs').mkdir(parents=True, exist_ok=True)
cfg = setup_wizard.build_config_dict(
    poll_interval=300, appid='cli_dev', keywords=['上线'],
    ai_provider='ollama', ai_model='qwen2.5', ai_key='', ai_base_url='',
    user_id='ou_dev', bot_chat_id='oc_dev')
setup_wizard.write_config_file(str(service.LISTENER_HOME/'config.yaml'), cfg)
service.PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
service.PLIST_PATH.write_text(service.build_plist(service.shim_path(), [d for d in [service.node_bin_dir()] if d]))
print('  config:', service.LISTENER_HOME/'config.yaml')
print('  plist :', service.PLIST_PATH)
" )
    echo "  --- plist ProgramArguments（应：venv 绝对路径 + run）---"
    /usr/libexec/PlistBuddy -c "Print :ProgramArguments" "$PLIST"
    echo -n "  波浪号检查: "; grep -q '~' "$PLIST" && echo "✗ 含 ~（错误）" || echo "✓ 无 ~"

    echo "[3] 安装后 status（应：已安装，未运行）"
    ( cd "$PROJECT_DIR" && "$PY" -m lark_listener.main status )

    echo "[4] 卸载（echo y 自动确认）"
    ( cd "$PROJECT_DIR" && echo y | "$PY" -m lark_listener.main uninstall )

    echo "[5] 卸载后 status + 残留检查"
    ( cd "$PROJECT_DIR" && "$PY" -m lark_listener.main status )
    [ -d "$LARK_LISTENER_HOME" ] && echo "  ✗ HOME 残留" || echo "  ✓ HOME 已删"
    [ -f "$PLIST" ] && echo "  ✗ plist 残留" || echo "  ✓ plist 已删"
}

_full() {
    echo "=== 完整真跑（建 venv→setup→start→更新→卸载）==="
    echo "★ 注意：这会发真实飞书测试消息、真起一个 dev 服务（Label: ${LARK_LISTENER_LABEL}）。"
    read -p "继续？(y/N) " ans; [ "$ans" = y ] || { echo "已取消"; return; }
    rm -rf "$LARK_LISTENER_HOME"; rm -f "$PLIST"

    local VENV="$LARK_LISTENER_HOME/venv"
    local EXE="$VENV/bin/lark-listener"

    echo "[install] 建 venv + 装工作树（editable，核心依赖）"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -e "$PROJECT_DIR"

    echo "[setup] 交互向导（选后端会按需装对应 AI SDK）"
    "$EXE" setup

    echo "[start] 真 launchctl load"
    "$EXE" start && "$EXE" status

    echo "[update] 重装 + 重启（模拟 pip --force-reinstall + restart）"
    "$VENV/bin/pip" install -q --force-reinstall -e "$PROJECT_DIR"
    "$EXE" restart && "$EXE" status

    echo "[uninstall] 卸载并清理"
    echo y | "$EXE" uninstall
    echo "（已卸载，venv 随 HOME 删除）"
}

_clean() {
    echo "=== 清理 dev 残留 ==="
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    rm -rf "$LARK_LISTENER_HOME"
    echo "✓ 已清理 $LARK_LISTENER_HOME 与 $PLIST"
}

case "${1:-default}" in
    unit)    _unit ;;
    smoke)   _smoke ;;
    full)    _full ;;
    clean)   _clean ;;
    default) _unit && echo "" && _smoke ;;
    *) echo "用法: $0 [unit|smoke|full|clean]（无参 = unit + smoke）"; exit 1 ;;
esac
