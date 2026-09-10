"""R-11：模型 / 敏感字段加密往返与密文落库校验。"""
from __future__ import annotations

from app import crypto, db
from app import runtime_settings
from app.routers import admin as admin_router


def test_encrypt_decrypt_roundtrip():
    plain = "sk-test-abc123"
    enc = crypto.encrypt(plain)
    assert enc != plain
    assert crypto.is_encrypted(enc)
    assert crypto.decrypt(enc) == plain


def test_legacy_plaintext_passthrough():
    assert crypto.decrypt("sk-plain-old") == "sk-plain-old"


def test_model_key_stored_as_ciphertext_and_decrypts():
    uid = db.create_user("mcry", "x")
    # 直接走 db 层：真实存储的是加密后的值
    db.add_model_config("Demo", "p", "m", "https://x/v1", crypto.encrypt("sk-mykey"), True)
    row = db.get_active_model_config()
    assert row is not None
    assert row["api_key"].startswith("gAAAAA")
    # runtime 读取时是明文
    rt = runtime_settings.model_settings()
    assert rt["llm_api_key"] == "sk-mykey"
