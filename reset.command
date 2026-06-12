#!/usr/bin/env bash
# 重置本地环境到「全新克隆、从未运行」的静默状态 —— 双击即运行（macOS .command）
# 保留 .env / config.yaml / 代码；清运行态缓存 + 画像 +（确认后）vault 笔记。
set -u
cd "$(dirname "$0")" || { echo "无法 cd 到脚本目录"; exit 1; }

banner() { printf "\n\033[1;36m▎%s\033[0m\n" "$1"; }

banner "KnowledgeX 环境重置 · $(date '+%Y-%m-%d %H:%M:%S')"

PY=".venv/bin/python"
if [[ ! -x "$PY" ]]; then
    echo
    echo "❌ 未找到 .venv，请先在仓库根目录初始化："
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    echo
    read -r -p "按回车关闭窗口..."
    exit 1
fi

"$PY" scripts/reset_local.py
echo
read -r -p "按回车关闭窗口..."
