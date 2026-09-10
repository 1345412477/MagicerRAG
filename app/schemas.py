"""API 请求/响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    invite_code: str | None = None


class LoginIn(BaseModel):
    username: str
    password: str


class SessionCreate(BaseModel):
    title: str = "新会话"
    dataset_id: int | None = None  # N8: 新建会话时绑定知识库；None=不限（全部有权限库）


class SessionRename(BaseModel):
    title: str


class AskIn(BaseModel):
    session_id: int | None = None
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = 4
    dataset_ids: list[int] | None = None  # 要检索的数据集；None = 全部有权限的


class ShareCreate(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


# —— 知识库 / 数据集 ——
class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class DatasetRename(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class DatasetParamsUpdate(BaseModel):
    score_threshold: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    split_mode: str | None = Field(default=None, pattern="^(markdown|recursive|whole)$")
    chunk_size: int | None = Field(default=None, ge=64, le=2000)
    overlap: int | None = Field(default=None, ge=0, le=400)


class MemberAdd(BaseModel):
    username: str
    role: str = "member"  # owner | manager | member


class DeleteFiles(BaseModel):
    """批量删除文件：文件主键列表。"""
    ids: list[int] = Field(min_length=1)


class MemberRoleUpdate(BaseModel):
    role: str  # owner | manager | member


# —— 超管 ——
class SetUserActive(BaseModel):
    is_active: bool


class SetInviteCode(BaseModel):
    invite_code: str = Field(min_length=1, max_length=64)
