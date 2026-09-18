"""H1/H2：限流中间件的 IP 信任与认证防暴破回归测试。

注意：conftest 的 autouse 夹具会把 limits._RULES/_GENERIC 放宽到 100000/60，
因此本文件对“精确限值”的验证通过“实例级注入 + 独立 Counter”完成，
对“路由映射”的验证只断言规则键存在/被路由，不依赖被放宽的具体数值。
"""
from __future__ import annotations

import asyncio

import app.limits as limits
from app.limits import _client_ip, _pick_rule, _Counter, RateLimitMiddleware


async def _ok_app(_scope, _recv, _send):
    """放行时由中间件转发的真实下游应用：返回 200。"""
    await _send({"type": "http.response.start", "status": 200, "headers": []})
    await _send({"type": "http.response.body", "body": b""})


def _run_once(mw, path, *, client=("9.9.9.9", 1234), headers=()):
    """驱动一个 ASGI 中间件实例处理单次请求，返回响应状态码。"""
    captured: dict = {}

    async def send(msg: dict):
        if msg["type"] == "http.response.start":
            captured["status"] = msg["status"]

    scope = {
        "type": "http", "method": b"POST", "scheme": "http",
        "path": path, "query_string": b"", "http_version": "1.1",
        "headers": list(headers), "client": client,
    }

    async def runner():
        await mw.__call__(scope, lambda: None, send)

    asyncio.run(runner())
    return captured.get("status", 200)


# ---- H1：X-Forwarded-For 信任开关 ----
def test_client_ip_ignores_forwarded_when_untrusted():
    scope = {"headers": [(b"x-forwarded-for", b"1.1.1.1")], "client": ("9.9.9.9", 1)}
    # 默认不信任转发头 -> 取真实 peer，伪造 XFF 无效
    assert _client_ip(scope, trust_proxy=False) == "9.9.9.9"


def test_client_ip_uses_forwarded_when_trusted():
    scope = {"headers": [(b"x-forwarded-for", b"1.1.1.1, 2.2.2.2")], "client": ("9.9.9.9", 1)}
    assert _client_ip(scope, trust_proxy=True) == "1.1.1.1"


def test_client_ip_untrusted_ignores_spoofed_chain():
    # 即便带一串伪造转发头，非可信部署也只看 peer
    scope = {"headers": [(b"x-forwarded-for", b"6.6.6.6")], "client": ("8.8.8.8", 1)}
    assert _client_ip(scope, trust_proxy=False) == "8.8.8.8"


# ---- H2：认证路由存在独立限流规则 ----
def test_auth_has_dedicated_route_rule():
    # 规则键必须存在并可被路由（具体限值在模块定义处；此处不依赖被放宽的数值）
    assert "/api/auth/" in limits._RULES
    picked = _pick_rule("/api/auth/login")
    assert picked is not None and picked[0] == "/api/auth/"


def test_generic_and_ask_still_routed():
    assert _pick_rule("/api/auth/login")[0] == "/api/auth/"
    assert _pick_rule("/api/chats/ask")[0] == "/api/chats/ask"
    assert _pick_rule("/api/metrics")[0] == "generic"
    assert _pick_rule("/static/app.js") is None


# ---- 中间件：超限 429；未信任时伪造 XFF 绕不过 ----
def _tight_middleware(monkeypatch) -> RateLimitMiddleware:
    # 用实例级紧规则覆盖模块级（后者被 conftest 放宽），隔离验证限流动作本身
    monkeypatch.setattr(
        limits, "_pick_rule",
        lambda path: ("tight", limits._Rule(3, 60)) if path.startswith("/api/auth/") else None,
    )
    return RateLimitMiddleware(_ok_app, trust_proxy=False)


def test_middleware_returns_429_on_overflow(monkeypatch):
    mw = _tight_middleware(monkeypatch)
    # 同一客户端连续请求：前 3 次放行，第 4 次 429
    codes = [_run_once(mw, "/api/auth/login") for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_middleware_spoofed_xff_does_not_bypass_when_untrusted(monkeypatch):
    # 非可信部署：即便每次换一个伪造 X-Forwarded-For，仍按真实 peer 计数，绕不过
    mw = _tight_middleware(monkeypatch)
    codes = [
        _run_once(mw, "/api/auth/login",
                  headers=[(b"x-forwarded-for", f"1.1.1.{i}".encode())])
        for i in range(4)
    ]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


# ---- 计数器基础行为 ----
def test_counter_window_resets():
    c = _Counter()
    assert all(c.allow("k", 2, 60) for _ in range(2))
    assert not c.allow("k", 2, 60)      # 达到上限拒绝
    # 换 key 不受影响
    assert c.allow("other", 2, 60)