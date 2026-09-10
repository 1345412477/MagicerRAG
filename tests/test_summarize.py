"""R-05 超长文档分层摘要：map-reduce 编排与降级。"""
from __future__ import annotations

from types import SimpleNamespace

from rag.summarize import _split_for_summarize, maybe_summary_chunks, summarize_document


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="核心要点摘要"))])


class _FakeClient:
    def __init__(self):
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_split_into_budgeted_pieces():
    text = "\n\n".join(["段落" * 200 for _ in range(50)])
    pieces = _split_for_summarize(text, budget=6000)
    assert len(pieces) >= 2


def test_short_doc_returns_none():
    assert summarize_document("短文", _FakeClient(), "m", threshold=20000) is None


def test_long_doc_map_reduce_calls_llm():
    client = _FakeClient()
    text = "内容" * 12000  # 超过 20000 字符
    result = summarize_document(text, client, "m", threshold=20000, map_budget=6000)
    assert result == "核心要点摘要"
    assert client.completions.calls >= 2  # map + reduce


def test_maybe_summary_chunks_disabled_and_enabled():
    chunks = [{"page_content": "内容" * 5000, "metadata": {"source": "big.md", "chunk_index": 0}}]
    # 关闭 -> 空
    assert maybe_summary_chunks(chunks, 1, _FakeClient(), "m", enabled=False) == []
    # 开启 + 超长 -> 生成 1 个摘要分块
    out = maybe_summary_chunks(chunks, 1, _FakeClient(), "m", enabled=True, threshold=10000)
    assert len(out) == 1
    assert out[0]["metadata"]["kind"] == "summary"
