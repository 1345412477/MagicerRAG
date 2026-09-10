"""知识库路由：数据集（个人库/团队库）管理、成员管理、上传、重建索引、删库。"""
from __future__ import annotations

import logging
import re
import shutil
import threading

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config import UPLOAD_DIR
from .. import db, tasks
from ..deps import (
    get_current_user,
    require_dataset_access,
    require_dataset_manager,
    require_dataset_owner,
)
from ..schemas import (
    DatasetParamsUpdate,
    DatasetRename,
    DeleteFiles,
    MemberAdd,
    MemberRoleUpdate,
    TeamCreate,
)
from ..service import kb_service
from rag.extractors import ALLOWED_EXTENSIONS, TEXT_SUFFIXES, extract_text
from rag.ingestor import chunk_documents, load_documents

router = APIRouter(prefix="/api/kb", tags=["kb"])

_ALLOWED_EXT = ALLOWED_EXTENSIONS
_MAX_SIZE = 50 * 1024 * 1024  # 50MB（办公文档/PDF/图片通常更大）

# 扩展名 -> 可信 magic 前缀（用于内容与扩展名一致性校验，防伪装文件）
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
    ".webp": (b"RIFF",),
    ".docx": (b"PK",),
    ".xlsx": (b"PK",),
    ".pptx": (b"PK",),
}
_LENIENT = TEXT_SUFFIXES | {".doc", ".xls", ".ppt"}  # 文本/老格式宽松放行

logger = logging.getLogger("magicerrag")


def _mime_matches(filename: str, content: bytes) -> bool:
    """扩展名与内容 magic 一致性校验；文本与老二进制走宽松放行，避免误伤。"""
    ext = re.search(r"\.([a-z0-9]+)$", filename or "", re.I)
    if not ext:
        return False
    suffix = "." + ext.group(1).lower()
    if suffix in _LENIENT:
        return True
    magic = _MAGIC.get(suffix)
    if not magic:
        return True  # 未知类型不拦（已由扩展名白名单兜底）
    return content.startswith(magic)


def _dataset_out(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "owner_id": row["owner_id"],
        "my_role": row["my_role"] if "my_role" in row.keys() else None,
    }


# ---------- 数据集 ----------
@router.get("/datasets")
def list_datasets(user: dict = Depends(get_current_user)):
    _ = db.ensure_personal_dataset(user["id"])
    return [_dataset_out(r) for r in db.list_my_datasets(user["id"])]


@router.post("/datasets/team")
def create_team(body: TeamCreate, user: dict = Depends(get_current_user)):
    did = db.create_team_dataset(user["id"], body.name)
    return _dataset_out(db.get_dataset(did))


@router.patch("/datasets/{datasetId}/name")
def rename(datasetId: int, body: DatasetRename, user: dict = Depends(get_current_user)):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_owner(datasetId, user)
    if db.get_dataset(datasetId)["type"] == "personal":
        raise HTTPException(status_code=400, detail="个人库不可改名")
    db.rename_dataset(datasetId, body.name)
    return {"ok": True}


@router.delete("/datasets/{datasetId}")
def delete_dataset(datasetId: int, user: dict = Depends(get_current_user)):
    ds = db.get_dataset(datasetId)
    if not ds:
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_owner(datasetId, user)
    if ds["type"] == "personal":
        raise HTTPException(status_code=400, detail="个人库不可删除")
    # 级联：向量 + 磁盘文件 + 库内成员与记录
    kb_service.clear_vectors_for(datasetId)
    kb_service.delete_dataset_files(datasetId)
    db.delete_dataset(datasetId)
    return {"ok": True}


# ---------- 成员管理（团队库，Owner） ----------
@router.get("/datasets/{datasetId}/members")
def members(datasetId: int, user: dict = Depends(get_current_user)):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_access(datasetId, user)
    return [
        {"id": m["user_id"], "username": m["username"], "role": m["role"]}
        for m in db.list_members(datasetId)
    ]


@router.post("/datasets/{datasetId}/members")
def add_member(
    datasetId: int, body: MemberAdd, user: dict = Depends(get_current_user)
):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_owner(datasetId, user)
    target = db.get_user_by_name(body.username)
    if not target:
        raise HTTPException(status_code=404, detail="目标用户不存在")
    if body.role not in {"owner", "manager", "member"}:
        raise HTTPException(status_code=400, detail="角色不合法")
    db.add_member(target["id"], datasetId, body.role)
    return {"ok": True}


@router.patch("/datasets/{datasetId}/members/{targetUserId}/role")
def change_role(
    datasetId: int,
    targetUserId: int,
    body: MemberRoleUpdate,
    user: dict = Depends(get_current_user),
):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_owner(datasetId, user)
    if body.role not in {"owner", "manager", "member"}:
        raise HTTPException(status_code=400, detail="角色不合法")
    target = next((m for m in db.list_members(datasetId) if m["user_id"] == targetUserId), None)
    if not target:
        raise HTTPException(status_code=404, detail="该成员不在知识库中")
    if target["role"] == "owner" and body.role != "owner" and not user["is_admin"]:
        owner_count = sum(1 for m in db.list_members(datasetId) if m["role"] == "owner")
        if targetUserId == user["id"] or owner_count <= 1:
            raise HTTPException(status_code=400, detail="至少需保留一名所有者，且不能降低自己的所有者权限")
    if targetUserId == user["id"] and body.role != "owner" and not user["is_admin"]:
        raise HTTPException(status_code=400, detail="不能降低自己的所有者权限")
    db.set_member_role(targetUserId, datasetId, body.role)
    return {"ok": True}


@router.delete("/datasets/{datasetId}/members/{targetUserId}")
def remove_member(
    datasetId: int, targetUserId: int, user: dict = Depends(get_current_user)
):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_owner(datasetId, user)
    if targetUserId == user["id"] and not user["is_admin"]:
        raise HTTPException(status_code=400, detail="所有者不能移除自己")
    db.remove_member(targetUserId, datasetId)
    return {"ok": True}


# ---------- 上传 / 文件 / 重建（有访问权限） ----------
@router.get("/datasets/{datasetId}/files")
def list_files(datasetId: int, user: dict = Depends(get_current_user)):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_access(datasetId, user)
    rows = db.list_kb_files(datasetId)
    return [
        {
            "id": r["id"],
            "filename": r["filename"],
            "size": r["size"],
            "status": r["status"],
            "parse_status": r["parse_status"] if "parse_status" in r.keys() else "ok",
            "uploaded_at": r["uploaded_at"],
        }
        for r in rows
    ]


# ---------- 数据集级检索参数（R-04） ----------
@router.get("/datasets/{datasetId}/params")
def get_params(datasetId: int, user: dict = Depends(get_current_user)):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_access(datasetId, user)
    dp = db.get_dataset_params(datasetId)
    return {
        "dataset_id": datasetId,
        "score_threshold": dp["score_threshold"] if dp else None,
        "top_k": dp["top_k"] if dp else None,
        "split_mode": dp["split_mode"] if dp else None,
        "chunk_size": dp["chunk_size"] if dp else None,
        "overlap": dp["overlap"] if dp else None,
    }


@router.put("/datasets/{datasetId}/params")
def set_params(datasetId: int, body: DatasetParamsUpdate, user: dict = Depends(get_current_user)):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_manager(datasetId, user)
    db.set_dataset_params(
        datasetId,
        body.score_threshold,
        body.top_k,
        body.split_mode,
        body.chunk_size,
        body.overlap,
    )
    return {"ok": True}


@router.get("/datasets/{datasetId}/chunks-preview")
def chunks_preview(
    datasetId: int,
    user: dict = Depends(get_current_user),
    limit: int = 12,
):
    """只读分块预览：按当前门类分块参数对库内文档切分，返回前若干区块，供重建前确认效果。"""
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_access(datasetId, user)
    upload_dir = kb_service.dataset_upload_dir(datasetId)
    if not upload_dir.exists() or not any(upload_dir.iterdir()):
        return {"chunks": [], "effective": None}
    dp = db.get_dataset_params(datasetId)
    kwargs: dict[str, object] = {}
    if dp:
        if dp["split_mode"]:
            kwargs["mode"] = dp["split_mode"]
        if dp["chunk_size"]:
            kwargs["chunk_size"] = dp["chunk_size"]
        if dp["overlap"] is not None:
            kwargs["overlap"] = dp["overlap"]
    chunks = chunk_documents(load_documents(upload_dir), **kwargs)
    return {
        "effective": {
            "split_mode": kwargs.get("mode", "markdown"),
            "chunk_size": kwargs.get("chunk_size", 500),
            "overlap": kwargs.get("overlap", 50),
        },
        "total": len(chunks),
        "chunks": [
            {
                "index": c["metadata"].get("chunk_index", i),
                "source": c["metadata"].get("source", ""),
                "section": c["metadata"].get("section", ""),
                "content": c["page_content"][:400],
                "length": len(c["page_content"]),
            }
            for i, c in enumerate(chunks[:max(1, min(limit or 12, 50))])
        ],
    }


@router.get("/datasets/{datasetId}/files/{fileId}/download")
def download_file(
    datasetId: int, fileId: int, user: dict = Depends(get_current_user)
):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_access(datasetId, user)
    f = db.get_kb_file(fileId)
    if not f or f["dataset_id"] != datasetId:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = kb_service.dataset_upload_dir(datasetId) / f["filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件已不存在")
    return FileResponse(path, filename=f["filename"], media_type="application/octet-stream")


@router.get("/datasets/{datasetId}/files/{fileId}/content")
def preview_file(
    datasetId: int, fileId: int, user: dict = Depends(get_current_user)
):
    """返回文件文本内容，供成员「查阅」（截断大文件）。"""
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_access(datasetId, user)
    f = db.get_kb_file(fileId)
    if not f or f["dataset_id"] != datasetId:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = kb_service.dataset_upload_dir(datasetId) / f["filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件已不存在")
    # 文本类直接读；Word/PDF/Excel/PPT/图片等走文本提取，避免二进制按 utf-8 解码产生乱码
    if path.suffix.lower() in TEXT_SUFFIXES:
        text = path.read_bytes().decode("utf-8", errors="replace")
    else:
        text = extract_text(path)
    return {"filename": f["filename"], "content": text[:200000]}


def _remove_file(f, datasetId: int, user: dict):
    """删除单个文件：校验权限/归属、移除磁盘文件、清理 DB 记录。返回 None 或该文件记录。"""
    require_dataset_manager(datasetId, user)
    if f is None or f["dataset_id"] != datasetId:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = kb_service.dataset_upload_dir(datasetId) / f["filename"]
    if path.exists():
        path.unlink()
    db.delete_kb_file(f["id"])
    return f


def _rebuild_after_delete(datasetId: int):
    """删除后重索引，保证向量与剩余文件一致；失败则标记为待重建。"""
    try:
        kb_service.rebuild_index(datasetId)
    except Exception:  # noqa: BLE001 重建失败不影响删除；标记为待重建
        for f in db.list_kb_files(datasetId):
            db.set_kb_file_status(f["id"], "pending")
    else:
        _sync_index_status(datasetId)


@router.delete("/datasets/{datasetId}/files/{fileId}")
def delete_file(datasetId: int, fileId: int, user: dict = Depends(get_current_user)):
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    f = db.get_kb_file(fileId)
    _remove_file(f, datasetId, user)
    _rebuild_after_delete(datasetId)
    return {"ok": True}


@router.post("/datasets/{datasetId}/files/batch-delete")
def batch_delete_files(datasetId: int, body: DeleteFiles, user: dict = Depends(get_current_user)):
    """批量删除文件：一次性校验并删除多个，末尾统一重建索引一次。"""
    if not db.get_dataset(datasetId):
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_manager(datasetId, user)
    removed = 0
    for file_id in body.ids:
        f = db.get_kb_file(file_id)
        if f is None or f["dataset_id"] != datasetId:
            continue
        _remove_file(f, datasetId, user)
        removed += 1
    if removed:
        _rebuild_after_delete(datasetId)
    return {"ok": True, "removed": removed}


def _sync_index_status(datasetId: int):
    """按磁盘文件是否存在同步该库文件索引状态（flush 到 indexed/pending）。"""
    upload_dir = kb_service.dataset_upload_dir(datasetId)
    for f in db.list_kb_files(datasetId):
        db.set_kb_file_status(
            f["id"], "indexed" if (upload_dir / f["filename"]).exists() else "pending"
        )


@router.post("/upload")
async def upload(
    file: UploadFile = File(...), dataset_id: int = Form(0),
    user: dict = Depends(get_current_user),
):
    ds = db.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_manager(dataset_id, user)
    ext = re.search(r"\.([a-z0-9]+)$", file.filename or "", re.I)
    if not ext or ("." + ext.group(1).lower()) not in _ALLOWED_EXT:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型，仅支持 {kb_service.allowed_ext_label()}")
    content = await file.read()
    if len(content) > _MAX_SIZE:
        raise HTTPException(status_code=413, detail="文件超过 50MB 限制")
    # R-08：内容与扩展名一致性校验，拒绝伪装文件
    if not _mime_matches(file.filename or "", content):
        raise HTTPException(status_code=400, detail="文件内容与扩展名不符，请检查文件类型")
    # 存储配额：按该用户参与所有库已用字节 + 本次文件，超出返回 413
    max_kb = db.get_user(user["id"])["max_kb_mb"]
    if db.sum_kb_bytes_for_user(user["id"]) + len(content) > max_kb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="个人知识库容量已满，请联系管理员调整配额")
    # 仅清理路径非法字符（保留中文等多字节字符），并避免重名覆盖：同名时追加序号
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", file.filename or "").strip() or "document.txt"
    dest_dir = kb_service.dataset_upload_dir(dataset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = safe_name
    i = 1
    while (dest_dir / candidate).exists():
        stem, dot, ext = candidate.rpartition(".")
        candidate = f"{stem}_{i}" + (f".{ext}" if dot else "")
        i += 1
    dest = dest_dir / candidate
    dest.write_bytes(content)
    file_id = db.add_kb_file(dataset_id, candidate, len(content))
    db.set_kb_file_parse_status(file_id, "pending")
    return {"ok": True, "filename": candidate, "size": len(content)}


@router.post("/datasets/{datasetId}/rebuild")
def rebuild(datasetId: int, user: dict = Depends(get_current_user)):
    ds = db.get_dataset(datasetId)
    if not ds:
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_manager(datasetId, user)
    # R-06：改为后台任务，立即返回 task_id，前端轮询进度，避免大库重建阻塞请求。
    tid = tasks.create("rebuild", f"重建知识库 #{datasetId}")
    tasks.update(tid, status="running", detail="任务已创建", progress=1)

    def _progress(pct, detail):
        tasks.update(tid, status="running", progress=int(pct), detail=detail)

    def _runner():
        try:
            n = kb_service.rebuild_index(datasetId, progress_cb=_progress)
            tasks.update(tid, status="done", progress=100, detail=f"完成，共 {n} 个区块")
            # 异步重建完成后同步文件索引状态（与 rebuild_sync 一致，避免 UI 显示长期停留在「待索引」）
            _sync_index_status(datasetId)
        except Exception:  # noqa: BLE001
            logger.exception("重建索引失败（dataset=%s, task=%s）", datasetId, tid)
            tasks.update(tid, status="error", detail="重建索引失败，请稍后重试或检查文件内容")

    threading.Thread(target=_runner, daemon=True).start()
    return {"ok": True, "task_id": tid}


@router.get("/tasks/{tid}")
def task_status(tid: str, user: dict = Depends(get_current_user)):
    t = tasks.get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


@router.get("/tasks")
def task_list(user: dict = Depends(get_current_user)):
    return {"items": tasks.list_recent(50)}


@router.post("/datasets/{datasetId}/rebuild_sync")
def rebuild_sync(datasetId: int, user: dict = Depends(get_current_user)):
    """同步重建（供少量文件/兼容历史调用）；返回区块数。"""
    ds = db.get_dataset(datasetId)
    if not ds:
        raise HTTPException(status_code=404, detail="知识库不存在")
    require_dataset_manager(datasetId, user)
    try:
        n = kb_service.rebuild_index(datasetId)
    except Exception:  # noqa: BLE001 记录细节到服务端日志，对外只回显通用文案
        logger.exception("重建索引失败（dataset=%s）", datasetId)
        raise HTTPException(status_code=500, detail="重建索引失败，请稍后重试或检查文件内容")
    for f in db.list_kb_files(datasetId):
        db.set_kb_file_status(
            f["id"],
            "indexed" if (kb_service.dataset_upload_dir(datasetId) / f["filename"]).exists() else "pending",
        )
    return {"ok": True, "count": n}
