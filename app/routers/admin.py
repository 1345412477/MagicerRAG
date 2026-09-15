"""管理路由：概览统计 / 模型管理（多模型、每模型独立 Key）/ 资源配额。

人员管理（用户列表/停用/删除）与邀请码保持于 auth.py，本路由补充管理系统的其余模块。
"""
from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI

from config import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL

from .. import crypto, db, runtime_settings, tasks
from ..deps import require_admin
from ..service import kb_service

logger = logging.getLogger("magicerrag")

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 常见对话模型的预设列表：仅用于管理页下拉引导，不是硬性白名单（可自定义任意模型/地址）
MODEL_PRESETS: list[dict] = [
    {"name": "DeepSeek-V3", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "provider": "DeepSeek"},
    {"name": "DeepSeek-R1（推理）", "model": "deepseek-reasoner", "base_url": "https://api.deepseek.com/v1", "provider": "DeepSeek"},
    {"name": "Qwen-Plus", "model": "qwen-plus", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "provider": "阿里云百炼"},
    {"name": "Qwen-Turbo", "model": "qwen-turbo", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "provider": "阿里云百炼"},
    {"name": "Qwen-Long（长文本）", "model": "qwen-long", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "provider": "阿里云百炼"},
    {"name": "GPT-4o-mini", "model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1", "provider": "OpenAI"},
    {"name": "Kimi Moonshot-v1-32k", "model": "moonshot-v1-32k", "base_url": "https://api.moonshot.cn/v1", "provider": "Moonshot"},
    {"name": "GLM-4-Flash", "model": "glm-4-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "provider": "智谱AI"},
]


def _key_ok(key: str) -> bool:
    return bool(key and not key.startswith("sk-xxxx"))


def _cfg(row) -> dict:
    """把一条模型配置转为对外返回结构（绝不回传明文 API Key）。"""
    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "provider": row["provider"],
        "model": row["model"],
        "base_url": row["base_url"],
        "has_key": bool(row["has_key"]),
        "is_active": bool(row["is_active"]),
    }


def _cfg_list() -> list[dict]:
    return [_cfg(r) for r in db.list_model_configs()]


class ModelCreate(BaseModel):
    display_name: str = Field(default="", max_length=64)
    provider: str = Field(default="", max_length=64)
    model: str = Field(..., max_length=128)
    base_url: str = Field(..., max_length=256)
    api_key: str = Field(default="", max_length=512)
    is_active: bool = False


class ModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=256)
    api_key: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None


class ParamsUpdate(BaseModel):
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    top_k: int | None = Field(default=None, ge=1, le=50)
    score_threshold: float | None = Field(default=None, ge=0, le=1)
    # v0.5 新增：对话/检索质量参数
    history_turns: int | None = Field(default=None, ge=1, le=20)
    context_token_budget: int | None = Field(default=None, ge=500, le=60000)
    query_rewrite: bool | None = None
    suggest_questions: bool | None = None  # W1：回答后推荐追问开关
    mmr_enabled: bool | None = None
    mmr_lambda: float | None = Field(default=None, ge=0, le=1)
    rerank_enabled: bool | None = None
    rerank_model: str | None = Field(default=None, max_length=128)
    rerank_channel: str | None = Field(default=None, pattern="^(local|external)$")
    rerank_external_url: str | None = Field(default=None, max_length=256)
    rerank_api_key: str | None = Field(default=None, max_length=512)
    hybrid_enabled: bool | None = None
    hybrid_alpha: float | None = Field(default=None, ge=0, le=1)
    hybrid_method: str | None = Field(default=None, pattern="^(weighted|rrf)$")
    max_context_hits: int | None = Field(default=None, ge=1, le=50)
    long_doc_summary: bool | None = None
    long_doc_chars: int | None = Field(default=None, ge=1000, le=2000000)


class QuotaUpdate(BaseModel):
    max_sessions: int | None = Field(default=None, ge=1, le=10000)
    max_kb_mb: int | None = Field(default=None, ge=1, le=102400)


class ModelTest(BaseModel):
    model: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=256)
    api_key: str | None = Field(default=None, max_length=512)


@router.get("/dashboard")
def dashboard(admin: dict = Depends(require_admin)):
    return db.usage_stats()


@router.get("/usage/daily")
def usage_daily(days: int = 14, admin: dict = Depends(require_admin)):
    return {"days": days, "items": db.daily_usage(max(1, min(90, days)))}


@router.get("/model")
def get_model(admin: dict = Depends(require_admin)):
    settings = runtime_settings.model_settings()
    models = _cfg_list()
    active = db.get_active_model_config()
    boot_default = None
    if not models:
        boot_default = {
            "display_name": "环境默认（.env）",
            "provider": "env",
            "model": settings["llm_model"],
            "base_url": settings["llm_base_url"],
            "has_key": _key_ok(settings["llm_api_key"]),
            "is_active": True,
        }
    return {
        "models": models,
        "active_id": active["id"] if active else None,
        "boot_default": boot_default,
        "params": {
            "llm_temperature": settings["llm_temperature"],
            "top_k": settings["top_k"],
            "score_threshold": settings["score_threshold"],
            "history_turns": settings["history_turns"],
            "context_token_budget": settings["context_token_budget"],
            "query_rewrite": settings["query_rewrite"],
            "mmr_enabled": settings["mmr_enabled"],
            "mmr_lambda": settings["mmr_lambda"],
            "rerank_enabled": settings["rerank_enabled"],
            "rerank_model": settings["rerank_model"],
            "rerank_channel": settings["rerank_channel"],
            "rerank_external_url": settings["rerank_external_url"],
            "rerank_has_key": bool(settings["rerank_api_key"]),
            "hybrid_enabled": settings["hybrid_enabled"],
            "hybrid_alpha": settings["hybrid_alpha"],
            "hybrid_method": settings["hybrid_method"],
            "max_context_hits": settings["max_context_hits"],
            "long_doc_summary": settings["long_doc_summary"],
            "long_doc_chars": settings["long_doc_chars"],
        },
        "embedding": {
            "model": settings["embedding_model"],
            "base_url": settings["embedding_base_url"],
            "has_key": _key_ok(EMBEDDING_API_KEY),
        },
        "presets": MODEL_PRESETS,
        "note": (
            "可同时配置多个模型，每条含独立的 API Key；激活（设为主用）的一条将用于实际对话。"
            "切换即时生效，无需重启。Embedding Key 仍需在 .env 中配置。"
        ),
    }


@router.post("/model")
def create_model(body: ModelCreate, admin: dict = Depends(require_admin)):
    model = body.model.strip()
    base_url = body.base_url.strip()
    if not model or not base_url:
        raise HTTPException(status_code=400, detail="模型标识与 API 地址不能为空")
    models = db.list_model_configs()
    activate = body.is_active or not models
    db.add_model_config(
        body.display_name.strip(),
        body.provider.strip(),
        model,
        base_url,
        crypto.encrypt((body.api_key or "").strip()),
        activate,
    )
    db.add_audit(admin["id"], admin["username"], "model.create", model, base_url)
    active = db.get_active_model_config()
    return {
        "ok": True,
        "active_id": active["id"] if active else None,
        "models": _cfg_list(),
    }


@router.post("/model/test")
def test_model(body: ModelTest, admin: dict = Depends(require_admin)):
    """用输入的模型/地址/Key 发一次极短请求验证连通性（只测不保存）。"""
    rt = runtime_settings.load()
    model = (body.model or "").strip() or rt["model"]
    base_url = (body.base_url or "").strip() or rt["base_url"]
    api_key = (body.api_key or "").strip() or rt["api_key"]
    if not _key_ok(api_key):
        return {"ok": False, "model": model, "base_url": base_url, "latency_ms": 0, "error": "未填写有效 API Key"}
    started = time.time()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=20, max_retries=0)
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
        latency_ms = int((time.time() - started) * 1000)
        reply = (r.choices[0].message.content or "").strip()[:40]
        return {"ok": True, "model": model, "base_url": base_url, "latency_ms": latency_ms, "reply": reply}
    except Exception as e:  # noqa: BLE001 状态码可安全透传，其余细节只记服务端日志
        logger.warning("模型连通性测试失败 model=%s base_url=%s: %s", model, base_url, e)
        latency_ms = int((time.time() - started) * 1000)
        status = getattr(e, "status_code", None)
        msg = (
            "API Key 无效或未授权（401），请检查 Key"
            if status == 401
            else "模型或 API 地址不存在（404），请核对模型名与地址"
            if status == 404
            else "请求过频或额度不足（429）"
            if status == 429
            else f"请求被拒绝（{status}），请核对模型名与地址"
            if status
            else "连接失败，请检查 API 地址与网络"
        )
        return {"ok": False, "model": model, "base_url": base_url, "latency_ms": latency_ms, "error": msg}


@router.put("/model/{model_id}")
def update_model(model_id: int, body: ModelUpdate, admin: dict = Depends(require_admin)):
    cur = db.get_model_config(model_id)
    if not cur:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    fields: dict = {}
    if body.display_name is not None:
        fields["display_name"] = body.display_name.strip()
    if body.provider is not None:
        fields["provider"] = body.provider.strip()
    if body.model is not None:
        if not body.model.strip():
            raise HTTPException(status_code=400, detail="模型标识不能为空")
        fields["model"] = body.model.strip()
    if body.base_url is not None:
        if not body.base_url.strip():
            raise HTTPException(status_code=400, detail="API 地址不能为空")
        fields["base_url"] = body.base_url.strip()
    # API Key：仅当提供非空新值时更新（留空代表“保持不变”）
    if body.api_key is not None and body.api_key.strip():
        fields["api_key"] = crypto.encrypt(body.api_key.strip())
    if fields:
        db.update_model_config(model_id, fields)
    if body.is_active:
        db.activate_model_config(model_id)
    db.add_audit(admin["id"], admin["username"], "model.update", f"model:{model_id}")
    active = db.get_active_model_config()
    return {
        "ok": True,
        "active_id": active["id"] if active else None,
        "models": _cfg_list(),
    }


@router.post("/model/{model_id}/activate")
def activate_model(model_id: int, admin: dict = Depends(require_admin)):
    if not db.get_model_config(model_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    db.activate_model_config(model_id)
    db.add_audit(admin["id"], admin["username"], "model.activate", f"model:{model_id}")
    return {"ok": True, "active_id": model_id, "models": _cfg_list()}


@router.delete("/model/{model_id}")
def delete_model(model_id: int, admin: dict = Depends(require_admin)):
    if not db.get_model_config(model_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    was_active = db.delete_model_config(model_id)
    if was_active:
        left = db.list_model_configs()
        if left:
            db.activate_model_config(left[0]["id"])
    db.add_audit(admin["id"], admin["username"], "model.delete", f"model:{model_id}")
    active = db.get_active_model_config()
    return {
        "ok": True,
        "active_id": active["id"] if active else None,
        "models": _cfg_list(),
    }


@router.put("/model")
def update_params(body: ParamsUpdate, admin: dict = Depends(require_admin)):
    """保存检索/生成参数（温度、TopK、相关度阈值）。模型与 Key 走多模型配置接口。"""
    updates: dict[str, str] = {}
    if body.llm_temperature is not None:
        updates["llm_temperature"] = str(body.llm_temperature)
    if body.top_k is not None:
        updates["top_k"] = str(body.top_k)
    if body.score_threshold is not None:
        updates["score_threshold"] = str(body.score_threshold)
    if body.history_turns is not None:
        updates["history_turns"] = str(body.history_turns)
    if body.context_token_budget is not None:
        updates["context_token_budget"] = str(body.context_token_budget)
    if body.query_rewrite is not None:
        updates["query_rewrite"] = "1" if body.query_rewrite else "0"
    if body.suggest_questions is not None:
        updates["suggest_questions"] = "1" if body.suggest_questions else "0"
    if body.mmr_enabled is not None:
        updates["mmr_enabled"] = "1" if body.mmr_enabled else "0"
    if body.mmr_lambda is not None:
        updates["mmr_lambda"] = str(body.mmr_lambda)
    if body.rerank_enabled is not None:
        updates["rerank_enabled"] = "1" if body.rerank_enabled else "0"
    if body.rerank_model is not None:
        updates["rerank_model"] = body.rerank_model.strip()
    if body.rerank_channel is not None:
        updates["rerank_channel"] = body.rerank_channel
    if body.rerank_external_url is not None:
        updates["rerank_external_url"] = body.rerank_external_url.strip()
    if body.rerank_api_key is not None and body.rerank_api_key.strip():
        updates["rerank_api_key"] = body.rerank_api_key.strip()
    if body.hybrid_enabled is not None:
        updates["hybrid_enabled"] = "1" if body.hybrid_enabled else "0"
    if body.hybrid_alpha is not None:
        updates["hybrid_alpha"] = str(body.hybrid_alpha)
    if body.hybrid_method is not None:
        updates["hybrid_method"] = body.hybrid_method
    if body.max_context_hits is not None:
        updates["max_context_hits"] = str(body.max_context_hits)
    if body.long_doc_summary is not None:
        updates["long_doc_summary"] = "1" if body.long_doc_summary else "0"
    if body.long_doc_chars is not None:
        updates["long_doc_chars"] = str(body.long_doc_chars)
    for k, v in updates.items():
        db.set_setting(k, v)
    db.add_audit(admin["id"], admin["username"], "params.update", "", ",".join(sorted(updates)))
    m = runtime_settings.model_settings()
    return {
        "ok": True,
        "params": {
            "llm_temperature": m["llm_temperature"],
            "top_k": m["top_k"],
            "score_threshold": m["score_threshold"],
            "history_turns": m["history_turns"],
            "context_token_budget": m["context_token_budget"],
            "query_rewrite": m["query_rewrite"],
            "mmr_enabled": m["mmr_enabled"],
            "mmr_lambda": m["mmr_lambda"],
            "rerank_enabled": m["rerank_enabled"],
            "rerank_model": m["rerank_model"],
            "rerank_channel": m["rerank_channel"],
            "rerank_external_url": m["rerank_external_url"],
            "rerank_has_key": bool(m["rerank_api_key"]),
            "hybrid_enabled": m["hybrid_enabled"],
            "hybrid_alpha": m["hybrid_alpha"],
            "hybrid_method": m["hybrid_method"],
            "max_context_hits": m["max_context_hits"],
            "long_doc_summary": m["long_doc_summary"],
            "long_doc_chars": m["long_doc_chars"],
        },
    }


@router.get("/audit")
def list_audit(limit: int = 100, admin: dict = Depends(require_admin)):
    """管理操作审计日志（用户停用/删除、邀请码、配额、模型、分享等）。"""
    limit = max(1, min(limit, 500))
    return {"items": db.list_audit(limit)}


# ---------- Embedding 一致性（R-07） ----------
@router.get("/embedding-status")
def embedding_status(admin: dict = Depends(require_admin)):
    """当前 Embedding vs 最近一次建索引所用 Embedding 是否一致。"""
    stored = db.get_setting("index_embedding_model")
    return {
        "current": EMBEDDING_MODEL,
        "index_model": stored,
        "needs_rebuild": bool(stored and stored != EMBEDDING_MODEL),
        "base_url": EMBEDDING_BASE_URL,
        "has_key": _key_ok(EMBEDDING_API_KEY),
        "note": "变更 Embedding 后请重建所有索引，否则检索将报错或不一致。",
    }


@router.post("/rebuild-all")
def rebuild_all(admin: dict = Depends(require_admin)):
    """将全部数据集加入后台重建任务（R-06 任务机制，R-07 一键重建）。"""
    ds_ids = db.list_all_dataset_ids()
    tid = tasks.create("rebuild_all", f"重建所有知识库索引（{len(ds_ids)} 个）")
    tasks.update(tid, status="running", progress=1, detail="任务已创建")

    def _runner():
        total = len(ds_ids)
        for i, did in enumerate(ds_ids, 1):
            try:
                n = kb_service.rebuild_index(did)
                tasks.update(tid, detail=f"知识库 {i}/{total}：{n} 个区块")
            except Exception:  # noqa: BLE001
                logger.warning("rebuild-all dataset=%s 失败", did, exc_info=True)
                tasks.update(tid, detail=f"知识库 {i}/{total} 重建失败")
            tasks.update(tid, progress=int(i / total * 100) if total else 100)
        tasks.update(tid, status="done", progress=100, detail=f"全部完成（{total} 个知识库）")

    threading.Thread(target=_runner, daemon=True).start()
    db.add_audit(admin["id"], admin["username"], "embedding.rebuild_all", "", f"datasets={len(ds_ids)}")
    return {"ok": True, "task_id": tid, "datasets": len(ds_ids)}


@router.get("/quotas")
def list_quotas(admin: dict = Depends(require_admin)):
    return db.list_user_quotas()


@router.put("/quotas/{user_id}")
def set_quota(user_id: int, body: QuotaUpdate, admin: dict = Depends(require_admin)):
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    db.set_user_quota(user_id, body.max_sessions, body.max_kb_mb)
    db.add_audit(
        admin["id"], admin["username"], "quota.update", f"user:{user_id}",
        f"max_sessions={body.max_sessions}, max_kb_mb={body.max_kb_mb}",
    )
    return {"ok": True}
