"""轻量限流中间件：分接口按客户端 IP 计数，超限返回 429。

实现为原生 ASGI 中间件而非 BaseHTTPMiddleware，避免缓冲 response body 破坏 /api/chats/ask 的 SSE 流式输出。
纯内存计数（单进程生效），数据量级（10 用户）下无持久化需求。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class _Rule:
    limit: int
    window: int  # 秒


# 路径前缀 -> 规则。
#   sensitive: 登录/注册等未认证敏感操作，防暴破
#   generate : 调用大模型生成，限制并发控成本
#   generic  : 其余 /api 接口的兜底
_RULES: dict[str, _Rule] = {
    # 认证相关（登录/注册等未认证敏感操作）：独立限流防暴破与撞库
    "/api/auth/": _Rule(10, 60),
    # 仅对真正触发大模型生成的 /api/chats/ask 限流；其余会话浏览类接口走通用兜底
    "/api/chats/ask": _Rule(20, 60),
}
_GENERIC = _Rule(600, 60)


class _Counter:
    """固定窗口计数器；窗口内超过 limit 则拒绝。"""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                q = deque()
                self._hits[key] = q
                q.append(now)
                return True
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            # 防止 key 集合无限增长，清理已过期的空窗口
            if len(self._hits) > 10000:
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True


def _pick_rule(path: str) -> tuple[str, _Rule] | None:
    for prefix, rule in _RULES.items():
        if path.startswith(prefix):
            return prefix, rule
    if path.startswith("/api/"):
        return "generic", _GENERIC
    return None


def _client_ip(scope: dict, trust_proxy: bool = False) -> str:
    """解析客户端 IP。

    trust_proxy=False（默认，直连部署）：只信任 socket peer，不读任何可伪造的转发头，
    防止攻击者用 X-Forwarded-For 伪造 IP 绕过限流。
    trust_proxy=True（仅用于可信反代之后）：取 X-Forwarded-For 最左（原始客户端）IP，
    避免所有请求都命中代理 IP 导致限流失真。调用方须保证反代会覆盖/剥离客户端伪造值。
    """
    if trust_proxy:
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-for":
                first = v.decode("latin-1").split(",")[0].strip()
                if first:
                    return first
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    def __init__(self, app, trust_proxy: bool = False):
        self.app = app
        self.trust_proxy = trust_proxy
        self._counter = _Counter()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        picked = _pick_rule(path)
        if picked:
            prefix, rule = picked
            ip = _client_ip(scope, self.trust_proxy)
            if not self._counter.allow(f"{ip}:{prefix}", rule.limit, rule.window):
                body = '{"detail":"请求过于频繁，请稍后再试"}'.encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)
