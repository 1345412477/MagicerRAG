"""面向网页工作台的服务层：把现有 RAG 管道封装成便于 API 调用的形态，并支持流式生成。"""
from __future__ import annotations

from typing import Iterator

from openai import AsyncOpenAI, OpenAI

from config import TOP_K
from rag.retriever import get_or_create_store, retrieve
from .. import db, runtime_settings

# AsyncOpenAI 客户端按 base_url 缓存复用（同一地址不重复建连接池）；
# model/temperature 在请求时传入，故切换模型即时生效，仅更换地址才新建 client。
_ahttp_clients: dict[str, AsyncOpenAI] = {}


def _ahttp(base_url: str, api_key: str) -> AsyncOpenAI:
    cache_key = (base_url, api_key)
    client = _ahttp_clients.get(cache_key)
    if client is None:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        _ahttp_clients[cache_key] = client
    return client

# 服务层专用生成提示：在 _SYSTEM 基础上引入「内联编号引用」，供前端渲染为可点击的①上标。
# 独立定义而非改动 rag/generator._SYSTEM，避免影响 CLI（ingest/query）该共享 prompt 的行为。
_SERVICE_SYSTEM = """你是一个忠实于给定资料的问答助手。请只依据下面的"资料"回答用户问题。

要求：
1. 若资料不足，直接说明"资料中没有相关信息"，不要编造。
2. 引用资料中的内容时，请在该句末尾标注对应的资料编号，格式如[1]、[2]（编号即资料条目开头的中括号序号）。
3. 一个句子若同时依据多条资料，可并列标注多个编号，如[1][2]。
4. 不要编造编号；只用资料中实际存在的内容作答。"""


def _est_tokens(text: str) -> int:
    """中文感知的 token 估算：中文通常按字 ≈1 token，其余按字符÷4 近似。

    旧的 `len(text)//4` 对纯中文低估约 4 倍，会导致上下文/历史预算形同虚设、
    甚至超过模型 context window 而被截断，故改为中英混合估算。
    """
    if not text:
        return 1
    cjk = sum(
        1
        for ch in text
        if "\u4e00" <= ch <= "\u9fff"          # CJK 汉字
        or "\u3000" <= ch <= "\u303f"          # CJK 标点/符号
        or "\uff00" <= ch <= "\uffef"          # 全角字符
    )
    other = len(text) - cjk
    return max(1, cjk + other // 4)


def _fit_history(history: list[dict], token_budget: int) -> list[dict]:
    """裁剪会话历史到 token 预算内，保留最近对话（自后向前累加）。"""
    out: list[dict] = []
    used = 0
    for m in reversed(history):
        cost = _est_tokens(m["content"]) + 8  # 每消息角色/分隔开销
        if used + cost > token_budget and out:
            break
        out.append(m)
        used += cost
    out.reverse()
    return out


def fit_context(hits: list[dict], token_budget: int) -> tuple[list[dict], bool]:
    """按 token 预算裁剪进入 LLM 的资料片段；超限时按相关度保留并标记截断。

    返回 (用于生成上下文的 hits, 是否发生了截断)。前端来源卡仍展示完整 hits（见 API 返回）。
    """
    if not hits:
        return [], False
    total = sum(_est_tokens(h["content"]) for h in hits) + 20
    if total <= token_budget:
        return hits, False
    kept: list[dict] = []
    used = 0
    for h in hits:
        cost = _est_tokens(h["content"]) + 10
        if used + cost > token_budget:
            break
        kept.append(h)
        used += cost
    return kept, len(kept) < len(hits)


def build_context(hits: list[dict]) -> str:
    # 带序号拼接，序号与前端来源卡片、正文内联引用对应
    return "\n\n---\n\n".join(f"[{i}] {h['source']}\n{h['content']}" for i, h in enumerate(hits, 1))


def build_messages(
    question: str,
    context: str,
    history: list[dict] | None = None,
    history_budget: int = 8000,
    total_budget: int | None = None,
) -> list[dict]:
    """组装 LLM 消息：system + 裁剪后的历史 + 当前问题与资料。

    N5：对「历史 + 当前资料 + system」做一次**联合总预算校验**，合计超限时
    优先压缩历史（当前资料已在 fit_context 按 context_token_budget 预裁剪），
    遵循「宁可截历史，也不截当前资料」的原则。
    """
    user_msg = f"问题：{question}\n\n资料：\n{context}"
    overhead = _est_tokens(_SERVICE_SYSTEM) + _est_tokens(user_msg) + 16
    if total_budget is None:
        # 默认联合上限：历史预算×2（历史 + 资料各按预算，但合计不超两倍预算）
        total_budget = history_budget * 2
    cap = max(0, min(history_budget, total_budget - overhead))

    msgs: list[dict] = [{"role": "system", "content": _SERVICE_SYSTEM}]
    if history:
        for m in _fit_history(history, cap):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_msg})
    return msgs


def retrieve_hits(
    question: str, top_k: int = TOP_K, dataset_ids: list[int] | None = None
) -> list[dict]:
    """执行检索，返回带来源的召回片段（供 API answered 与来源展示）。

    R-04：若只检索单个数据集且该库配置了参数覆盖，则优先使用库级阈值/top_k。
    """
    store = get_or_create_store()
    rt = runtime_settings.load()
    eff_top_k = top_k or rt["top_k"]
    eff_threshold = rt["score_threshold"]
    if dataset_ids and len(dataset_ids) == 1:
        dp = db.get_dataset_params(dataset_ids[0])
        if dp:
            if dp["top_k"] is not None:
                eff_top_k = dp["top_k"]
            if dp["score_threshold"] is not None:
                eff_threshold = dp["score_threshold"]
    return retrieve(
        store,
        question,
        top_k=eff_top_k,
        dataset_ids=dataset_ids,
        score_threshold=eff_threshold,
        mmr_lambda=rt["mmr_lambda"] if rt["mmr_enabled"] else None,
        rerank=rt["rerank_enabled"],
        rerank_model=rt["rerank_model"],
        rerank_channel=rt["rerank_channel"],
        rerank_external_url=rt["rerank_external_url"],
        rerank_external_key=rt["rerank_api_key"],
        hybrid=rt["hybrid_enabled"],
        hybrid_alpha=rt["hybrid_alpha"] if rt["hybrid_enabled"] else None,
        hybrid_method=rt["hybrid_method"],
    )


def rewrite_question(question: str, history: list[dict] | None = None) -> str:
    """R-02：基于会话历史把追问改写为独立完整问题；失败或无历史则原样返回。"""
    if not history:
        return question
    rt = runtime_settings.load()
    if not rt.get("query_rewrite"):
        return question
    try:
        client = OpenAI(api_key=rt["api_key"], base_url=rt["base_url"], timeout=15, max_retries=0)
        msgs = [{"role": "system", "content": "把用户的追问改写成一个可独立检索的完整问题，只输出改写后的问题本身，不要解释。"}]
        for m in history[-6:]:
            msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": question})
        r = client.chat.completions.create(model=rt["model"], temperature=0, messages=msgs, max_tokens=200)
        out = (r.choices[0].message.content or "").strip()
        return out[:400] if out else question
    except Exception:  # noqa: BLE001 改写失败降级为原问题
        return question


async def stream_answer_async(question: str, context: str, history: list[dict] | None = None) -> Iterator[str]:
    """异步流式生成（SSE 用）：基于 AsyncOpenAI，不在事件循环里阻塞。

    N7：原同步版 stream_answer 与它逻辑重复，且无调用方；统一只保留一个 async 实现。
    """
    rt = runtime_settings.load()
    client = _ahttp(rt["base_url"], rt["api_key"])
    msgs = build_messages(
        question,
        context,
        history,
        rt["context_token_budget"],
        total_budget=rt["context_token_budget"] * 2,
    )
    stream = await client.chat.completions.create(
        model=rt["model"],
        temperature=rt["temperature"],
        stream=True,
        messages=msgs,
    )
    async for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
