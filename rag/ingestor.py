"""文档加载与切分：把 docs 目录下的知识文档切成语义区块（Chunk）。"""
from __future__ import annotations

import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import DOCS_DIR
from rag.extractors import ALLOWED_EXTENSIONS, extract_text

# 支持的文件后缀（文本 + 办公文档/PDF/Excel/PPT/图片OCR）
_SUPPORTED_SUFFIXES = ALLOWED_EXTENSIONS


def _read_file(path: Path) -> str:
    return extract_text(path)


def load_documents(docs_dir: Path | None = None, progress_cb=None) -> list[dict]:
    """递归扫描 docs 目录，返回 [{page_content, metadata{source}}]。None 时使用配置默认目录。

    progress_cb(done, total) 每读完一个文件调用一次，用于后台任务进度上报。
    """
    docs_dir = docs_dir or DOCS_DIR
    if not docs_dir.exists():
        raise FileNotFoundError(f"知识目录不存在：{docs_dir}，请先创建并放入文档。")

    docs: list[dict] = []
    files = [p for p in docs_dir.rglob("*") if p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES]
    total = len(files)
    for i, path in enumerate(files, 1):
        docs.append(
            {
                "page_content": _read_file(path),
                "metadata": {"source": path.name},
            }
        )
        if progress_cb:
            progress_cb(i, total)
    return docs


def chunk_documents(
    docs: list[dict],
    chunk_size: int = 500,
    overlap: int = 50,
    mode: str = "markdown",
) -> list[dict]:
    """按分块模板把文档切成区块。

    R-05：对带 Markdown 标题的文本优先做「标题感知」分节（保语义边界），
    超长节再递归细分；非 Markdown / 无标题文本回退到递归切分，行为如旧。

    mode（分块模板）：
      "markdown"  默认：标题感知分节，超长递归；无标题回退递归切分。
      "recursive" 强制递归切分（不看标题边界）。
      "whole"     大段不拆：按 Markdown 节整体成块（不做长度细分），无标题整篇一块。
    """
    chunks: list[dict] = []
    for d in docs:
        text = d["page_content"]
        if mode == "recursive":
            pieces = _recursive_split(text, chunk_size, overlap)
        elif mode == "whole":
            pieces = _whole_split(text)
        elif _looks_markdown(text):
            pieces = _split_markdown_sections(text, chunk_size, overlap)
        else:
            pieces = _recursive_split(text, chunk_size, overlap)
        for i, (content, section) in enumerate(pieces):
            meta = {**d["metadata"], "chunk_index": i}
            if section:
                meta["section"] = section
            chunks.append({"page_content": content, "metadata": meta})
    return chunks


def _whole_split(text: str) -> list[tuple[str, str]]:
    """大段不拆：有标题则按节整块，无标题则整篇一块。"""
    if _looks_markdown(text):
        try:
            from langchain_text_splitters import MarkdownHeaderTextSplitter

            headers = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4"), ("#####", "h5")]
            s = MarkdownHeaderTextSplitter(headers_to_split_on=headers)
            return [
                (doc.page_content, " / ".join(str(v) for v in doc.metadata.values()))
                for doc in s.split_text(text)
            ]
        except Exception:  # noqa: BLE001
            return [(text, "")]
    return [(text, "")]


def _recursive_split(text: str, chunk_size: int, overlap: int) -> list[tuple[str, str]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "！", "？", " ", ""],
    )
    return [(p, "") for p in splitter.split_text(text)]


def _looks_markdown(text: str) -> bool:
    return bool(re.search(r"^#{1,6}\s+\S", text, re.M))


def _split_markdown_sections(text: str, chunk_size: int, overlap: int) -> list[tuple[str, str]]:
    """按 Markdown 标题分节并保留节名称；超长节再递归细分。"""
    try:
        from langchain_text_splitters import MarkdownHeaderTextSplitter

        headers = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4"), ("#####", "h5")]
        s = MarkdownHeaderTextSplitter(headers_to_split_on=headers)
        section_docs = s.split_text(text)
    except Exception:  # noqa: BLE001 标题切分不可用则回退递归
        return _recursive_split(text, chunk_size, overlap)

    out: list[tuple[str, str]] = []
    for i, doc in enumerate(section_docs):
        content = doc.page_content
        section = " / ".join(str(v) for k, v in doc.metadata.items())
        section = section or ""
        if len(content) > chunk_size * 4:
            for sub in _recursive_split(content, chunk_size, overlap):
                out.append((sub[0], section or sub[1]))
        else:
            out.append((content, section))
    return out


def load_and_chunk(docs_dir: Path | None = None, progress_cb=None, **kw) -> list[dict]:
    """一键完成 加载 + 切分，供 ingest 脚本调用。"""
    return chunk_documents(load_documents(docs_dir, progress_cb=progress_cb), **kw)
