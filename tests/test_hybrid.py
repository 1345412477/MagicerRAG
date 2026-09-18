"""R-03 混合检索：BM25 打分与稠密/稀疏融合。"""
from __future__ import annotations

from rag.hybrid import BM25, fuse, tokenize


def test_tokenize_handles_mixed_chinese_english():
    toks = tokenize("如何注册账号 how to register")
    assert "register" in toks
    assert "注册" in toks


def test_bm25_ranks_matching_doc_higher():
    docs = [
        {"content": "账号注册需要邀请码", "source": "a.md"},
        {"content": "如何重置密码", "source": "b.md"},
    ]
    bm = BM25(docs)
    scores = bm.score("账号注册")
    assert scores[0] > scores[1]


def test_fuse_merges_and_normalizes():
    dense = [
        {"content": "甲", "score": 0.9, "source": "a.md"},
        {"content": "乙", "score": 0.5, "source": "b.md"},
    ]
    lexical = [
        {"content": "甲", "score": 0.4, "source": "a.md"},
        {"content": "丙", "score": 0.8, "source": "c.md"},
    ]
    merged = fuse(dense, lexical, alpha=0.7, top_k=3)
    assert merged[0]["content"] == "甲"  # 稠密强 + 中词命中，应居首
    assert len(merged) == 3
    assert all(0 <= m["score"] <= 1 for m in merged)
