"""token 鉴权中间件 —— URL 参数或 Cookie 任一通过即可"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from web.config import WEB_TOKEN


# 这些路径不需要鉴权
ALLOWLIST_PREFIXES = ("/login", "/static/", "/favicon.ico", "/health")

# 桌面端（Tauri）模式：后端仅绑定 127.0.0.1，由本机 webview 独占访问，
# 无需 token。由 desktop/start-backend.sh 设置 DESKTOP_LOCAL=1。
# 浏览器(BS)模式不设此变量，token 鉴权照常生效。
DESKTOP_LOCAL = os.environ.get("DESKTOP_LOCAL") == "1"


def _token_ok(token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token, WEB_TOKEN)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """检查 URL ?token=xxx 或 Cookie web_token。首次 ?token 命中后自动写 Cookie。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if DESKTOP_LOCAL or path.startswith(ALLOWLIST_PREFIXES):
            return await call_next(request)

        url_token = request.query_params.get("token")
        cookie_token = request.cookies.get("web_token")

        if _token_ok(url_token):
            response = await call_next(request)
            # 把合法 token 写到 Cookie，后续访问无需带 ?token
            response.set_cookie(
                "web_token",
                url_token,
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                samesite="lax",
            )
            return response

        if _token_ok(cookie_token):
            return await call_next(request)

        # API 路径返回 401 JSON
        if path.startswith("/api/"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未授权，请用 ?token= 重新进入")

        # 页面路径跳到登录提示
        return RedirectResponse(url="/login")
