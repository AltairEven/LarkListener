#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$PROJECT_DIR/build"
WORK_DIR="$PROJECT_DIR/.build_tmp"

echo "=== LarkListener Build ==="

# 1. Check pyinstaller
if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller..."
    pip3 install pyinstaller --quiet
fi

# 2. Build
echo "Building..."
cd "$PROJECT_DIR"
python3 -m PyInstaller \
    --onefile run_service.py \
    --name lark-listener \
    --distpath "$DIST_DIR" \
    --workpath "$WORK_DIR" \
    --clean \
    --noconfirm \
    --log-level WARN

rm -rf "$WORK_DIR" "$PROJECT_DIR/lark-listener.spec"

# 3. Copy management tool
cp "$PROJECT_DIR/LarkListener.command" "$DIST_DIR/"

echo ""
echo "=== Build complete ==="
echo "Output: $DIST_DIR/"
echo "  lark-listener          可执行文件"
echo "  LarkListener.command   管理工具（双击运行）"
