"""轻量后台任务管理（内存，适配 10 人量级）：用于索引重建等耗时操作。"""
from __future__ import annotations

import threading
import time
import uuid

_tasks: dict[str, dict] = {}
_lock = threading.Lock()


def create(kind: str, desc: str) -> str:
    tid = uuid.uuid4().hex[:16]
    with _lock:
        _tasks[tid] = {
            "id": tid,
            "kind": kind,
            "desc": desc,
            "status": "queued",  # queued | running | done | error
            "progress": 0,
            "detail": "",
            "created_at": time.time(),
        }
    return tid


def update(tid: str, **kw):
    with _lock:
        t = _tasks.get(tid)
        if t:
            t.update(kw)


def get(tid: str) -> dict | None:
    with _lock:
        t = _tasks.get(tid)
        return dict(t) if t else None


def list_recent(limit: int = 50) -> list[dict]:
    with _lock:
        return sorted(_tasks.values(), key=lambda x: -x["created_at"])[:limit]


def cleanup(ttl: int = 6 * 3600):
    now = time.time()
    with _lock:
        dead = [
            k
            for k, t in _tasks.items()
            if t["status"] in ("done", "error") and now - t["created_at"] > ttl
        ]
        for k in dead:
            _tasks.pop(k, None)
