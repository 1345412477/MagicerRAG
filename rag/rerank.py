"""可选的重排序（Rerank）模块。

R-03：在稠密召回后，按「问题-片段」相关性重排并归一化分数。
支持两种通道（N4）：
  - channel="local"：本机 CrossEncoder（如 bge-reranker，需安装 sentence-transformers）；
  - channel="external"：OpenAI 兼容的外部 /v1/rerank 服务（即开即用、无需重依赖）。
任一通道不可用或调用失败都会**安全降级**为原召回顺序，不阻塞主链路。
默认关闭（RERANK_ENABLED=0），管理后端口可开启。
"""
from __future__ import annotations

import json
import urllib.request

_reranker = None
_loaded = False


def _load_reranker(model_name: str):
    """加载 CrossEncoder；失败返回 None（下层安全回退到稠密排序）。"""
    global _reranker, _loaded
    if _loaded:
        return _reranker
    _loaded = True
    try:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(model_name, max_length=512)
    except Exception:  # noqa: BLE001 未安装或加载失败皆可回退
        _reranker = None
    return _reranker


def _external_rerank(
    question: str, hits: list[dict], top_k: int, model_name: str, base_url: str, api_key: str
) -> list[dict]:
    """OpenAI 兼容的外部 /v1/rerank：POST {base}/rerank，body 带 model/query/documents/top_n。

    请求体兼容主流 OpenAI 兼容 rerank（Jina / Cohere 风格）。返回体容忍多种字段：
    `results` 或 `data`，每项含 `index` + `relevance_score`（或 `score`）。
    调用失败或超时抛异常，由上层降级为原召回顺序。
    """
    documents = [h.get("content", "")[:512] for h in hits]
    body = {"model": model_name, "query": question, "documents": documents, "top_n": len(documents)}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/rerank",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 外部服务地址由管理员配置
        payload = json.loads(resp.read().decode("utf-8"))
    results = payload.get("results") or payload.get("data") or []
    scored: list[tuple[float, dict]] = []
    for r in results:
        idx = r.get("index")
        if isinstance(idx, str):
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                continue
        if idx is None or not (0 <= idx < len(hits)):
            continue
        score = r.get("relevance_score", r.get("score", 0.0))
        try:
            scored.append((float(score), hits[idx]))
        except (TypeError, ValueError):
            continue
    if not scored:
        return hits
    # 等分时保持原召回顺序（稳定排序），避免外部服务打乱既有次序。
    order = {id(h): i for i, h in enumerate(hits)}
    ranked = [h for _, h in sorted(scored, key=lambda t: (-t[0], order[id(t[1])]))]
    arr = [s for s, _ in sorted(scored, key=lambda t: (-t[0], order[id(t[1])]))]
    mn, mx = min(arr), max(arr)
    by_content = {h.get("content", ""): h for h in hits}
    out: list[dict] = []
    for s, h in zip(arr, ranked):
        h = dict(h)
        h["score"] = round((s - mn) / (mx - mn), 4) if mx > mn else h.get("score", 0.0)
        out.append(by_content.get(h["content"], h))
    return out[:top_k]


def rerank_hits(
    question: str,
    hits: list[dict],
    top_k: int,
    model_name: str,
    channel: str = "local",
    external_url: str = "",
    external_key: str = "",
) -> list[dict]:
    """按相关性重排并截断到 top_k；无可用通道/失败时原样返回（保持稠密顺序）。

    N4：新增 channel="external" 分支 —— 走 OpenAI 兼容 /v1/rerank；未配置或调用失败时
    降级回退（优先尝试本机模型，否则原序），确保重排开启后不因外部服务问题而报错。
    """
    if not hits:
        return hits
    if channel == "external":
        if external_url:
            try:
                return _external_rerank(question, hits, top_k, model_name, external_url, external_key)
            except Exception:  # noqa: BLE001 外部通道失败安全降级
                return hits[:top_k]
        # 显式选择外部但未配地址：回退本机模型。
    model = _load_reranker(model_name)
    if model is None:
        return hits[:top_k]
    try:
        pairs = [(question, h.get("content", "")[:512]) for h in hits]
        scores = model.predict(pairs).tolist()
    except Exception:  # noqa: BLE001 预测失败则回退
        return hits[:top_k]
    order = {id(h): i for i, h in enumerate(hits)}
    ranked = sorted(zip(scores, hits), key=lambda x: (-x[0], order[id(x[1])]))
    # 归一化到 0..1，便于前端来源卡展示相关度
    arr = [s for s, _ in ranked]
    mn, mx = min(arr), max(arr)
    out: list[dict] = []
    for s, h in ranked:
        h = dict(h)
        h["score"] = round((s - mn) / (mx - mn), 4) if mx > mn else h["score"]
        out.append(h)
    return out[:top_k]
