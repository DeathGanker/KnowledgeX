#!/usr/bin/env bash
# 处理收件箱 —— 双击即运行（macOS .command 文件）
# 在 Finder 中双击此文件会自动用 Terminal 打开并执行
set -u

# 切到本脚本所在目录（无论被从哪里调用）
cd "$(dirname "$0")" || { echo "无法 cd 到脚本目录"; exit 1; }

# 让 Terminal 窗口在显示这条提示后定格几秒，避免一闪而过
banner() {
    printf "\n\033[1;36m▎%s\033[0m\n" "$1"
}

banner "KnowledgeX 收件箱处理 · $(date '+%Y-%m-%d %H:%M:%S')"
echo "工作目录: $(pwd)"

PY=".venv/bin/python"
if [[ ! -x "$PY" ]]; then
    echo
    echo "❌ 未找到 .venv，请先在仓库根目录初始化："
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    echo
    read -r -p "按回车关闭窗口..."
    exit 1
fi

banner "开始处理"
"$PY" scripts/process_inbox.py
status=$?

echo
if [[ $status -eq 0 ]]; then
    banner "✅ 完成（退出码 0）"
else
    banner "⚠️ 异常（退出码 $status，详见 logs/$(date '+%Y-%m-%d').log）"
fi

echo
read -r -p "按回车关闭窗口..."
