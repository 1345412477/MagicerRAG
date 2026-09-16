"""鉴权路由：注册（邀请码，超管免码）/ 登录 / 超管用户管理 / 邀请码管理。"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from config import ADMIN_USERNAME, INVITE_CODE
from .. import db, websecurity
from ..deps import CSRF_COOKIE, SESSION_COOKIE, extract_token, get_current_user, require_admin
from ..schemas import LoginIn, RegisterIn, SetInviteCode, SetUserActive
from ..service import kb_service

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_AGE = 7 * 24 * 3600


def _current_invite_code() -> str:
    return db.get_setting("invite_code") or INVITE_CODE


def _set_session_cookie(response: Response, request: Request, token: str):
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=_COOKIE_AGE,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        path="/",
    )


def _set_csrf_cookie(response: Response, request: Request):
    """双提交 CSRF 令牌：非 HttpOnly（前端可读），带回请求头比对。"""
    response.set_cookie(
        key=CSRF_COOKIE,
        value=secrets.token_hex(16),
        max_age=_COOKIE_AGE,
        httponly=False,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        path="/",
    )


def _clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "is_active": user["is_active"],
        "is_admin": bool(ADMIN_USERNAME) and user["username"] == ADMIN_USERNAME,
    }


@router.post("/register")
def register(body: RegisterIn, response: Response, request: Request):
    if body.username == ADMIN_USERNAME:
        # 超管账号：免邀请码播种，但仅允许一次；已存在则拒绝同名注册，防止被抢先注册为超管。
        if db.get_user_by_name(ADMIN_USERNAME):
            raise HTTPException(status_code=409, detail="超管账号已存在，不可重复注册")
    elif not body.invite_code or body.invite_code != _current_invite_code():
        raise HTTPException(status_code=403, detail="邀请码不正确")
    if db.get_user_by_name(body.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user_id = db.create_user(body.username, websecurity.hash_password(body.password))
    db.ensure_personal_dataset(user_id)
    row = db.get_user(user_id)
    token = websecurity.issue_token(user_id)
    _set_session_cookie(response, request, token)
    _set_csrf_cookie(response, request)
    return {"token": token, "user": _public_user(row)}


@router.post("/login")
def login(body: LoginIn, response: Response, request: Request):
    user = db.get_user_by_name(body.username)
    if not user or not websecurity.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="账号已被停用")
    db.ensure_personal_dataset(user["id"])
    token = websecurity.issue_token(user["id"])
    _set_session_cookie(response, request, token)
    _set_csrf_cookie(response, request)
    return {"token": token, "user": _public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response, user: dict = Depends(get_current_user)):
    token = extract_token(request)
    if token:
        websecurity.revoke_token(token)
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(request: Request, response: Response, user: dict = Depends(get_current_user)):
    # 兼容既有 Cookie 会话：若已登录但缺 CSRF Cookie，则补发，避免后续变更请求 403
    if request.cookies.get(SESSION_COOKIE) and not request.cookies.get(CSRF_COOKIE):
        _set_csrf_cookie(response, request)
    return {"user": {"id": user["id"], "username": user["username"], "is_admin": user["is_admin"]}}


# ---------- 超管：用户管理 ----------
@router.get("/admin/users")
def admin_list_users(admin: dict = Depends(require_admin)):
    return [_public_user(u) for u in db.list_users()]


@router.patch("/admin/users/{user_id}/active")
def admin_set_active(user_id: int, body: SetUserActive, admin: dict = Depends(require_admin)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user["username"] == ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="不能停用超级管理员")
    db.set_user_active(user_id, body.is_active)
    db.add_audit(user["id"], user["username"], "user.set_active", f"user:{user_id}", f"active={body.is_active}")
    return {"ok": True}


def _cleanup_user_data(user_id: int):
    """删除用户：级联清理数据库记录 + 遗留磁盘/向量数据。"""
    result = db.delete_user(user_id)
    if result.get("personal_id"):
        kb_service.cleanup_dataset(result["personal_id"])
    for tid in result.get("team_cleanup_ids", []):
        kb_service.cleanup_dataset(tid)


@router.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user["username"] == ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="不能删除超级管理员")
    _cleanup_user_data(user_id)
    db.add_audit(user["id"], user["username"], "user.delete", f"user:{user_id}", user["username"])
    return {"ok": True}
@router.get("/invite-code")
def get_invite_code(user: dict = Depends(get_current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return {"invite_code": _current_invite_code()}


@router.put("/invite-code")
def set_invite_code(body: SetInviteCode, admin: dict = Depends(require_admin)):
    global INVITE_CODE
    INVITE_CODE = body.invite_code
    db.set_setting("invite_code", body.invite_code)
    db.add_audit(admin["id"], admin["username"], "invite_code.set", "", "invite_code 已更新")
    return {"invite_code": body.invite_code}
