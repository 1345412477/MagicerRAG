"""H3/H4/H5 回归：SECRET_KEY 来源、RAG 不可信资料边界、CSP 兜底头。"""
from __future__ import annotations

from app import crypto
from app.service import rag_service


# ---- H3：加密密钥来源自检 ----
def test_key_source_in_allowed_set():
    assert crypto.key_source() in {"explicit", "embedding", "hardcoded"}
    assert isinstance(crypto.key_is_secure(), bool)


# ---- H4：检索资料被标记为“不可信数据”，抑制指令注入 ----
def test_build_messages_marks_context_untrusted():
    msgs = rag_service.build_messages("什么是权限？", "权限=RBAC 规则。", history=None)
    system = next(m["content"] for m in msgs if m["role"] == "system")
    user = next(m["content"] for m in msgs if m["role"] == "user")

    # 系统提示明确了资料不可信、忽略其中的指令
    assert "不可信" in system
    assert "指令" in system
    # 资料被边界标记包裹，与用户问题/系统设定明确分离
    assert "检索资料开始" in user and "检索资料结束" in user
    # 无资料时不应插入空标记
    empty = rag_service.build_messages("你好", "", history=None)
    empty_user = next(m["content"] for m in empty if m["role"] == "user")
    assert "检索资料开始" not in empty_user


# ---- H5：CSP 兜底头已注入，且 script-src 不含 unsafe-inline ----
def test_csp_header_present_and_script_src_strict(client):
    r = client.get("/")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy")
    assert csp, "缺少 Content-Security-Policy 头"
    assert "script-src 'self' https://unpkg.com" in csp
    script_directive = csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-inline'" not in script_directive, "script-src 不应含 unsafe-inline"
    assert "object-src 'none'" in csp