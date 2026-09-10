"""R-03 混合检索：稠密向量 + BM25 稀疏，按权重融合。

纯 Python 实现（无外部依赖），对整库做稀疏召回后与稠密结果按 alpha 融合，
提升长尾/术语化问题的召回与排序。默认关闭（HYBRID_ENABLED=0）。
"""
from __future__ import annotations

import math
import re

_STOPWORDS = {
    "的", "了", "和", "是", "在", "与", "及", "或", "这", "那", "一个", "什么", "如何",
    "the", "a", "an", "is", "are", "to", "of", "and", "in", "on", "for",
}


def tokenize(text: str) -> list[str]:
    """中英混合分词：英文按词，中文按字（保留 2+ 字连续串，简单字级切分）。"""
    text = text.lower()
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text)
    out: list[str] = []
    for w in words:
        if re.match(r"[a-z0-9]+", w):
            if w not in _STOPWORDS:
                out.append(w)
        else:
            # 中文：整词 + 双字 bigram + 单字（提升“注册/账号”等词命中率）
            if w not in _STOPWORDS:
                out.append(w)
            for c in w:
                if c not in _STOPWORDS:
                    out.append(c)
            if len(w) >= 2:
                for i in range(len(w) - 1):
                    big = w[i:i + 2]
                    if big not in _STOPWORDS:
                        out.append(big)
    return out


class BM25:
    """Okapi BM25 打分器，基于给定文档语料。

    N1：额外维护「词 → 文档下标」的**倒排索引** (`_postings`)，`score()` 只遍历
    真正命中的文档，避免对每条查询词都全库扫描，索引/打分复杂度从 O(N) 降到 O(命中)。
    """

    def __init__(self, docs: list[dict], k1: float = 1.5, b: float = 0.75):
        self._docs = docs
        self._k1 = k1
        self._b = b
        self._n = len(docs)
        self._doc_tokens = [tokenize(d.get("content", "")) for d in docs]
        self._doc_len = [len(t) for t in self._doc_tokens]
        self._avgdl = sum(self._doc_len) / self._n if self._n else 0.0
        self._df: dict[str, int] = {}
        self._postings: dict[str, list[int]] = {}
        for i, toks in enumerate(self._doc_tokens):
            for w in set(toks):
                self._df[w] = self._df.get(w, 0) + 1
                self._postings.setdefault(w, []).append(i)

    def score(self, query: str) -> list[float]:
        q_tokens = tokenize(query)
        scores = [0.0] * self._n
        for w in q_tokens:
            df = self._df.get(w, 0)
            if df == 0:
                continue
            idf = math.log((self._n - df + 0.5) / (df + 0.5) + 1)
            for i in self._postings.get(w, ()):
                tf = self._doc_tokens[i].count(w)
                dl = self._doc_len[i]
                denom = tf + self._k1 * (1 - self._b + self._b * (dl / (self._avgdl or 1)))
                scores[i] += idf * (tf * (self._k1 + 1)) / denom
        return scores


def _normalize(vals: list[float]) -> list[float]:
    if not vals:
        return vals
    mx = max(vals)
    if mx <= 0:
        return [0.0] * len(vals)
    return [v / mx for v in vals]


def _ranks(items: list[dict]) -> dict[str, int]:
    """按 score 降序给 items 排 1 基名次，返回 content -> rank。"""
    ordered = sorted(items, key=lambda h: -h.get("score", 0.0))
    return {h.get("content", ""): i + 1 for i, h in enumerate(ordered)}


def _fuse_rrf(dense: list[dict], lexical: list[dict], top_k: int = 4, k: float = 60.0) -> list[dict]:
    """RRF（Reciprocal Rank Fusion）排名融合：score = Σ 1/(k + rank)。

    对稠密与稀疏各自的名次取倒数求和，不依赖分数尺度/归一化，对术语化、指令词问题更稳健。
    相比加权融合，两个列表只要进了候选就有贡献，避免某一通道分数被低估而整体失落。
    """
    rank_d, rank_l = _ranks(dense), _ranks(lexical)
    by_content: dict[str, dict] = {}
    for h in dense + lexical:
        by_content.setdefault(h.get("content", ""), dict(h))
    merged: list[dict] = []
    for key, h in by_content.items():
        s = 0.0
        if key in rank_d:
            s += 1.0 / (k + rank_d[key])
        if key in rank_l:
            s += 1.0 / (k + rank_l[key])
        out = dict(h)
        out["score"] = round(s, 4)
        merged.append(out)
    merged.sort(key=lambda x: -x["score"])
    return merged[:top_k]


def fuse(dense: list[dict], lexical: list[dict], alpha: float = 0.7, top_k: int = 4, method: str = "weighted") -> list[dict]:
    """稠密 + 稀疏融合，按内容去重。

    method="weighted"：score = alpha*dense_norm + (1-alpha)*lexical_norm（默认，兼容旧行为）；
    method="rrf"    ：score = Σ 1/(k + rank)，排名融合，对分数尺度不敏感。
    """
    if method == "rrf":
        return _fuse_rrf(dense, lexical, top_k=top_k)
    by_content: dict[str, dict] = {}
    for h in dense:
        key = h.get("content", "")
        by_content.setdefault(key, dict(h))
    for h in lexical:
        key = h.get("content", "")
        if key in by_content:
            by_content[key]["_lex"] = h.get("score", 0.0)
        else:
            by_content[key] = dict(h, _lex=h.get("score", 0.0), _dense=0.0)
    for h in by_content.values():
        h.setdefault("_dense", h.get("score", 0.0))
        h["_lex"] = h.get("_lex", 0.0)
    dense_vals = [h["_dense"] for h in by_content.values()]
    lex_vals = [h["_lex"] for h in by_content.values()]
    dn, ln = _normalize(dense_vals), _normalize(lex_vals)
    merged: list[dict] = []
    for i, h in enumerate(by_content.values()):
        out = dict(h)
        out["score"] = round(alpha * dn[i] + (1 - alpha) * ln[i], 4)
        merged.append(out)
    merged.sort(key=lambda x: -x["score"])
    return merged[:top_k]
