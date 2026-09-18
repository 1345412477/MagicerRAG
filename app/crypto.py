"""密钥/敏感字段加密（R-11）：模型 API Key 以密文落库，避免库泄露直接冒用。

采用 Fernet（AES-128-CBC + HMAC），密钥来自 SECRET_KEY（未配置则从 EMBEDDING_API_KEY
推导，保证开箱即用）。对旧明文数据兼容：不以 Fernet 前缀开头的按原文返回。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from config import EMBEDDING_API_KEY, SECRET_KEY

_fernet: Fernet | None = None
_PREFIX = "gAAAAA"  # Fernet token 固定前缀，用于识别密文


def key_source() -> str:
    """当前加密密钥材料来源：
    explicit=显式 SECRET_KEY（推荐）｜embedding=回退用 EMBEDDING_API_KEY｜hardcoded=内置兜底（不安全）。
    """
    from config import EMBEDDING_API_KEY as _eak, SECRET_KEY as _sk

    if _sk:
        return "explicit"
    if _eak:
        return "embedding"
    return "hardcoded"


def key_is_secure() -> bool:
    return key_source() == "explicit"


def _derive_key() -> bytes:
    material = SECRET_KEY or EMBEDDING_API_KEY or "magicerrag-insecure-default"
    return base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())


def _get() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_key())
    return _fernet


def is_encrypted(text: str) -> bool:
    return bool(text) and text.startswith(_PREFIX)


def encrypt(text: str) -> str:
    if not text:
        return text
    return _get().encrypt(text.encode()).decode()


def decrypt(text: str) -> str:
    if not text:
        return text
    if not is_encrypted(text):
        return text  # 旧明文数据兼容
    try:
        return _get().decrypt(text.encode()).decode()
    except Exception:  # noqa: BLE001 密钥变更或损坏时回退原文，避免阻断
        return text
