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

# 6) 软链短命令到 ~/.local/bin（best-effort：失败不影响安装，仍可用 venv 绝对路径）
#    plist 另指向 venv 内真实入口，软链仅为短命令便利。
SHIM_OK=false
if mkdir -p "$HOME/.local/bin" 2>/dev/null && ln -sf "$VENV/bin/lark-listener" "$SHIM" 2>/dev/null; then
    SHIM_OK=true
    echo "✓ 已安装，短命令软链：$SHIM"
else
    echo "⚠️  无法在 ~/.local/bin 创建短命令软链（目录不可写）——不影响使用，用绝对路径即可。"
fi

# 7) 结尾：提示手动跑 setup（始终给一定可用的 venv 绝对路径）
echo ""
echo "✅ 安装完成。现在运行配置向导："
if $SHIM_OK; then
    echo "   lark-listener setup"
    echo "   （若提示 command not found，说明 ~/.local/bin 不在 PATH，请改用：$VENV/bin/lark-listener setup）"
else
    echo "   $VENV/bin/lark-listener setup"
    echo ""
    echo "   想用短命令 lark-listener？修复 ~/.local/bin 写权限后重建软链："
    echo "     sudo chown \$(whoami) ~/.local/bin"
    echo "     ln -sf $VENV/bin/lark-listener ~/.local/bin/lark-listener"
fi
