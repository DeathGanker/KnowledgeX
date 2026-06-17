#!/usr/bin/env bash
# 双击启动「个人知识助手」桌面端（Tauri）。首次会 npm install + 编译 Rust，稍慢。
set -u
cd "$(dirname "$0")" || exit 1

banner() { printf "\n\033[1;36m▎%s\033[0m\n" "$1"; }

banner "个人知识助手 · 桌面端 · $(date '+%Y-%m-%d %H:%M:%S')"

# 后端 venv 检查
if [[ ! -x ../.venv/bin/python ]]; then
  echo "❌ 未找到后端 .venv，请先："
  echo "   cd .. && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r -p "按回车关闭…"; exit 1
fi

# Rust 检查
if ! command -v cargo >/dev/null 2>&1; then
  echo "❌ 未检测到 Rust（cargo）。Tauri 需要，请先安装：https://rustup.rs"
  read -r -p "按回车关闭…"; exit 1
fi

# Node 依赖
if [[ ! -d node_modules ]]; then
  banner "首次运行：安装 Node 依赖（npm install）"
  npm install || { echo "npm install 失败"; read -r -p "按回车关闭…"; exit 1; }
fi

banner "启动（npm run tauri:dev）"
npm run tauri:dev
status=$?

echo
[[ $status -eq 0 ]] || banner "⚠️ 退出码 $status"
read -r -p "按回车关闭窗口…"
