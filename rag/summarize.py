"""R-05 超长文档的分层摘要（map-reduce）。

仅用于「超大文档」检索覆盖：当某文档文本超过阈值时，先按段落切块逐个归纳（map），
再整合为整篇摘要（reduce），作为额外分块进入向量库，帮助面向整个文档的广谱问题被召回。
默认关闭（LONG_DOC_SUMMARY=0），且任一 LLM 调用失败均回退为“不生成摘要”，不影响索引。
"""
from __future__ import annotations

import re


_MAP_SYSTEM = "你是一名文档摘要助手。请只提取给定文本的核心要点/结论，用简洁中文分点概括，不超过300字，不要臆造。"
_REDUCE_SYSTEM = "你是一名文档摘要助手。请把下列各节要点整合成一篇连贯、忠实于原文的文档摘要，用简洁中文书写，不要臆造。"


def _split_for_summarize(text: str, budget: int = 6000) -> list[str]:
    paragraphs = re.split(r"\n{2,}", text)
    pieces: list[str] = []
    cur: list[str] = []
    ln = 0
    for para in paragraphs:
        if ln + len(para) > budget and cur:
            pieces.append("\n\n".join(cur))
            cur = []
            ln = 0
        cur.append(para)
        ln += len(para) + 2
    if cur:
        pieces.append("\n\n".join(cur))
    return pieces or [text[:budget]]


def _call(client, model, system, user, temperature=0.2) -> str | None:
    try:
        r = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=800,
        )
        return (r.choices[0].message.content or "").strip() or None
    except Exception:  # noqa: BLE001 单次失败回退，不阻塞索引
        return None


def summarize_document(text: str, client, model: str, threshold: int = 20000, map_budget: int = 6000) -> str | None:
    """map 阶段分块归纳 + reduce 阶段整篇整合；太短或失败返回 None。"""
    if len(text) < threshold:
        return None
    pieces = _split_for_summarize(text, map_budget)
    summaries: list[str] = []
    for p in pieces:
        s = _call(client, model, _MAP_SYSTEM, p[:map_budget])
        if s:
            summaries.append(s)
    if not summaries:
        return None
    return _call(client, model, _REDUCE_SYSTEM, "\n\n".join(summaries))


def maybe_summary_chunks(
    chunks: list[dict],
    dataset_id: int,
    client,
    model: str,
    enabled: bool = False,
    threshold: int = 20000,
) -> list[dict]:
    """为超长文档生成摘要分块（metadata.kind=summary），否则返回空列表。"""
    if not enabled:
        return []
    by_source: dict[str, list[dict]] = {}
    for c in chunks:
        by_source.setdefault(c["metadata"].get("source", ""), []).append(c)
    out: list[dict] = []
    for source, group in by_source.items():
        # 按 chunk_index 排序近似还原原文（含少量 overlap，不影响归纳）
        group_sorted = sorted(group, key=lambda c: c["metadata"].get("chunk_index", 0))
        full = "\n".join(c["page_content"] for c in group_sorted)
        summary = summarize_document(full, client, model, threshold=threshold)
        if summary:
            out.append(
                {
                    "page_content": summary,
                    "metadata": {"source": source, "dataset_id": str(dataset_id), "kind": "summary", "chunk_index": 0},
                }
            )
    return out
