"""向量库与检索：基于 Chroma 持久化 + 相似度召回。"""
from __future__ import annotations

import threading
import uuid

from langchain_chroma import Chroma
from openai import OpenAI

from config import (
    RERANK_MODEL,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    SCORE_THRESHOLD,
    TOP_K,
    VECTOR_DIR,
)
from rag.hybrid import BM25, fuse
from rag.rerank import rerank_hits

import logging

_logger = logging.getLogger("magicerrag.retriever")


class _RWLock:
    """简单公平读写锁：读读可并发，写写/写读互斥；写模式可重入（同一线程嵌套再拿）。

    不加 write 优先，读者较多时写可能被延迟，但对本应用（重建低频）足够安全。
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer_tid: int | None = None
        self._write_depth = 0

    def acquire_write(self) -> None:
        tid = threading.get_ident()
        with self._cond:
            if self._writer_tid == tid:
                self._write_depth += 1
                return
            while self._writer_tid is not None or self._readers > 0:
                self._cond.wait()
            self._writer_tid = tid
            self._write_depth = 1

    def release_write(self) -> None:
        with self._cond:
            self._write_depth -= 1
            if self._write_depth == 0:
                self._writer_tid = None
                self._cond.notify_all()

    def acquire_read(self) -> None:
        tid = threading.get_ident()
        with self._cond:
            # 自身已持写锁时，读可直接通过（同一线程），避免死锁
            while self._writer_tid is not None and self._writer_tid != tid:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    # 上下文管理器
    def reader(self):
        class _Ctx:
            def __enter__(v):
                self.acquire_read()
            def __exit__(v, *a):
                self.release_read()
        return _Ctx()

    def writer(self):
        class _Ctx:
            def __enter__(v):
                self.acquire_write()
            def __exit__(v, *a):
                self.release_write()
        return _Ctx()

# 模块级单例：复用 embedding 客户端与持久化 store，避免每次请求新建连接/客户端
# 造成的资源开销与并发打开同一持久目录的锁竞争。
# N2：用「读写锁」替代单一 RLock——查询走读模式可并发，只有重建(clear+add)走写模式独占，
#      从而解决多用户并发提问被同一把锁串行化的问题；写模式可重入（重建内再拿 store）。
_embeddings = None
_embeddings_lock = threading.Lock()
_store: Chroma | None = None
_rw = _RWLock()
# 混合检索的文本语料 / BM25 索引缓存：dataset_id 子集 -> 语料 / BM25 对象
_key_of = lambda ds: ("all",) if not ds else ("ds", tuple(sorted(ds)))
_corpus_cache: dict[tuple, list[dict]] = {}
_bm_cache: dict[tuple, "BM25"] = {}
_corpus_lock = threading.Lock()
_corpus_max = 64

# 阿里云百炼 embedding 单次批量上限
_DASH_BATCH = 10


class DashScopeCompatibleEmbeddings:
    """兼容 Chroma 的向量化适配器。

    基于原生 OpenAI 客户端直接构造请求，规避 langchain-openai 与部分国产
    服务商（如阿里云百炼 DashScope）在批量请求格式上的不兼容。
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def _embed(self, input_value: str | list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=input_value)
        return [item.embedding for item in resp.data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # 阿里云百炼(DashScope)单次 embedding 批量上限为 10，超出返回
        # InvalidParameter(batch size)；按条分批调用后拼接，兼容任意 chunk 数量。
        out: list[list[float]] = []
        for i in range(0, len(texts), _DASH_BATCH):
            out.extend(self._embed(texts[i : i + _DASH_BATCH]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)[0]


def build_embeddings():
    """Embedding 客户端（建索引与查询必须一致），模块级单例复用。"""
    global _embeddings
    if _embeddings is None:
        with _embeddings_lock:
            if _embeddings is None:
                _embeddings = DashScopeCompatibleEmbeddings(
                    api_key=EMBEDDING_API_KEY,
                    base_url=EMBEDDING_BASE_URL,
                    model=EMBEDDING_MODEL,
                )
    return _embeddings


def get_or_create_store(docs: list[dict] | None = None) -> Chroma:
    """获取向量库：返回共享单例 store；docs 非空时用于重建（替换为该分片）。

    兼容历史调用：传入 docs 即视为「全量重建」入口（由 kb_service 驱动）。
    """
    global _store
    if docs:
        with _rw.writer():
            embeddings = build_embeddings()
            store = Chroma.from_documents(
                documents=_to_langchain(docs),
                embedding=embeddings,
                persist_directory=str(VECTOR_DIR),
            )
            _store = store
            _invalidate_caches()
            return store
    return get_shared_store()


def get_shared_store() -> Chroma:
    global _store
    if _store is None:
        with _rw.writer():
            if _store is None:
                _store = Chroma(
                    embedding_function=build_embeddings(),
                    persist_directory=str(VECTOR_DIR),
                )
    return _store


def rebuild_dataset(dataset_id: int, chunks: list[dict]) -> int:
    """原子重建某数据集：清空该库旧向量后写入新分片，期间用读写锁写模式隔离并发重建与查询。

    直接操作共享 collection（而非重开持久客户端），避免多实例并发写同一目录；
    N1：重建后使语料/BM25 缓存失效，保证下次混合检索读到最新数据。
    """
    with _rw.writer():
        store = get_shared_store()
        store._collection.delete(where={"dataset_id": str(dataset_id)})
        if chunks:
            texts = [c["page_content"] for c in chunks]
            vectors = build_embeddings().embed_documents(texts)
            store._collection.add(
                embeddings=vectors,
                documents=texts,
                metadatas=[c["metadata"] for c in chunks],
                ids=[f"{dataset_id}::{uuid.uuid4().hex}" for _ in chunks],
            )
        _invalidate_caches()
    return len(chunks)


def _to_langchain(docs: list[dict]) -> list:
    from langchain_core.documents import Document

    return [Document(page_content=d["page_content"], metadata=d["metadata"]) for d in docs]


def retrieve(
    store: Chroma,
    question: str,
    top_k: int = TOP_K,
    dataset_ids: list[int] | None = None,
    score_threshold: float | None = None,
    mmr_lambda: float | None = None,
    rerank: bool = False,
    rerank_model: str | None = None,
    rerank_channel: str = "local",
    rerank_external_url: str = "",
    rerank_external_key: str = "",
    hybrid: bool = False,
    hybrid_alpha: float | None = None,
    hybrid_method: str = "weighted",
    fetch_mult: int = 2,
) -> list[dict]:
    """按相似度召回 TopK 片段，并过滤过低相关度。

    dataset_ids 非空时仅在该数据集子集内检索（按 metadata 过滤）。
    mmr_lambda 非空时启用多样性（MMR 重排序）；rerank=True 时再用 Cross-Encoder 重排。
    默认走纯稠密路径，行为与旧版一致。
    """
    threshold = SCORE_THRESHOLD if score_threshold is None else score_threshold
    fetch_k = max(top_k * fetch_mult, top_k)
    kw: dict = {"k": fetch_k}
    if dataset_ids:
        kw["filter"] = {"dataset_id": {"$in": [str(d) for d in dataset_ids]}}

    if hybrid:
        hits = _hybrid_retrieve(store, question, dataset_ids, top_k, threshold, hybrid_alpha, hybrid_method)
    elif mmr_lambda is not None and 0 <= mmr_lambda <= 1:
        hits = _mmr_retrieve(store, question, kw, fetch_k, top_k, threshold, mmr_lambda)
    else:
        # 与重建隔离：查询走读模式，避免并发 clear+add 与查询竞态读到中间态；读模式可并发
        with _rw.reader():
            results = store.similarity_search_with_relevance_scores(question, **kw)
        hits = []
        for doc, score in results:
            if score < threshold:
                continue
            hits.append(
                {
                    "content": doc.page_content,
                    "score": round(score, 4),
                    "source": doc.metadata.get("source", "未知来源"),
                }
            )
            if len(hits) >= fetch_k:
                break

    if rerank:
        hits = rerank_hits(
            question,
            hits,
            top_k,
            rerank_model or RERANK_MODEL,
            channel=rerank_channel,
            external_url=rerank_external_url,
            external_key=rerank_external_key,
        )
    return hits[:top_k]


def _invalidate_caches():
    """重建/清库后使语料与 BM25 索引缓存失效，保证下次混合检索读到最新数据（N1）。"""
    with _corpus_lock:
        _corpus_cache.clear()
        _bm_cache.clear()


def _corpus_for(store: Chroma, dataset_ids: list[int] | None) -> list[dict]:
    """读取并缓存（按数据集子集）语料供 BM25 使用；读取失败返回空列表。"""
    cache_key = _key_of(dataset_ids)
    with _corpus_lock:
        if cache_key in _corpus_cache:
            return _corpus_cache[cache_key]
    try:
        res = store._collection.get(include=["documents", "metadatas"])
    except Exception:  # noqa: BLE001 集合为空或读取失败
        return []
    docs: list[dict] = []
    try:
        ds_set = set(dataset_ids) if dataset_ids else None
        for doc, meta in zip(res.get("documents", []) or [], res.get("metadatas", []) or []):
            if ds_set is not None:
                did = (meta or {}).get("dataset_id")
                if did is None or int(did) not in ds_set:
                    continue
            docs.append({"content": doc, "source": (meta or {}).get("source", "未知来源")})
    except Exception:  # noqa: BLE001
        return []
    with _corpus_lock:
        if len(_corpus_cache) >= _corpus_max:
            _corpus_cache.clear()
            _bm_cache.clear()
        _corpus_cache[cache_key] = docs
    return docs


def _bm_index_for(dataset_ids: list[int] | None, corpus: list[dict]) -> "BM25":
    """取（或构建缓存）某数据集子集的 BM25 稀疏索引，避免每次提问全量重建（N1）。"""
    cache_key = _key_of(dataset_ids)
    with _corpus_lock:
        bm = _bm_cache.get(cache_key)
        if bm is not None:
            return bm
    bm = BM25(corpus)
    with _corpus_lock:
        _bm_cache[cache_key] = bm
    return bm


def _hybrid_retrieve(store, question, dataset_ids, top_k, threshold, alpha, method="weighted"):
    """稠密 + BM25 融合；alpha 越靠近 1 越偏语义，越靠近 0 越偏字面。method 见 fuse()。"""
    alpha = 0.7 if alpha is None else alpha
    kw = {"k": max(top_k * 4, top_k)}
    if dataset_ids:
        kw["filter"] = {"dataset_id": {"$in": [str(d) for d in dataset_ids]}}
    dense: list[dict] = []
    try:
        with _rw.reader():
            results = store.similarity_search_with_relevance_scores(question, **kw)
        for doc, score in results:
            if score < threshold:
                continue
            dense.append({
                "content": doc.page_content,
                "score": round(score, 4),
                "source": doc.metadata.get("source", "未知来源"),
            })
            if len(dense) >= kw["k"]:
                break
    except Exception:  # noqa: BLE001
        dense = []

    corpus = _corpus_for(store, dataset_ids)
    lexical: list[dict] = []
    if corpus:
        bm = _bm_index_for(dataset_ids, corpus)
        scores = bm.score(question)
        mx = max(scores) if scores else 0.0
        if mx > 0:
            ranked = sorted(range(len(corpus)), key=lambda i: -scores[i])
            for i in ranked[: kw["k"]]:
                lexical.append({
                    "content": corpus[i]["content"],
                    "score": round(scores[i] / mx, 4),
                    "source": corpus[i]["source"],
                })
    out = fuse(dense, lexical, alpha=alpha, top_k=top_k, method=method)
    # 融合后再按相关度下限聚焦：剔除混入的低相关碎片，让参考资料更贴近问题；
    # 若全部被滤则回退原始结果，避免"查不到/无参考资料"（分数尺度不稳定，weighted 下生效，rrf 下自然保底）。
    kept = [h for h in out if h["score"] >= threshold]
    return kept if kept else out


def _mmr_retrieve(
    store: Chroma,
    question: str,
    kw: dict,
    fetch_k: int,
    top_k: int,
    threshold: float,
    mmr_lambda: float,
) -> list[dict]:
    """多样性召回：基于 collection 原生查询取候选 + 向量，再算 MMR 得分重排。"""
    try:
        query_emb = build_embeddings().embed_query(question)
        with _rw.reader():
            res = store._collection.query(
                query_embeddings=[query_emb],
                n_results=min(fetch_k, max(store._collection.count(), 0)),
                where=kw.get("filter"),
                include=["documents", "metadatas", "distances", "embeddings"],
            )
    except Exception:  # noqa: BLE001 MMR 失败回退到稠密
        with _rw.reader():
            results = store.similarity_search_with_relevance_scores(question, **kw)
        hits = []
        for doc, score in results:
            if score < threshold:
                continue
            hits.append(
                {"content": doc.page_content, "score": round(score, 4), "source": doc.metadata.get("source", "未知来源")}
            )
            if len(hits) >= top_k:
                break
        return hits

    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    embs = res.get("embeddings", [[]])[0]
    cands: list[dict] = []
    for doc, meta, dist in zip(docs, metas, dists):
        # cosine distance ∈ [0,2]，换算为相关度
        score = 1 - min(dist, 1.0)
        if score < threshold:
            continue
        cands.append(
            {
                "content": doc,
                "score": round(score, 4),
                "source": (meta or {}).get("source", "未知来源"),
            }
        )
    if not cands:
        return []
    return _mmr_select(query_emb, embs, cands, top_k, mmr_lambda)


def _mmr_select(query_emb, doc_embs, cands, top_k, lam):
    """经典 MMR：λ*sim(Q,D) - (1-λ)*max_{S} sim(D,S)，选多样性且相关的片段。"""
    n = len(cands)
    import math

    def _dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def _norm(v):
        s = math.sqrt(sum(x * x for x in v))
        return s if s else 1.0

    qn = _norm(query_emb)
    qsim = [_dot(query_emb, doc_embs[i]) / qn / _norm(doc_embs[i]) for i in range(n)]
    unselected = set(range(n))
    selected: list[int] = []
    while unselected and len(selected) < top_k:
        best_i, best_v = -1, float("-inf")
        for i in unselected:
            sim_q = qsim[i]
            sim_s = 0.0
            if selected:
                sim_s = max(
                    _dot(doc_embs[i], doc_embs[j]) / _norm(doc_embs[i]) / _norm(doc_embs[j])
                    for j in selected
                )
            mmr = lam * sim_q - (1 - lam) * sim_s
            if mmr > best_v:
                best_v, best_i = mmr, i
        selected.append(best_i)
        unselected.discard(best_i)
    return [cands[i] for i in selected]


def clear_dataset(dataset_id: int):
    """删除向量库中某数据集的所有向量（Chroma 单集合 + metadata 过滤）。

    N2：写模式独占，避免与并发查询竞态；N1：删除后使语料/BM25 缓存失效。
    """
    store = get_or_create_store()
    try:
        with _rw.writer():
            collection = store._collection  # 原生 chromadb Collection
            collection.delete(where={"dataset_id": str(dataset_id)})
            _invalidate_caches()
    except Exception as e:  # noqa: BLE001
        # 集合尚不存在或为空属正常，忽略；其余删除失败需告警以便排查
        if "does not exist" not in str(e).lower():
            _logger.warning("clear_dataset 清理 dataset=%s 失败: %s", dataset_id, e)
