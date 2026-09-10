"""离线建索引入口：python ingest.py

流程：加载 docs/ 文档 -> 切分 Chunk -> Embedding -> 写入 Chroma 向量库。
"""
import sys
import time

from config import require_env, VECTOR_DIR
from rag.pipeline import RAGPipeline


def main():
    require_env("EMBEDDING_API_KEY")
    pipeline = RAGPipeline()
    t0 = time.time()
    try:
        n_chunks = pipeline.build_index()
    finally:
        pipeline.close()
    print(f"建索引完成：共 {n_chunks} 个 Chunk，写入 {VECTOR_DIR}，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    sys.exit(main())