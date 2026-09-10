"""API 通用依赖：登录态校验、超管校验、数据集权限。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from config import ADMIN_USERNAME
from . import db, websecurity

SESSION_COOKIE = "mr_session"
CSRF_COOKIE = "mr_csrf"


def _is_admin(username: str) -> bool:
    return bool(ADMIN_USERNAME) and username == ADMIN_USERNAME


def extract_token(request: Request) -> str | None:
    """从 Authorization Bearer 或 httpOnly Cookie 中提取令牌（Cookie 优先于兼容无头）。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        tok = auth[7:].strip()
        if tok:
            return tok
    return request.cookies.get(SESSION_COOKIE)


def get_current_user(request: Request) -> dict:
    """从 Authorization:Bearer 或 httpOnly Cookie 解析当前用户（含是否超管/启用）。"""
    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    user_id = websecurity.resolve_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    user = db.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="账号已被停用")
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": _is_admin(user["username"]),
    }


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return user


def require_dataset_owner(dataset_id: int, user: dict = Depends(get_current_user)) -> None:
    """校验当前用户是某数据集 Owner（超管视为任意库 Owner）。"""
    if user["is_admin"]:
        return
    role = db.get_member_role(user["id"], dataset_id)
    if role != "owner":
        raise HTTPException(status_code=403, detail="需要该知识库的所有者权限")


def require_dataset_manager(dataset_id: int, user: dict = Depends(get_current_user)) -> None:
    """校验当前用户可管理内容（Owner 或 Manager；超管总是放行）。"""
    if user["is_admin"]:
        return
    role = db.get_member_role(user["id"], dataset_id)
    if role not in {"owner", "manager"}:
        raise HTTPException(status_code=403, detail="需要该知识库的管理权限（可增删内容）")


def require_dataset_access(dataset_id: int, user: dict = Depends(get_current_user)) -> None:
    """校验当前用户对该数据集有访问权（个人 Owner 或团队 Member；超管总是放行）。"""
    if user["is_admin"]:
        return
    if db.get_member_role(user["id"], dataset_id) is None:
        raise HTTPException(status_code=403, detail="无该知识库的访问权限")
