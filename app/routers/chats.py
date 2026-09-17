"""会话与问答路由：多会话 CRUD + SSE 流式问答。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from .. import db, runtime_settings
from ..deps import get_current_user
from ..schemas import AskIn, SessionCreate, SessionRename, ShareCreate
from ..service import rag_service

logger = logging.getLogger("magicerrag")

# 对话内随消息上传的图片：存到静态目录下，直接以 /static/... 提供
_CHAT_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "chat-uploads"
_CHAT_ALLOWED = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_CHAT_MAX = 8 * 1024 * 1024  # 8MB


router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    ext = (Path(file.filename or "").suffix or "").lower()
    if ext not in _CHAT_ALLOWED:
        raise HTTPException(status_code=400, detail="仅支持 PNG/JPG/JPEG/WEBP/GIF 图片")
    content = await file.read()
    if not content or len(content) > _CHAT_MAX:
        raise HTTPException(status_code=413, detail="图片需在 8MB 以内")
    _CHAT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}{ext}"
    (_CHAT_UPLOAD_DIR / name).write_bytes(content)
    return {"url": f"/static/chat-uploads/{name}"}


def _session_out(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "dataset_id": row["dataset_id"],  # N8: 会话绑定知识库，供前端按知识库分组
        "created_at": row["created_at"],
    }


def _ensure_session_quota(user: dict) -> None:
    """校验用户会话配额；超出返回 429（请求过多语义），防止会话无限膨胀。"""
    max_sessions = db.get_user(user["id"])["max_sessions"]
    if db.count_sessions(user["id"]) >= max_sessions:
        raise HTTPException(status_code=429, detail="会话数已达上限，请删除部分会话后再新建")


@router.get("/sessions")
def list_sessions(user: dict = Depends(get_current_user)):
    return [_session_out(s) for s in db.list_sessions(user["id"])]


@router.post("/sessions")
def create_session(body: SessionCreate, user: dict = Depends(get_current_user)):
    _ensure_session_quota(user)
    # N8：新建会话时选择知识库 —— 绑定该库；None=不限（全部有权限库）
    dataset_id = _validate_bind_dataset(body.dataset_id, user)
    sid = db.create_session(user["id"], body.title, dataset_id)
    return _session_out(db.get_session(sid, user["id"]))


def _validate_bind_dataset(dataset_id: int | None, user: dict) -> int | None:
    """N8：校验会话要绑定的知识库属于用户可访问集合；None 表示不限（返回 None）。"""
    if dataset_id is None:
        return None
    accessible = set(db.list_accessible_dataset_ids(user["id"], user["is_admin"]))
    if dataset_id not in accessible:
        raise HTTPException(status_code=403, detail="无可访问的知识库，无法绑定")
    return dataset_id


def _resolve_session_datasets(session_dataset_id: int | None, requested: list[int] | None, user: dict) -> list[int]:
    """N8：续聊沿用会话绑定库，不再由前端选择；未绑定会话则回退到请求/全部有权限库。

    返回最终用于检索的 dataset_id 列表。若会话绑定了单库，强制用单库；
    否则（未绑定）按历史语义：有请求用请求，无则全部有权限。
    """
    if session_dataset_id is not None:
        return [session_dataset_id]
    accessible = set(db.list_accessible_dataset_ids(user["id"], user["is_admin"]))
    if requested:
        bad = [d for d in requested if d not in accessible]
        if bad:
            raise HTTPException(status_code=403, detail="包含无可访问的知识库")
        return list(requested)
    return list(accessible)


@router.patch("/sessions/{sid}")
def rename_session(sid: int, body: SessionRename, user: dict = Depends(get_current_user)):
    if not db.get_session(sid, user["id"]):
        raise HTTPException(status_code=404, detail="会话不存在")
    db.rename_session(sid, user["id"], body.title)
    return {"ok": True}


@router.delete("/sessions/{sid}")
def delete_session(sid: int, user: dict = Depends(get_current_user)):
    if not db.delete_session(sid, user["id"]):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.get("/sessions/{sid}/messages")
def get_messages(sid: int, user: dict = Depends(get_current_user)):
    if not db.get_session(sid, user["id"]):
        raise HTTPException(status_code=404, detail="会话不存在")
    return db.list_messages(sid)


@router.post("/ask")
async def ask(body: AskIn, user: dict = Depends(get_current_user)):
    """SSE 流式问答：先发 retrieval 事件（召回片段），再逐个发 delta，最后发 done。"""
    # 新建会话（未指定时）：绑定单一选择的库，None=不限
    if body.session_id is None:
        _ensure_session_quota(user)
        bind_ds = _validate_bind_dataset(body.dataset_ids[0] if len(body.dataset_ids or []) == 1 else None, user)
        sd = db.create_session(user["id"], body.question[:20], bind_ds)
        body.session_id = sd
    elif not db.get_session(body.session_id, user["id"]):
        raise HTTPException(status_code=404, detail="会话不存在")

    # N8：续聊沿用会话绑定库，新建会话时已绑定；未绑定库回退到请求/全部有权限库
    sess = db.get_session(body.session_id, user["id"])
    if body.dataset_ids:
        # 新建会话（body.session_id 刚创建完毕）时接收前端选库
        requested = body.dataset_ids
    else:
        requested = None
    dataset_ids = _resolve_session_datasets(sess["dataset_id"] if sess else None, requested, user)

    sid = body.session_id

    # R-01：取最近历史用于多轮上下文（在当前用户消息落库前，避免重复注入当前问题）
    rt = runtime_settings.load()
    history = db.list_recent_messages(sid, limit=rt["history_turns"] * 2)
    # R-02：基于历史改写为独立问题用于检索；生成仍用原问题 + 历史
    retrieval_question = rag_service.rewrite_question(body.question, history if rt["query_rewrite"] else None)

    # 保存用户消息
    db.add_message(sid, "user", body.question, images=body.images)

    async def event_stream():
        # 当前模型不支持看图时，直接回提示，避免多模态请求报错
        if body.images and not rag_service.supports_vision(rt["model"]):
            yield _sse("stage", {"stage": "error"})
            yield _sse("delta", {"text": "⚠️ 当前模型暂不支持图片识别，请在「模型管理」中切换为支持视觉的模型（如 gpt-4o / qwen-vl / glm-4v 等）后再发送图片。"})
            yield _sse("done", {"message_id": None, "answer": ""})
            return
        # W2：检索移入流内，前端阶段条即时反馈「检索中」，不再等检索完成后才开始 SSE
        yield _sse("stage", {"stage": "retrieve"})
        try:
            hits = await asyncio.to_thread(
                rag_service.retrieve_hits, retrieval_question, body.top_k, dataset_ids
            )
        except Exception:  # noqa: BLE001 流内错误用 SSE 回显，避免流中途无响应
            logger.exception("检索失败（session=%s）", sid)
            yield _sse("stage", {"stage": "error"})
            yield _sse("delta", {"text": "\n\n[知识库检索失败，请稍后重试]"})
            yield _sse("done", {"message_id": None, "answer": ""})
            return
        # R-05：进入 prompt 的资料按 token 预算裁剪（前端来源卡仍展示全量 hits）
        ctx_hits, _truncated = rag_service.fit_context(hits, rt["context_token_budget"])
        context = rag_service.build_context(ctx_hits) if ctx_hits else ""
        msg_id = db.add_message(sid, "assistant", "", hits)
        yield _sse("retrieval", {"hits": hits})
        yield _sse("stage", {"stage": "generate"})
        parts: list[str] = []
        reasons: list[str] = []
        try:
            async for reason, delta in rag_service.stream_answer_async(
                body.question, context, history, images=body.images
            ):
                if reason:
                    reasons.append(reason)
                    yield _sse("reasoning", {"text": reason})
                if delta:
                    parts.append(delta)
                    yield _sse("delta", {"text": delta})
        except Exception:  # noqa: BLE001 仅对外回显通用文案，细节落服务端日志
            logger.exception("生成中断（session=%s）", sid)
            note = "\n\n[生成中断，请稍后重试]"
            parts.append(note)
            yield _sse("delta", {"text": note})
        full = "".join(parts)
        thinking = "".join(reasons)
        db.update_message_content(msg_id, full, thinking)
        # W1：回答生成后基于回答+命中资料生成推荐追问（开关开启且命中有意义才做；失败回退兜底）
        if rt["suggest_questions"] and hits:
            try:
                qs = await rag_service.recommend_questions_async(body.question, full, hits)
                if qs:
                    yield _sse("suggest", {"questions": qs})
            except Exception:  # noqa: BLE001 推荐失败不影响主回答
                logger.warning("推荐追问生成失败（session=%s）", sid)
        yield _sse("done", {"message_id": msg_id, "answer": full})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- 会话导出 / 只读分享（R-09） ----------
@router.get("/sessions/{sid}/export")
def export_session(sid: int, user: dict = Depends(get_current_user)):
    """导出会话为 Markdown（含问答与来源引用），仅会话所属用户可导出。"""
    sess = db.get_session(sid, user["id"])
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    msgs = db.list_messages(sid)
    lines = [f"# {sess['title']}", f"导出时间：{db.utcnow()}", ""]
    for m in msgs:
        role = "🧑 用户" if m["role"] == "user" else "🤖 助手"
        lines.append(f"## {role}\n\n{m['content']}\n")
        if m["role"] == "assistant" and m.get("hits"):
            lines.append("**参考资料：**\n")
            for h in m["hits"]:
                lines.append(f"- {h.get('source', '未知来源')}（相关度 {h.get('score')}）")
            lines.append("")
    body = "\n".join(lines)
    safe_title = "".join(c for c in sess["title"] if c not in '\\/:*?"<>|') or "chat"
    return PlainTextResponse(
        body,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_title}.md"},
    )


@router.post("/sessions/{sid}/share")
def create_share(sid: int, body: ShareCreate, user: dict = Depends(get_current_user)):
    """生成只读分享链接（token 仅存哈希），默认 7 天有效。记录审计。"""
    sess = db.get_session(sid, user["id"])
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    token = "shr_" + secrets.token_hex(24)
    expire = time.time() + max(1, min(body.days or 7, 90)) * 86400
    db.add_share(sid, _h(token), expire)
    db.add_audit(user["id"], user["username"], "share.create", f"session:{sid}", f"expires_at={expire}")
    return {"token": token, "expires_at": expire, "url": f"/share/{token}"}


@router.delete("/sessions/{sid}/share")
def revoke_share(sid: int, user: dict = Depends(get_current_user)):
    """撤销该会话的分享（按会话删除其分享记录）。"""
    sess = db.get_session(sid, user["id"])
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 撤销当前会话全部分享
    _revoke_shares_for_session(sid)
    db.add_audit(user["id"], user["username"], "share.revoke", f"session:{sid}")
    return {"ok": True}


@router.get("/shares/{token}")
def view_share(token: str):
    """公开只读查看分享（无需登录，token 即密钥）；过期或被撤销返回 404。"""
    row = db.get_share_by_token(_h(token))
    if row is None or time.time() > row["expires_at"]:
        raise HTTPException(status_code=404, detail="分享链接无效或已过期")
    sess = db.get_session_by_id(row["session_id"])
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"title": sess["title"], "messages": db.list_messages(row["session_id"])}


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _revoke_shares_for_session(session_id: int):
    # 复用 shares 表：无「按会话删」的现成函数，这里直接 SQL。
    with db.get_conn() as conn:
        conn.execute("DELETE FROM shares WHERE session_id=?", (session_id,))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
