"""面向网页工作台的服务层：把现有 RAG 管道封装成便于 API 调用的形态，并支持流式生成。"""
from __future__ import annotations

import json
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


def _message_text(msg) -> str:
    """一次对话消息正文：content 优先，空则回退 reasoning_content（兼容推理型模型）。"""
    t = getattr(msg, "content", None) or ""
    return t or (getattr(msg, "reasoning_content", None) or "")


def _extract_json_array(text: str) -> list | None:
    """从 LLM 输出中提取 JSON 字符串数组。直接解析优先；推理型（reasoning_content）
    常把数组嵌在叙述里，则按括号配平截取第一个 `[...]` 再解析。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(t)
        return data if isinstance(data, list) else None
    except Exception:  # noqa: BLE001 走宽松提取
        pass
    start = t.find("[")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "[":
            depth += 1
        elif t[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    cand = json.loads(t[start:i + 1])
                    return cand if isinstance(cand, list) else None
                except Exception:  # noqa: BLE001 截取仍非法则放弃
                    return None
    return None

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


async def stream_answer_async(question: str, context: str, history: list[dict] | None = None) -> Iterator[tuple[str, str]]:
    """异步流式生成（SSE 用）：基于 AsyncOpenAI，不在事件循环里阻塞。

    每个产出一组 (reasoning, content)。推理型模型（如 deepseek-flash）分别流式回吐
    思考链（reasoning_content）与最终正文（content）；普通模型 reasoning 恒为空。
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
            d = chunk.choices[0].delta
            reason = getattr(d, "reasoning_content", None) or ""
            content = d.content or ""
            if reason or content:
                yield reason, content


# W1：推荐追问的兜底问题（LLM 生成失败/被关时使用，与历史前端硬编码一致）
_FALLBACK_SUGGESTIONS = [
    "帮我总结一下这份资料",
    "我该重点关注哪些内容？",
    "根据这些资料生成一份学习笔记",
]


async def recommend_questions_async(question: str, answer: str, hits: list[dict]) -> list[str]:
    """W1：基于用户提问、助手回答与命中资料，生成 3 个可点击的推荐追问。

    单次 LLM 调用；任何失败或非法输出都回退到《FALLBACK_SUGGESTIONS》，绝不影响主回答。
    """
    rt = runtime_settings.load()
    if not rt["api_key"] or not rt["model"]:
        return list(_FALLBACK_SUGGESTIONS)
    ctx = "\n".join(
        f"- {h.get('source', '')}: {(h.get('content') or '')[:150]}"
        for h in (hits or [])[:5]
    )
    sys_p = (
        "你是知识问答助手。请基于用户的提问、助手已给出的回答和你看到的参考资料，"
        "生成用户接下来最可能继续追问的 3 个问题。"
        "只输出一个 JSON 字符串数组（如 [\"q1\",\"q2\",\"q3\"]），不要包含任何解释或其他文字。"
    )
    user_p = f"用户提问：{question}\n\n助手回答：{answer[:400]}\n\n参考资料：\n{ctx}"
    try:
        client = _ahttp(rt["base_url"], rt["api_key"])
        resp = await client.chat.completions.create(
            model=rt["model"],
            temperature=0.3,
            max_tokens=512,
            messages=[
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_p},
            ],
        )
        raw = _message_text(resp.choices[0].message).strip()
        data = _extract_json_array(raw)
        if not isinstance(data, list):
            return list(_FALLBACK_SUGGESTIONS)
        out = [str(q).strip() for q in data if isinstance(q, str) and q.strip()][:3]
        return out if out else list(_FALLBACK_SUGGESTIONS)
    except Exception:  # noqa: BLE001 推荐失败回退兜底，不阻塞主回答
        return list(_FALLBACK_SUGGESTIONS)
