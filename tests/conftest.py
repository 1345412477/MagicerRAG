"""pytest 公共夹具：隔离存储（临时 SQLite / upload / vectordb）、放宽限流、提供 TestClient。"""
from __future__ import annotations

import pytest

import app.db as db
import config
import rag.retriever as retriever
import app.limits as limits
import app.service.kb_service as kb_service


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """每个测试用独立存储，且重置单例/连接，避免污染真实数据与跨用例污染。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "test.db"
    upload_dir = tmp_path / "uploads"
    vector_dir = tmp_path / "vectordb"

    monkeypatch.setattr(db, "DB_PATH", db_path)
    # 关闭 / 清除线程本地连接，强制下次重连到新库
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        del db._local.conn

    monkeypatch.setattr(config, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(kb_service, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(config, "VECTOR_DIR", vector_dir)
    monkeypatch.setattr(retriever, "VECTOR_DIR", vector_dir)
    retriever._store = None
    retriever._embeddings = None

    # 放宽限流：测试里会密集注册 / 登录，避免误触 429
    monkeypatch.setattr(
        limits,
        "_RULES",
        {k: limits._Rule(100000, 60) for k in limits._RULES.keys()},
    )
    monkeypatch.setattr(limits, "_GENERIC", limits._Rule(100000, 60))
    return {"db_path": db_path, "upload_dir": upload_dir, "vector_dir": vector_dir}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def registered_user(client):
    """注册一个普通用户（邀请码 magicer-demo）。"""
    r = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "secret123", "invite_code": "magicer-demo"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return {"token": data["token"], "user": data["user"], "headers": {"Authorization": f"Bearer {data['token']}"}}
