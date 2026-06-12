#!/usr/bin/env bash
# 重启 KnowledgeX Web 应用（先杀旧进程，再启动），并自动打开浏览器
set -u
cd "$(dirname "$0")" || exit 1

banner() {
    printf "\n\033[1;36m▎%s\033[0m\n" "$1"
}

banner "KnowledgeX Web · 重启 · $(date '+%Y-%m-%d %H:%M:%S')"
echo "工作目录: $(pwd)"

if [[ ! -x .venv/bin/python ]]; then
    echo
    echo "❌ 未找到 .venv，请先在仓库根目录初始化："
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    echo
    read -r -p "按回车关闭窗口..."
    exit 1
fi

# 1) 先杀掉所有旧的 web.app 进程（关键：否则旧进程占着端口，加载的是旧代码）
banner "停止旧服务进程…"
pkill -f "web.app" 2>/dev/null && echo "  已发送停止信号" || echo "  无运行中的旧进程"
sleep 1.5

# 2) 读端口（默认 7345）
PORT=$(grep "^WEB_PORT=" .env 2>/dev/null | head -1 | cut -d= -f2)
PORT=${PORT:-7345}

# 3) 启动新服务
banner "启动 FastAPI（监听 0.0.0.0:${PORT}）"
.venv/bin/python -m web.app &
SERVER_PID=$!

# 等服务端起来
sleep 3

# 4) 读 token（首次启动后已写入 .env）打开浏览器
TOKEN=$(grep "^WEB_TOKEN=" .env | head -1 | cut -d= -f2)
URL="http://localhost:${PORT}/?token=${TOKEN}"
banner "在浏览器打开：$URL"
open "$URL"

echo
echo "服务进程 PID: $SERVER_PID"
echo "关闭服务请在本窗口按 Ctrl+C，或直接关掉窗口"

# 把 Ctrl+C 转发给 uvicorn
trap "kill $SERVER_PID 2>/dev/null; exit 0" INT TERM
wait $SERVER_PID
