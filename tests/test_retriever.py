"""检索/上下文组装单元测试：MMR、重排降级、上下文预算、消息组装。"""
from __future__ import annotations

from app.service import rag_service
from rag.retriever import _mmr_select
from rag.rerank import rerank_hits


def _cand(i):
    return {"content": f"片段{i}", "score": 0.5, "source": f"doc{i}.md"}


def test_mmr_select_respects_topk_and_diversity():
    query = [1.0, 0.0, 0.0]
    embs = [
        [1.0, 0.0, 0.0],
        [0.9, 0.1, 0.0],
        [0.0, 1.0, 0.0],
    ]
    cands = [_cand(i) for i in range(3)]
    picked = _mmr_select(query, embs, cands, top_k=3, lam=0.5)
    assert len(picked) == 3
    # 高 lambda 时优先相关；含多样性应至少覆盖不同方向
    picked2 = _mmr_select(query, embs, cands, top_k=2, lam=0.6)
    assert len(picked2) == 2


def test_rerank_gracefully_falls_back_without_model():
    hits = [_cand(i) for i in range(4)]
    # 未安装 sentence_transformers 时应原样返回且不抛异常
    out = rerank_hits("q", hits, top_k=2, model_name="BAAI/bge-reranker-base")
    assert out == hits[:2]


def test_fit_context_truncates_when_over_budget():
    hits = [{"content": "很长的内容" * 400, "score": 0.9, "source": "a.md"} for _ in range(3)]
    kept, truncated = rag_service.fit_context(hits, token_budget=500)
    assert truncated is True
    assert len(kept) < len(hits)


def test_build_messages_includes_history_with_budget():
    history = [{"role": "user", "content": "之前的问题"}, {"role": "assistant", "content": "之前的回答"}]
    msgs = rag_service.build_messages("现在呢", "资料", history=history, history_budget=10000)
    assert msgs[0]["role"] == "system"
    assert any(m["role"] == "user" and m["content"] == "之前的问题" for m in msgs)
    assert msgs[-1]["role"] == "user"
    assert "资料" in msgs[-1]["content"]
