"""知识库服务：按数据集扫描上传目录并重建向量索引、删除库清理。"""
from __future__ import annotations

import logging
import shutil
from collections import Counter
from pathlib import Path

from config import EMBEDDING_MODEL, UPLOAD_DIR
from openai import OpenAI
from rag.extractors import LEGACY_UNSUPPORTED, allowed_ext_label, extract_text
from rag.ingestor import load_and_chunk
from rag.retriever import clear_dataset, rebuild_dataset
from rag.summarize import maybe_summary_chunks
from .. import db, runtime_settings

_logger = logging.getLogger("magicerrag.kb")


def dataset_upload_dir(dataset_id: int) -> Path:
    return UPLOAD_DIR / str(dataset_id)


def rebuild_index(
    dataset_id: int, upload_dir: Path | None = None, progress_cb=None
) -> int:
    """重建指定数据集的向量索引：原子地清空该库旧向量并全量重建该库分片。

    progress_cb(percent:int, detail:str) 用于后台任务上报进度；无则忽略。
    """
    upload_dir = upload_dir or dataset_upload_dir(dataset_id)
    if not upload_dir.exists() or not any(upload_dir.iterdir()):
        clear_dataset(dataset_id)  # 目录为空：清除该库旧索引
        _sync_parse_statuses(dataset_id, upload_dir)
        db.set_setting("index_embedding_model", EMBEDDING_MODEL)
        if progress_cb:
            progress_cb(100, "无文件，已清空索引")
        return 0

    if progress_cb:
        progress_cb(2, "正在读取与提取文本…")

    def _file_progress(done, total):
        if progress_cb and total:
            progress_cb(2 + int(done / total * 78), f"提取文件 {done}/{total}")

    # P0-1：按该知识库的门类分块参数（split_mode/chunk_size/overlap）透传切分；未设置为空则用默认
    chunk_kw: dict[str, object] = {}
    dp = db.get_dataset_params(dataset_id)
    if dp:
        if dp["split_mode"]:
            chunk_kw["mode"] = dp["split_mode"]
        if dp["chunk_size"]:
            chunk_kw["chunk_size"] = dp["chunk_size"]
        if dp["overlap"] is not None:
            chunk_kw["overlap"] = dp["overlap"]
    chunks = load_and_chunk(upload_dir, progress_cb=_file_progress, **chunk_kw)
    # 给每个 chunk 打上数据集标记，供检索按库过滤与按库清空
    for c in chunks:
        c["metadata"]["dataset_id"] = str(dataset_id)

    if progress_cb:
        progress_cb(85, f"已提取 {len(chunks)} 个区块，正在生成向量…")
    _sync_parse_statuses(dataset_id, upload_dir, chunks)

    # R-05：超长文档分层摘要（可选）。失败即回退，不阻塞索引。
    rt = runtime_settings.load()
    if rt.get("long_doc_summary"):
        try:
            client = OpenAI(api_key=rt["api_key"], base_url=rt["base_url"], timeout=30, max_retries=0)
            summary_chunks = maybe_summary_chunks(
                chunks, dataset_id, client, rt["model"],
                enabled=True, threshold=rt.get("long_doc_chars", 20000),
            )
            if summary_chunks:
                chunks.extend(summary_chunks)
                if progress_cb:
                    progress_cb(92, f"已生成 {len(summary_chunks)} 个摘要分块")
        except Exception as e:  # noqa: BLE001
            _logger.warning("超长文档摘要生成失败，已回退（不阻塞索引）: %s", e)

    n = rebuild_dataset(dataset_id, chunks)
    # 记录本次所用 Embedding，供管理台检测「索引模型 vs 当前 Embedding」是否一致
    db.set_setting("index_embedding_model", EMBEDDING_MODEL)
    if progress_cb:
        progress_cb(100, f"重建完成，共 {n} 个区块")
    return n


def _sync_parse_statuses(dataset_id: int, upload_dir: Path, chunks: list[dict] | None = None):
    """按重建结果回填每个文件的解析状态（ok/empty/unsupported/pending）。"""
    counts: Counter[str] = Counter()
    if chunks:
        for c in chunks:
            counts[c["metadata"].get("source", "")] += 1
    for f in db.list_kb_files(dataset_id):
        ext = Path(f["filename"]).suffix.lower()
        if ext in LEGACY_UNSUPPORTED:
            # 老二进制格式：实际提取一次正文，能取出文本才标 ok，否则标 unsupported
            try:
                text = extract_text(upload_dir / f["filename"])
            except Exception:  # noqa: BLE001
                text = ""
            status = "ok" if text.strip() else "unsupported"
        elif counts.get(f["filename"], 0) == 0:
            status = "empty"
        else:
            status = "ok"
        db.set_kb_file_parse_status(f["id"], status)


def clear_vectors_for(dataset_id: int):
    """删库时清理向量库中该数据集的向量。"""
    clear_dataset(dataset_id)


def delete_dataset_files(dataset_id: int):
    """删库时清理上传目录下该库的磁盘文件。"""
    upload_dir = dataset_upload_dir(dataset_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)


def cleanup_dataset(dataset_id: int):
    """彻底清理某库的磁盘上传目录与向量库（供删库/删用户用）。"""
    delete_dataset_files(dataset_id)
    clear_vectors_for(dataset_id)
