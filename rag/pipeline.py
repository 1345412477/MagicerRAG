"""RAG 编排：把离线建索引与在线问答串成完整闭环。"""
from __future__ import annotations

from langchain_chroma import Chroma

from rag.generator import answer, build_llm
from rag.ingestor import load_and_chunk
from rag.retriever import get_or_create_store, retrieve


class RAGPipeline:
    """一键式的 RAG 管道封装，屏蔽底层细节，便于脚本与后续扩展调用。"""

    def __init__(self):
        self._store: Chroma | None = None
        self._llm = None

    # ---- 离线建索引 ----
    def build_index(self, docs_dir=None, chunk_size=500, overlap=50) -> int:
        chunks = load_and_chunk(docs_dir, chunk_size=chunk_size, overlap=overlap)
        self._store = get_or_create_store(chunks)
        return len(chunks)

    # ---- 在线问答 ----
    def ensure_store(self) -> Chroma:
        if self._store is None:
            self._store = get_or_create_store()
        return self._store

    def ask(self, question: str, top_k: int = 4) -> dict:
        store = self.ensure_store()
        hits = retrieve(store, question, top_k=top_k)
        return {
            "question": question,
            "hits": hits,
            "answer": answer(build_llm(), question, hits),
        }

    def close(self):
        """释放 Chroma 连接，防止程序退出时告警。"""
        try:
            self.ensure_store()
        except Exception:
            pass