"""切分测试：标题感知分节（R-05）与非 Markdown 回退。"""
from __future__ import annotations

from rag.ingestor import chunk_documents


def test_markdown_split_by_headings_attaches_section():
    md = "# 第一章\n这是第一章正文，较长的内容用于测试。\n\n## 1.1 小节\n这是小节内容。\n"
    docs = [{"page_content": md, "metadata": {"source": "book.md"}}]
    chunks = chunk_documents(docs, chunk_size=3000, overlap=50)
    assert len(chunks) >= 2
    # 至少一个 chunk 含小节 metadata.section
    assert any("1.1" in (c["metadata"].get("section") or "") for c in chunks)


def test_plain_text_fallback_preserves_recursive_behavior():
    raw = "短文本。" * 20
    docs = [{"page_content": raw, "metadata": {"source": "a.txt"}}]
    chunks = chunk_documents(docs, chunk_size=200, overlap=10)
    assert chunks
    assert all("section" not in c["metadata"] for c in chunks)
    assert all(c["metadata"]["source"] == "a.txt" for c in chunks)
