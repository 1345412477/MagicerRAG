"""运行时模型/检索设置：管理页可覆盖并持久化到 settings 表，未覆盖则回落环境配置。

仅 LLM 生成参数与检索参数支持运行时切换；Embedding 模型变更会影响向量索引，
故此处仅透出用于展示，不参与运行（需重建索引后经环境变量重启生效）。
"""
from __future__ import annotations

from config import (
    CONTEXT_TOKEN_BUDGET,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    HISTORY_TURNS,
    HYBRID_ALPHA,
    HYBRID_ENABLED,
    HYBRID_METHOD,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LONG_DOC_CHARS,
    LONG_DOC_SUMMARY,
    MAX_CONTEXT_HITS,
    MMR_ENABLED,
    MMR_LAMBDA,
    QUERY_REWRITE,
    RERANK_ENABLED,
    RERANK_MODEL,
    RERANK_API_KEY,
    RERANK_CHANNEL,
    RERANK_EXTERNAL_URL,
    SCORE_THRESHOLD,
    SUGGEST_QUESTIONS,
    TOP_K,
)

from . import crypto, db

# 可在管理页修改的键 -> 环境默认值
_DEFAULTS: dict[str, str] = {
    "llm_model": LLM_MODEL,
    "llm_base_url": LLM_BASE_URL,
    "llm_api_key": LLM_API_KEY,
    "llm_temperature": str(LLM_TEMPERATURE),
    "top_k": str(TOP_K),
    "score_threshold": str(SCORE_THRESHOLD),
    "history_turns": str(HISTORY_TURNS),
    "context_token_budget": str(CONTEXT_TOKEN_BUDGET),
    "query_rewrite": "1" if QUERY_REWRITE else "0",
    "suggest_questions": "1" if SUGGEST_QUESTIONS else "0",
    "mmr_enabled": "1" if MMR_ENABLED else "0",
    "mmr_lambda": str(MMR_LAMBDA),
    "rerank_enabled": "1" if RERANK_ENABLED else "0",
    "rerank_model": RERANK_MODEL,
    "rerank_channel": RERANK_CHANNEL,
    "rerank_external_url": RERANK_EXTERNAL_URL,
    "rerank_api_key": RERANK_API_KEY,
    "hybrid_enabled": "1" if HYBRID_ENABLED else "0",
    "hybrid_alpha": str(HYBRID_ALPHA),
    "hybrid_method": HYBRID_METHOD,
    "max_context_hits": str(MAX_CONTEXT_HITS),
    "long_doc_summary": "1" if LONG_DOC_SUMMARY else "0",
    "long_doc_chars": str(LONG_DOC_CHARS),
    # 以下为展示用（可供查看，改动需重建索引后重启生效）
    "embedding_model": EMBEDDING_MODEL,
    "embedding_base_url": EMBEDDING_BASE_URL,
}
_EDITABLE = {
    "llm_model",
    "llm_base_url",
    "llm_temperature",
    "top_k",
    "score_threshold",
    "history_turns",
    "context_token_budget",
    "query_rewrite",
    "suggest_questions",
    "mmr_enabled",
    "mmr_lambda",
    "rerank_enabled",
    "rerank_model",
    "rerank_channel",
    "rerank_external_url",
    "rerank_api_key",
    "hybrid_enabled",
    "hybrid_alpha",
    "hybrid_method",
    "max_context_hits",
    "long_doc_summary",
    "long_doc_chars",
}


def model_settings() -> dict[str, str]:
    """返回所有模型/检索键的当前有效值（DB 覆盖优先，否则环境默认）。"""
    stored = db.get_all_setting_keys()
    out = {k: stored.get(k, default) for k, default in _DEFAULTS.items()}
    active = db.get_active_model_config()
    if active:
        out["llm_model"] = active["model"]
        out["llm_base_url"] = active["base_url"]
        if active["api_key"]:
            out["llm_api_key"] = crypto.decrypt(active["api_key"])
    return out


def load() -> dict:
    """数值化后的便捷读取，供服务层使用。"""
    m = model_settings()
    return {
        "model": m["llm_model"],
        "base_url": m["llm_base_url"],
        "api_key": m["llm_api_key"],
        "temperature": float(m["llm_temperature"]),
        "top_k": int(m["top_k"]),
        "score_threshold": float(m["score_threshold"]),
        "history_turns": int(m["history_turns"]),
        "context_token_budget": int(m["context_token_budget"]),
        "query_rewrite": m["query_rewrite"] == "1",
        "suggest_questions": m["suggest_questions"] == "1",
        "mmr_enabled": m["mmr_enabled"] == "1",
        "mmr_lambda": float(m["mmr_lambda"]),
        "rerank_enabled": m["rerank_enabled"] == "1",
        "rerank_model": m["rerank_model"],
        "rerank_channel": m["rerank_channel"],
        "rerank_external_url": m["rerank_external_url"],
        "rerank_api_key": m["rerank_api_key"],
        "hybrid_enabled": m["hybrid_enabled"] == "1",
        "hybrid_alpha": float(m["hybrid_alpha"]),
        "hybrid_method": m["hybrid_method"],
        "max_context_hits": int(m["max_context_hits"]),
        "long_doc_summary": m["long_doc_summary"] == "1",
        "long_doc_chars": int(m["long_doc_chars"]),
        "embedding_model": m["embedding_model"],
        "embedding_base_url": m["embedding_base_url"],
    }


def editable_keys() -> list[str]:
    return sorted(_EDITABLE)
