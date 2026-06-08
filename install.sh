#!/bin/bash
set -euo pipefail

REPO="https://github.com/AltairEven/LarkListener.git"
VENV="$HOME/.lark_listener/venv"
SHIM="$HOME/.local/bin/lark-listener"

echo "=== LarkListener 安装 ==="

# 1) python3 ≥ 3.9
if ! command -v python3 >/dev/null 2>&1; then
    echo "未检测到 python3，触发 Apple 命令行工具安装（系统级、已公证）..."
    xcode-select --install || true
    echo "请在弹窗完成 Command Line Tools 安装后重跑本脚本。"
    exit 1
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)'; then
    echo "❌ python3 版本过低（$PYV），需 ≥ 3.9。"
    exit 1
fi
echo "✓ python3 $PYV"

# 2) git（pip install git+ 需要；macOS 随 CLT 提供）
if ! command -v git >/dev/null 2>&1; then
    echo "❌ 未检测到 git，请先安装 Xcode Command Line Tools: xcode-select --install"
    exit 1
fi
echo "✓ git"

# 3) 检测 lark-cli（本工具的前提，不自动安装）
if ! command -v lark-cli >/dev/null 2>&1; then
    echo ""
    echo "⚠️  未检测到 lark-cli（本工具的前提）。请先安装并登录后重跑本脚本："
    echo "   npm install -g @larksuite/cli"
    echo "   lark-cli config init"
    echo "   lark-cli auth login --scope search:message"
    exit 1
fi
echo "✓ lark-cli"

# 4) 建隔离环境（标准库 venv，无需额外工具；已存在则复用）
#    venv 内的 pip 不受 PEP 668 externally-managed 限制，无需任何回退。
if [ ! -x "$VENV/bin/python" ]; then
    echo "创建虚拟环境：$VENV"
    python3 -m venv "$VENV"
fi

# 5) 安装本工具（venv 内 pip）
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install --force-reinstall "git+$REPO"

# 6) 软链短命令到 ~/.local/bin（plist 另指向 venv 内真实入口，软链仅为短命令）
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/lark-listener" "$SHIM"
echo "✓ 已安装，短命令软链：$SHIM"

# 7) 结尾：提示手动跑 setup（用绝对路径，不依赖 PATH 刷新）
echo ""
echo "✅ 安装完成。现在运行："
echo "   ~/.local/bin/lark-listener setup"
echo "（新开终端后，若 ~/.local/bin 在 PATH，可直接用 lark-listener setup）"
