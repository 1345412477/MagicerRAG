"""MagicerRAG 网页工作台 —— FastAPI 入口。

本地运行：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import hashlib
import logging
import re
import uuid

from config import PORT, TRUST_PROXY_HEADERS
from . import crypto, sharepage
from .deps import CSRF_COOKIE, SESSION_COOKIE
from .limits import RateLimitMiddleware
from .routers import admin, auth, chats, kb

logger = logging.getLogger("magicerrag")

if not crypto.key_is_secure():
    logger.warning(
        "SECRET_KEY 未显式配置（当前加密密钥源=%s）。生产环境请设置独立高熵 SECRET_KEY，"
        "否则落库的模型 API Key 加密强度取决于 EMBEDDING_API_KEY/内置兜底值。",
        crypto.key_source(),
    )

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MagicerRAG", version="1.0.0")

# 限流需最先拦截，避免未认证请求消耗后续依赖校验逻辑；
# trust_proxy 仅在确定处于可信反代之后时置 1，否则直连部署将信任可伪造的转发头。
app.add_middleware(RateLimitMiddleware, trust_proxy=TRUST_PROXY_HEADERS)


@app.middleware("http")
async def security_and_trace(request: Request, call_next):
    """安全响应头 + 请求 ID（R-11/R-12）+ CSRF 双提交校验（R-11）。"""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    # CSRF：仅对「Cookie 会话 + /api 变更请求」校验双提交令牌；
    # Bearer 头请求天然抗 CSRF，auth 接口（登录/注册/登出）豁免，登录前无会话 Cookie 也天然放行。
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        p = request.url.path
        if p.startswith("/api/") and not p.startswith("/api/auth/"):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") and request.cookies.get(SESSION_COOKIE):
                csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
                csrf_header = request.headers.get("X-CSRF-Token", "")
                if not csrf_cookie or csrf_header != csrf_cookie:
                    return JSONResponse(
                        {"detail": "CSRF 校验失败"},
                        status_code=403,
                        headers={"X-Request-ID": request_id},
                    )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    # 纵深防御：XSS 第一道防线是 DOMPurify，CSP 作为兜底。
    # script-src 不含 'unsafe-inline'（前端无内联脚本/处理器），可阻断注入脚本执行；
    # style-src 保留 unsafe-inline 以兼容 markdown 内联样式；img 放行 https 以支持文档外部图片。
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'",
    )
    response.headers.setdefault("X-Request-ID", request_id)
    return response

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(chats.router)
app.include_router(kb.router)


@app.get("/api/health")
def health():
    return {"ok": True, "app": "magicerrag"}


# 静态前端（需在 API 路由之后挂载，避免吞掉 /api 前缀）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _static_version(rel: str) -> str:
    p = STATIC_DIR / rel
    if not p.exists():
        return "0"
    return hashlib.md5(p.read_bytes()).hexdigest()[:8]


@app.get("/")
def index():
    # 版本号由静态资源内容哈希自动推导：内容变化才换新，避免手动累加过快的 ?vN
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css_v = _static_version("style.css")
    js_v = _static_version("app.js")
    html = re.sub("style\\.css(?:\\?v=[^\"\\s]*)?", f"style.css?v={css_v}", html)
    html = re.sub("app\\.js(?:\\?v=[^\"\\s]*)?", f"app.js?v={js_v}", html)
    return HTMLResponse(html)


@app.get("/share/{token}")
def share_page(token: str):
    """分享落地页：复用 /api/chats/shares 的公开读取逻辑，渲染只读 HTML。"""
    data = chats.view_share(token)
    return HTMLResponse(sharepage.render(data["title"], data["messages"]))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
