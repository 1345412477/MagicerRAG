"""API 端到端（TestClient）：认证 / 越权 / 导出 / 分享 / 审计。检索与大模型调用被 mock。"""
from __future__ import annotations

import pytest
import time

from app.service import kb_service
from app.service import rag_service


def _mock_ask(monkeypatch):
    async def fake_stream(question, context, history=None):
        yield "模拟答案"

    monkeypatch.setattr(rag_service, "retrieve_hits", lambda *a, **k: [
        {"content": "区块内容", "score": 0.9, "source": "demo.md"}
    ])
    monkeypatch.setattr(rag_service, "stream_answer_async", fake_stream)


def test_register_requires_correct_invite(client):
    assert client.post("/api/auth/register", json={"username": "bob", "password": "secret123", "invite_code": "wrong"}).status_code == 403
    r = client.post("/api/auth/register", json={"username": "bob", "password": "secret123", "invite_code": "magicer-demo"})
    assert r.status_code == 200
    assert r.json()["user"]["is_admin"] is False


def test_ask_requires_auth(client):
    assert client.post("/api/chats/ask", json={"question": "hi"}).status_code == 401


def test_ask_streams_and_history(client, registered_user, monkeypatch):
    _mock_ask(monkeypatch)
    h = registered_user["headers"]
    # 新建会话并提问（会话懒创建走 ask）
    r = client.post("/api/chats/ask", json={"question": "第一个问题", "dataset_ids": []}, headers=h)
    assert r.status_code == 200
    assert "event: retrieval" in r.text
    assert "模拟答案" in r.text


def test_admin_audit_requires_admin(client, registered_user):
    h = registered_user["headers"]
    assert client.get("/api/admin/audit", headers=h).status_code == 403


def test_admin_register_is_admin(client):
    r = client.post("/api/auth/register", json={"username": "admin", "password": "secret123"})
    assert r.status_code == 200
    assert r.json()["user"]["is_admin"] is True
    ah = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/admin/audit", headers=ah).status_code == 200


def test_admin_register_duplicate_rejected(client):
    """超管只能播种一次：首次成功，重复注册同名 admin 应被拒绝，防抢先后门。"""
    r1 = client.post("/api/auth/register", json={"username": "admin", "password": "secret123"})
    assert r1.status_code == 200 and r1.json()["user"]["is_admin"] is True
    r2 = client.post("/api/auth/register", json={"username": "admin", "password": "other123"})
    assert r2.status_code == 409


def test_export_session_only_owner(client, registered_user):
    h = registered_user["headers"]
    sid = client.post("/api/chats/sessions", json={"title": "t"}, headers=h).json()["id"]
    # 他人无权限导出
    other = client.post("/api/auth/register", json={"username": "carol", "password": "secret123", "invite_code": "magicer-demo"}).json()
    assert client.get(f"/api/chats/sessions/{sid}/export", headers={"Authorization": f"Bearer {other['token']}"}).status_code == 404
    # 本人可导出
    r = client.get(f"/api/chats/sessions/{sid}/export", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")


def test_share_create_and_view(client, registered_user, monkeypatch):
    h = registered_user["headers"]
    _mock_ask(monkeypatch)
    sid = client.post("/api/chats/sessions", json={"title": "shared"}, headers=h).json()["id"]
    r = client.post(f"/api/chats/sessions/{sid}/share", json={"days": 7}, headers=h)
    assert r.status_code == 200
    token = r.json()["token"]
    # 公开只读查看（无需登录）
    v = client.get(f"/api/chats/shares/{token}")
    assert v.status_code == 200
    assert v.json()["title"] == "shared"


def test_share_landing_page_renders(client, registered_user, monkeypatch):
    """分享落地页 /share/{token} 应渲染只读 HTML，不再返回无效链接。"""
    h = registered_user["headers"]
    _mock_ask(monkeypatch)
    # 通过 ask 建会话并写入 user + assistant 消息
    r = client.post("/api/chats/ask", json={"question": "你好", "dataset_ids": []}, headers=h)
    assert r.status_code == 200
    # 取该用户最新会话并分享
    sid = client.get("/api/chats/sessions", headers=h).json()[0]["id"]
    token = client.post(f"/api/chats/sessions/{sid}/share", json={"days": 7}, headers=h).json()["token"]
    page = client.get(f"/share/{token}")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "你好" in page.text          # 用户消息
    assert "模拟答案" in page.text      # 助手消息
    # 无效 token -> 404
    assert client.get("/share/not-a-real-token").status_code == 404


def test_rebuild_returns_task(client, registered_user, monkeypatch):
    monkeypatch.setattr(kb_service, "rebuild_index", lambda ds, **k: 3)
    h = registered_user["headers"]
    ds = client.get("/api/kb/datasets", headers=h).json()
    did = ds[0]["id"]
    r = client.post(f"/api/kb/datasets/{did}/rebuild", headers=h)
    assert r.status_code == 200
    tid = r.json()["task_id"]
    st = None
    for _ in range(30):
        st = client.get(f"/api/kb/tasks/{tid}", headers=h).json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert st is not None and st["status"] == "done"


def test_dataset_params_set_and_read(client, registered_user):
    h = registered_user["headers"]
    ds = client.get("/api/kb/datasets", headers=h).json()
    did = ds[0]["id"]
    r = client.put(f"/api/kb/datasets/{did}/params", headers=h, json={"score_threshold": 0.7, "top_k": 6})
    assert r.status_code == 200
    g = client.get(f"/api/kb/datasets/{did}/params", headers=h).json()
    assert g["score_threshold"] == 0.7 and g["top_k"] == 6


def test_admin_update_params_persists(client):
    r = client.post("/api/auth/register", json={"username": "admin", "password": "secret123"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    body = {"history_turns": 8, "context_token_budget": 6000, "query_rewrite": True, "rerank_enabled": True, "max_context_hits": 5}
    up = client.put("/api/admin/model", headers=h, json=body)
    assert up.status_code == 200
    assert up.json()["params"]["history_turns"] == "8"
    assert up.json()["params"]["query_rewrite"] == "1"
    gm = client.get("/api/admin/model", headers=h).json()
    assert gm["params"]["rerank_enabled"] == "1"


def test_http_only_cookie_session(client, registered_user):
    # 注册返回的 Cookie 应带 HttpOnly，且后续无 Authorization 头也能通过 Cookie 鉴权
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    r2 = client.post("/api/auth/logout")
    assert r2.status_code == 200
    # 登出后 Cookie 已清除 -> /me 返回未登录
    assert client.get("/api/auth/me").status_code == 401


def test_login_sets_http_only_cookie(client):
    client.post("/api/auth/register", json={"username": "bob2", "password": "secret123", "invite_code": "magicer-demo"})
    r = client.post("/api/auth/login", json={"username": "bob2", "password": "secret123"})
    set_cookie = r.headers.get("set-cookie", "")
    assert "mr_session=" in set_cookie
    assert "HttpOnly" in set_cookie


def test_embedding_status_and_rebuild_all(client, monkeypatch):
    monkeypatch.setattr(kb_service, "rebuild_index", lambda ds, **k: 0)
    r = client.post("/api/auth/register", json={"username": "admin", "password": "secret123"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    es = client.get("/api/admin/embedding-status", headers=h)
    assert es.status_code == 200
    assert "current" in es.json() and "needs_rebuild" in es.json()
    t = client.post("/api/admin/rebuild-all", headers=h, json={})
    assert t.status_code == 200
    tid = t.json()["task_id"]
    last = None
    for _ in range(30):
        last = client.get(f"/api/kb/tasks/{tid}", headers=h).json()
        if last["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert last is not None and last["status"] == "done"


def test_csrf_blocks_cookie_mutation_without_token(client, registered_user):
    # 注册后客户端已带 Cookie 会话；无 CSRF 头的变更请求应被拦截
    affected = client.post("/api/chats/sessions", json={"title": "t1"})
    assert affected.status_code == 403
    csrf = client.cookies.get("mr_csrf")
    assert csrf
    ok = client.post("/api/chats/sessions", json={"title": "t2"}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200
