"""数据层单元测试：用户 / 数据集 / 会话历史 / 数据集参数 / 分享 / 审计。"""
from __future__ import annotations

import app.db as db


def test_personal_dataset_created_once():
    uid = db.create_user("u1", "x")
    d1 = db.ensure_personal_dataset(uid)
    d2 = db.ensure_personal_dataset(uid)
    assert d1 == d2  # 幂等
    assert db.get_dataset(d1)["type"] == "personal"


def test_recent_messages_returns_chronological_and_excludes_empty():
    uid = db.create_user("u2", "x")
    sid = db.create_session(uid, "s")
    db.add_message(sid, "user", "第一问")
    db.add_message(sid, "assistant", "第一答")
    db.add_message(sid, "user", "第二问")
    db.add_message(sid, "assistant", "")  # 空占位应被排除
    msgs = db.list_recent_messages(sid, limit=10)
    assert [m["content"] for m in msgs] == ["第一问", "第一答", "第二问"]


def test_dataset_params_set_and_get():
    uid = db.create_user("u3", "x")
    did = db.ensure_personal_dataset(uid)
    db.set_dataset_params(did, score_threshold=0.7, top_k=6)
    dp = db.get_dataset_params(did)
    assert dp["score_threshold"] == 0.7
    assert dp["top_k"] == 6


def test_share_flow_and_audit():
    uid = db.create_user("u4", "x")
    db.add_audit(uid, "u4", "share.create", "session:1", "x")
    rows = db.list_audit(10)
    assert rows[0]["action"] == "share.create"
    db.add_share(1, "hash1", 123456.0)
    assert db.get_share_by_token("hash1")["session_id"] == 1
    db.revoke_share(db.get_share_by_token("hash1")["id"])
    assert db.get_share_by_token("hash1") is None
