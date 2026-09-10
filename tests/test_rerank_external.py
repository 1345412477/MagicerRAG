"""N4 验证：外部 OpenAI 兼容 /v1/rerank 通道（重排 + 安全降级）。"""
import json
import unittest
from unittest import mock

from rag.rerank import rerank_hits

HITS = [
    {"content": "第一段完全不相关", "score": 0.30, "source": "a.txt"},
    {"content": "第二段比较相关", "score": 0.60, "source": "b.txt"},
    {"content": "第三段最相关", "score": 0.90, "source": "c.txt"},
]


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


class TestExternalRerank(unittest.TestCase):
    def _mock(self, payload):
        return mock.patch(
            "rag.rerank.urllib.request.urlopen", return_value=_Resp(payload)
        )

    def test_reorder_by_relevance(self):
        # 原始第 1 段(0.30)改为最高相关 -> 应排到最前
        res = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}
        with self._mock(res):
            out = rerank_hits("q", list(HITS), 3, "jina-reranker-v2",
                              channel="external", external_url="https://x/v1",
                              external_key="tok")
        self.assertEqual(out[0]["content"], "第二段比较相关")
        self.assertEqual(out[1]["content"], "第一段完全不相关")
        # 分数应被归一化到 0..1
        self.assertLessEqual(out[0]["score"], 1.0)

    def test_fallback_when_no_results(self):
        with self._mock({}):
            out = rerank_hits("q", list(HITS), 3, "m",
                              channel="external", external_url="https://x/v1")
        # 无结果 -> 保持原序
        self.assertEqual([h["content"] for h in out], [h["content"] for h in HITS])

    def test_degrade_on_connection_error(self):
        with mock.patch("rag.rerank.urllib.request.urlopen", side_effect=OSError("conn refused")):
            out = rerank_hits("q", list(HITS), 3, "m",
                              channel="external", external_url="https://x/v1")
        # 外部失败 -> 降级为原序，不抛错
        self.assertEqual([h["content"] for h in out], [h["content"] for h in HITS])

    def test_local_channel_no_model_keeps_order(self):
        # 未安装 sentence-transformers -> local 通道原样返回
        with mock.patch("rag.rerank._load_reranker", return_value=None):
            out = rerank_hits("q", list(HITS), 3, "BAAI/bge-reranker-base", channel="local")
        self.assertEqual([h["content"] for h in out], [h["content"] for h in HITS])


if __name__ == "__main__":
    unittest.main(verbosity=2)