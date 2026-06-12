"""Web 应用配置：路径、token、端口"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv


WEB_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = WEB_DIR.parent

load_dotenv(PIPELINE_DIR / ".env", override=True)

# 笔记目录：可配置（.env 的 VAULT_ROOT），默认兼容旧布局，全新克隆默认 ~/KnowledgeX。
# web 端与管道脚本共用 scripts/paths 的单一解析逻辑。首次运行自动建标准目录。
import sys as _sys
if str(PIPELINE_DIR) not in _sys.path:
    _sys.path.insert(0, str(PIPELINE_DIR))
from scripts import paths as _paths  # noqa: E402  已在 import 时解析好 VAULT_ROOT
VAULT_ROOT = _paths.ensure_vault(_paths.VAULT_ROOT)


def _load_pipeline_config() -> dict:
    # 统一走 scripts/paths：读 config.yaml + 叠加 .env 的端点覆盖（LLM_BASE_URL/LLM_MODEL/…）
    return _paths.load_pipeline_config()


def _get_or_create_web_token() -> str:
    """从 .env 读 WEB_TOKEN，没有就生成一个写回去。"""
    token = os.environ.get("WEB_TOKEN")
    if token:
        return token
    token = secrets.token_urlsafe(24)
    env_path = PIPELINE_DIR / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    text = text.rstrip() + f"\n\n# Web 鉴权 token（自动生成，启动 URL 必须带 ?token=<这个值>）\nWEB_TOKEN={token}\n"
    env_path.write_text(text, encoding="utf-8")
    os.environ["WEB_TOKEN"] = token
    return token


PIPELINE_CONFIG = _load_pipeline_config()
WEB_TOKEN = _get_or_create_web_token()

# 桌面端（Tauri）模式：强制回环 + 独立端口。
# DESKTOP_LOCAL / DESKTOP_PORT 由 desktop/start-backend.sh 注入，且不写在 .env 里，
# 因此不会被上面的 load_dotenv(override=True) 覆盖（而 WEB_PORT 在 .env 里会被覆盖，
# 这正是之前后端起在 7345、Tauri 却等 7346 连不上的原因）。
if os.environ.get("DESKTOP_LOCAL") == "1":
    HOST = "127.0.0.1"
    PORT = int(os.environ.get("DESKTOP_PORT", "7346"))
else:
    HOST = os.environ.get("WEB_HOST", "0.0.0.0")
    PORT = int(os.environ.get("WEB_PORT", "7333"))


# 笔记浏览时可见的目录前缀（用于侧栏文件树）
VISIBLE_DIRS = [
    "00-收件箱",
    "01-笔记",
    "02-领域",
    "03-资源",
    "04-项目",
    "05-归档",
]
