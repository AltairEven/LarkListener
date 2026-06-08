#!/bin/bash
set -euo pipefail

REPO="https://github.com/AltairEven/LarkListener.git"
VENV="$HOME/.lark_listener/venv"
SHIM_RECORD="$HOME/.lark_listener/shim_link"   # 记录软链实际位置，供 uninstall 精确清理

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
#    pip 升级非致命（wheel 安装通常不需要新 pip）：set -e 下若静默退出会让用户无从判断，
#    故显式兜底并提示，不中断安装。
"$VENV/bin/pip" install --upgrade pip >/dev/null 2>&1 \
    || echo "⚠️  pip 升级失败（继续；若下一步安装失败请检查网络）"
"$VENV/bin/pip" install --force-reinstall "git+$REPO"

# 6) 软链短命令（ensurepath 式，best-effort）。plist 另指向 venv 真实入口，软链仅为便利。
#    优先「可写 且 已在 PATH」的目录（免改 shell 配置，brew 用户开箱即用）；
#    否则用 ~/.local/bin 并把它幂等加入 PATH。
SHIM_DIR=""; IN_PATH=false
for d in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
    if [ -d "$d" ] && [ -w "$d" ] && case ":$PATH:" in *":$d:"*) true ;; *) false ;; esac; then
        SHIM_DIR="$d"; IN_PATH=true; break
    fi
done
if [ -z "$SHIM_DIR" ] && mkdir -p "$HOME/.local/bin" 2>/dev/null && [ -w "$HOME/.local/bin" ]; then
    SHIM_DIR="$HOME/.local/bin"
    case ":$PATH:" in *":$HOME/.local/bin:"*) IN_PATH=true ;; *) IN_PATH=false ;; esac
fi

SHIM_OK=false; PATH_INJECTED=false; SHIM=""
if [ -n "$SHIM_DIR" ] && ln -sf "$VENV/bin/lark-listener" "$SHIM_DIR/lark-listener" 2>/dev/null; then
    SHIM_OK=true; SHIM="$SHIM_DIR/lark-listener"
    printf '%s\n' "$SHIM" > "$SHIM_RECORD" 2>/dev/null || true   # 记录位置供 uninstall
    echo "✓ 短命令软链：$SHIM"
fi

# 6b) 软链目录不在 PATH → 幂等注入用户 shell 配置
if $SHIM_OK && ! $IN_PATH; then
    case "$(basename "${SHELL:-zsh}")" in
        bash) RC="$HOME/.bash_profile" ;;
        zsh)  RC="$HOME/.zshrc" ;;
        *)    RC="$HOME/.profile" ;;
    esac
    # 用安装器自己的标记行判断是否已注入，而非裸目录名——后者可能因 rc 里
    # 别处提到该路径而误判为已注入，从而跳过本该做的注入。
    if [ -f "$RC" ] && grep -qF "# Added by LarkListener installer" "$RC" 2>/dev/null; then
        PATH_INJECTED=true   # 已注入过
    elif printf '\n# Added by LarkListener installer\nexport PATH="%s:$PATH"\n' "$SHIM_DIR" >> "$RC" 2>/dev/null; then
        PATH_INJECTED=true
    fi
fi

# 7) 结尾：始终给一定可用的 venv 绝对路径，并按软链情况提示短命令
echo ""
echo "✅ 安装完成。现在运行配置向导："
if $SHIM_OK && $IN_PATH; then
    echo "   lark-listener setup"
elif $SHIM_OK && $PATH_INJECTED; then
    echo "   lark-listener setup        # 已把 $SHIM_DIR 加入 PATH，请【重开终端】后生效"
    echo "   （本终端想立即用，则跑：$VENV/bin/lark-listener setup）"
else
    echo "   $VENV/bin/lark-listener setup"
    echo "   （未能创建短命令软链；用上面绝对路径即可。日常使用是在飞书跟 Bot 聊天，不受影响。）"
fi
