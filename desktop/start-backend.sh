#!/usr/bin/env bash
# 由 Tauri 的 beforeDevCommand 调用：用项目自带 .venv 启动 FastAPI 后端，
# 仅绑定 127.0.0.1:<DESKTOP_PORT>，并开启 DESKTOP_LOCAL（免 token）。
# 进程在前台常驻，Tauri 轮询 devUrl 起来后再开窗口；关掉窗口时 Tauri 收掉本进程。
set -euo pipefail

# desktop/ 的上一级就是仓库根（含 web/、scripts/、.venv 等）
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

PORT="${DESKTOP_PORT:-7346}"

if [[ ! -x .venv/bin/python ]]; then
  echo "❌ 未找到 .venv，请先在仓库根目录初始化后端依赖："
  echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# 用 DESKTOP_* 注入（这些键不在 .env 里，不会被 config.py 的 load_dotenv(override=True) 覆盖）。
# config.py 在 DESKTOP_LOCAL=1 时会强制 127.0.0.1 并用 DESKTOP_PORT。
export DESKTOP_LOCAL=1
export DESKTOP_PORT="$PORT"

# 透传海外代理：从 .env 读出 HTTPS_PROXY/HTTP_PROXY/ALL_PROXY 显式导出，
# 让 DeepWiki、GitHub 抓取等海外出站走代理；国内豆包/embedding 用 mounts 硬禁代理，不受影响。
# （python 端 load_dotenv 也会读，这里再导出一次以覆盖子进程并在启动日志里可见。）
for _k in HTTPS_PROXY HTTP_PROXY ALL_PROXY; do
  _v=$(grep -E "^${_k}=" .env 2>/dev/null | head -1 | cut -d= -f2-)
  if [[ -n "${_v:-}" ]]; then export "${_k}=${_v}"; echo "▎代理透传 ${_k}=${_v}"; fi
done

echo "▎个人知识助手 · 桌面端后端 → http://127.0.0.1:${PORT}"
exec .venv/bin/python -m web.app
