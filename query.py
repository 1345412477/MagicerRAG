"""在线问答入口：python query.py "你的问题"

流程：问题向量化 -> 相似度检索 -> 构造提示词 -> LLM 生成回答，并展示召回片段。
"""
import sys

from config import require_env
from rag.pipeline import RAGPipeline


def main():
    if len(sys.argv) < 2:
        print("用法：python query.py \"你的问题\"")
        return 1

    require_env("EMBEDDING_API_KEY")
    require_env("LLM_API_KEY")
    question = sys.argv[1]

    pipeline = RAGPipeline()
    try:
        result = pipeline.ask(question)
    finally:
        pipeline.close()

    print(f"\n问题：{result['question']}\n")
    print("=" * 60)
    print(result["answer"])
    print("=" * 60)
    print(f"\n召回片段（{len(result['hits'])} 条）：")
    for h in result["hits"]:
        print(f"  · [{h['source']}] 相关度 {h['score']}: {h['content'][:60]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())