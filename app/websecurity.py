"""安全：密码哈希（PBKDF2-HMAC，stdlib）与令牌管理。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from . import db

_ITERATIONS = 200_000
_TOKEN_BYTES = 32
_TOKEN_TTL = 7 * 24 * 3600  # 令牌 7 天过期，过期需重新登录（小团队量级仍轻量）


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS
    ).hex()
    return hmac.compare_digest(test, digest)


# 令牌 -> (user_id, expiry)；内存缓存加速，DB 持久化保证重启不踢下线
_tokens: dict[str, tuple[int, float]] = {}


def _h(token: str) -> str:
    """令牌仅以 sha256 哈希形式入库，库泄露也不能直接冒用。"""
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(user_id: int) -> str:
    token = "tk_" + secrets.token_hex(_TOKEN_BYTES)
    exp = time.time() + _TOKEN_TTL
    db.add_token(_h(token), user_id, exp)
    _tokens[token] = (user_id, exp)
    return token


def resolve_token(token: str) -> int | None:
    rec = _tokens.get(token)
    if rec is None:
        # 内存未命中（如重启后）回源数据库
        row = db.get_token(_h(token))
        if row is None:
            return None
        user_id, expiry = row["user_id"], row["expires_at"]
        if time.time() > expiry:
            db.delete_token(_h(token))
            return None
        _tokens[token] = (user_id, expiry)
        return user_id
    user_id, expiry = rec
    if time.time() > expiry:
        _tokens.pop(token, None)
        db.delete_token(_h(token))
        return None
    return user_id


def revoke_token(token: str):
    _tokens.pop(token, None)
    db.delete_token(_h(token))