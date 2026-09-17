"""SQLite 数据层：账号 / 会话 / 消息 / 知识库 文件的初始化与访问。

使用 Python 标准库 sqlite3，避免额外 ORM 依赖；对内封装所有 SQL。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from config import DB_PATH

# 全局统一采用 UTC+8（东八区）时间：所有入库时间戳与统计分组均以该时区为基准
TZ8 = timezone(timedelta(hours=8))

# sqlite3 默认连接不可跨线程共享，用本地线程持有连接
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,            -- personal | team
    owner_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_members (
    user_id INTEGER NOT NULL,
    dataset_id INTEGER NOT NULL,
    role TEXT NOT NULL,            -- owner | manager | member
    PRIMARY KEY (user_id, dataset_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '新会话',
    dataset_id INTEGER,             -- N8: 会话绑定知识库；NULL=不限（检索全部有权限库）
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,          -- user | assistant
    content TEXT NOT NULL,
    hits TEXT,                   -- assistant 消息的召回片段(JSON)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'   -- pending | indexed
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,   -- 仅存 sha256，不落明文
    user_id INTEGER NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- ===== v0.5：迭代优化新增表 =====
-- 数据集级检索参数（覆盖全局阈值 / 召回条数）：
CREATE TABLE IF NOT EXISTS dataset_params (
    dataset_id INTEGER PRIMARY KEY,
    score_threshold REAL,          -- NULL = 用全局默认
    top_k INTEGER,                 -- NULL = 用全局默认
    split_mode TEXT,               -- markdown | recursive | whole；NULL = 用全局默认
    chunk_size INTEGER,            -- NULL = 用全局默认
    overlap INTEGER,               -- NULL = 用全局默认
    created_at TEXT NOT NULL
);
-- 只读分享：会话导出为带权限校验的分享链接（token 仅存哈希）
CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL
);
-- 管理操作审计日志
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    username TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    """返回东八区（UTC+8）时间戳（无时区后缀，前端按墙钟直接展示即全局统一为 +8）。"""
    return datetime.now(TZ8).strftime("%Y-%m-%dT%H:%M:%S")


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
        _local.conn = conn
    return _local.conn


def _migrate(conn: sqlite3.Connection):
    """轻量字段迁移：旧库新增列，便于已有部署平滑升级（新列有默认值）。"""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "is_active" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    fcolumns = {r["name"] for r in conn.execute("PRAGMA table_info(kb_files)")}
    if "dataset_id" not in fcolumns:
        conn.execute(
            "ALTER TABLE kb_files ADD COLUMN dataset_id INTEGER NOT NULL DEFAULT 0"
        )
    if "parse_status" not in fcolumns:
        # 解析状态：ok=已解析 | empty=无文本 | unsupported=暂不支持解析 | pending=待解析
        conn.execute(
            "ALTER TABLE kb_files ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'ok'"
        )
    ucolumns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "max_sessions" not in ucolumns:
        conn.execute("ALTER TABLE users ADD COLUMN max_sessions INTEGER NOT NULL DEFAULT 50")
    if "max_kb_mb" not in ucolumns:
        conn.execute("ALTER TABLE users ADD COLUMN max_kb_mb INTEGER NOT NULL DEFAULT 200")
    # dataset_params 补分块列（老库无此表则 CREATE 已含）
    pcol = {r["name"] for r in conn.execute("PRAGMA table_info(dataset_params)")}
    if "split_mode" not in pcol:
        conn.execute("ALTER TABLE dataset_params ADD COLUMN split_mode TEXT")
        conn.execute("ALTER TABLE dataset_params ADD COLUMN chunk_size INTEGER")
        conn.execute("ALTER TABLE dataset_params ADD COLUMN overlap INTEGER")
    # N8：会话绑定知识库。NULL=不绑定（检索全部有权限库）；旧会话迁移后保持 NULL。
    scol = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "dataset_id" not in scol:
        conn.execute("ALTER TABLE sessions ADD COLUMN dataset_id INTEGER")
    # 阶段20：会话消息补充推理链（思考过程）。旧消息迁移后为空。
    mcol = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if "reasoning" not in mcol:
        conn.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT")
    if "images" not in mcol:
        conn.execute("ALTER TABLE messages ADD COLUMN images TEXT")


@contextmanager
def get_conn():
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------- 用户 ----------
def create_user(username: str, password_hash: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
            (username, password_hash, utcnow()),
        )
        return cur.lastrowid


def get_user_by_name(username: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def get_user(id_: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (id_,)).fetchone()


def list_users() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY id").fetchall()


def set_user_active(user_id: int, active: bool):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_active=? WHERE id=?", (1 if active else 0, user_id))


def delete_user(user_id: int) -> dict:
    """级联删除某用户及其个人库；团队库若因删除而失去 owner，则转交给其余成员、否则删除。

    返回需上层清理磁盘/向量的库 id（个人库 + 被删除的空团队库）。
    """
    with get_conn() as conn:
        personal = conn.execute(
            "SELECT id FROM datasets WHERE type='personal' AND owner_id=?",
            (user_id,),
        ).fetchone()
        team_cleanup: list[int] = []
        team_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM datasets WHERE type='team' AND owner_id=?", (user_id,)
            )
        ]
        for tid in team_ids:
            others = conn.execute(
                "SELECT user_id FROM dataset_members WHERE dataset_id=? AND user_id!=? ORDER BY user_id",
                (tid, user_id),
            ).fetchall()
            if others:
                # 移交 owner 给第一位剩余成员，避免团队库无主
                conn.execute(
                    "UPDATE dataset_members SET role='owner' WHERE dataset_id=? AND user_id=?",
                    (tid, others[0]["user_id"]),
                )
            else:
                conn.execute("DELETE FROM kb_files WHERE dataset_id=?", (tid,))
                conn.execute("DELETE FROM dataset_members WHERE dataset_id=?", (tid,))
                conn.execute("DELETE FROM datasets WHERE id=?", (tid,))
                team_cleanup.append(tid)
        # 个人库文件记录与会话
        conn.execute(
            "DELETE FROM kb_files WHERE dataset_id=?",
            (personal["id"],) if personal else (-1,),
        )
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM messages WHERE session_id NOT IN (SELECT id FROM sessions)")
        conn.execute("DELETE FROM dataset_members WHERE user_id=?", (user_id,))
        conn.execute(
            "DELETE FROM datasets WHERE type='personal' AND owner_id=?",
            (user_id,),
        )
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return {
            "personal_id": personal["id"] if personal else None,
            "team_cleanup_ids": team_cleanup,
        }


# ---------- 数据集（知识库） ----------
def ensure_personal_dataset(user_id: int) -> int:
    """为每个用户隐式创建/获取其个人库。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM datasets WHERE type='personal' AND owner_id=?",
            (user_id,),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO datasets (name, type, owner_id, created_at) VALUES (?,?,?,?)",
            ("我的知识库", "personal", user_id, utcnow()),
        )
        conn.execute(
            "INSERT INTO dataset_members (user_id, dataset_id, role) VALUES (?,?,?)",
            (user_id, cur.lastrowid, "owner"),
        )
        return cur.lastrowid


def create_team_dataset(owner_id: int, name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO datasets (name, type, owner_id, created_at) VALUES (?,?,?,?)",
            (name, "team", owner_id, utcnow()),
        )
        did = cur.lastrowid
        conn.execute(
            "INSERT INTO dataset_members (user_id, dataset_id, role) VALUES (?,?,?)",
            (owner_id, did, "owner"),
        )
        return did


def get_dataset(dataset_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()


def rename_dataset(dataset_id: int, name: str):
    with get_conn() as conn:
        conn.execute("UPDATE datasets SET name=? WHERE id=?", (name, dataset_id))


def get_member_role(user_id: int, dataset_id: int):
    """返回用户在数据集内的角色；无权限返回 None。超管由调用方另行判断。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT role FROM dataset_members WHERE user_id=? AND dataset_id=?",
            (user_id, dataset_id),
        ).fetchone()
        return row["role"] if row else None


def list_my_datasets(user_id: int) -> list[sqlite3.Row]:
    """该用户可见的库：个人库 + 作为成员加入的团队库。"""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT d.*, m.role AS my_role
            FROM datasets d
            JOIN dataset_members m ON m.dataset_id = d.id
            WHERE m.user_id=?
            ORDER BY d.type='team' DESC, d.id
            """,
            (user_id,),
        ).fetchall()


def list_accessible_dataset_ids(user_id: int, is_admin: bool = False) -> list[int]:
    """该用户可检索的数据集 id 集合；超管额外含所有团队库。"""
    with get_conn() as conn:
        if is_admin:
            rows = conn.execute(
                """
                SELECT id FROM datasets WHERE type='team'
                UNION
                SELECT dataset_id FROM dataset_members WHERE user_id=?
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT dataset_id FROM dataset_members WHERE user_id=?",
                (user_id,),
            ).fetchall()
        key = "id" if is_admin else "dataset_id"
        return [r[key] for r in rows]


def add_member(user_id: int, dataset_id: int, role: str = "member"):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO dataset_members (user_id, dataset_id, role) VALUES (?,?,?)",
            (user_id, dataset_id, role),
        )


def remove_member(user_id: int, dataset_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM dataset_members WHERE user_id=? AND dataset_id=?",
            (user_id, dataset_id),
        )


def set_member_role(user_id: int, dataset_id: int, role: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE dataset_members SET role=? WHERE user_id=? AND dataset_id=?",
            (role, user_id, dataset_id),
        )


def list_members(dataset_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.id AS user_id, u.username, m.role
            FROM dataset_members m JOIN users u ON u.id = m.user_id
            WHERE m.dataset_id=?
            """,
            (dataset_id,),
        ).fetchall()


def delete_dataset(dataset_id: int):
    """级联删除：成员关系 + 库内文件记录（磁盘与向量由上层清理）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM dataset_members WHERE dataset_id=?", (dataset_id,))
        conn.execute("DELETE FROM kb_files WHERE dataset_id=?", (dataset_id,))
        conn.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))


def list_all_dataset_ids() -> list[int]:
    """全部数据集 id（用于「重建所有索引」）。"""
    with get_conn() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM datasets ORDER BY id")]


# ---------- 会话 ----------
def create_session(user_id: int, title: str = "新会话", dataset_id: int | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (user_id, title, dataset_id, created_at) VALUES (?,?,?,?)",
            (user_id, title, dataset_id, utcnow()),
        )
        return cur.lastrowid


def list_sessions(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()


def get_session(session_id: int, user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)
        ).fetchone()


def get_session_by_id(session_id: int):
    """按主键取会话（不校验收款归属），供公开只读分享使用。"""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()


def rename_session(session_id: int, user_id: int, title: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET title=? WHERE id=? AND user_id=?",
            (title, session_id, user_id),
        )


def delete_session(session_id: int, user_id: int) -> int:
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        cur = conn.execute(
            "DELETE FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)
        )
        return cur.rowcount


# ---------- 消息 ----------
def add_message(session_id: int, role: str, content: str, hits: list | None = None, reasoning: str | None = None, images: list | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, hits, reasoning, images, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                role,
                content,
                json.dumps(hits, ensure_ascii=False) if hits else None,
                reasoning,
                json.dumps(images, ensure_ascii=False) if images else None,
                utcnow(),
            ),
        )
        return cur.lastrowid


def list_messages(session_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "hits": json.loads(r["hits"]) if r["hits"] else None,
                "reasoning": r["reasoning"] if "reasoning" in r.keys() else None,
                "images": json.loads(r["images"]) if "images" in r.keys() and r["images"] else None,
            }
        )
    return out


def list_recent_messages(session_id: int, limit: int = 20) -> list[dict]:
    """按时间倒序取最近 limit 条消息，再正序返回，用于组装多轮对话上下文。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content FROM messages "
            "WHERE session_id=? AND (content IS NOT NULL AND content <> '') "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [
        {"id": r["id"], "role": r["role"], "content": r["content"]}
        for r in reversed(rows)
    ]


def update_message_content(message_id: int, content: str, reasoning: str | None = None):
    with get_conn() as conn:
        if reasoning is None:
            conn.execute("UPDATE messages SET content=? WHERE id=?", (content, message_id))
        else:
            conn.execute(
                "UPDATE messages SET content=?, reasoning=? WHERE id=?", (content, reasoning, message_id)
            )


# ---------- 知识库文件 ----------
def add_kb_file(dataset_id: int, filename: str, size: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO kb_files (dataset_id, filename, size, uploaded_at) VALUES (?,?,?,?)",
            (dataset_id, filename, size, utcnow()),
        )
        return cur.lastrowid


def list_kb_files(dataset_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM kb_files WHERE dataset_id=? ORDER BY id DESC", (dataset_id,)
        ).fetchall()


def list_all_kb_files() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM kb_files ORDER BY id DESC").fetchall()


def set_kb_file_status(file_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE kb_files SET status=? WHERE id=?", (status, file_id))


def set_kb_file_parse_status(file_id: int, parse_status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE kb_files SET parse_status=? WHERE id=?", (parse_status, file_id)
        )


def get_kb_file(file_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM kb_files WHERE id=?", (file_id,)).fetchone()


def delete_kb_file(file_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM kb_files WHERE id=?", (file_id,))


# ---------- 运行时设置（key/value，供管理页覆盖模型/检索参数） ----------
def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_all_setting_keys() -> dict[str, str]:
    with get_conn() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


# ---------- 数据集级检索参数（v0.5） ----------
def get_dataset_params(dataset_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM dataset_params WHERE dataset_id=?", (dataset_id,)
        ).fetchone()


def set_dataset_params(
    dataset_id: int,
    score_threshold: float | None = None,
    top_k: int | None = None,
    split_mode: str | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    create: bool = True,
):
    """写入数据集级参数；*/top_k 传 None 表示清空为 NULL（回落全局）。

    split_mode/chunk_size/overlap 为分块模板与参数，None 表示回落全局默认。
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT 1 FROM dataset_params WHERE dataset_id=?", (dataset_id,)
        )
        if cur.fetchone() is None and create:
            conn.execute(
                "INSERT INTO dataset_params "
                "(dataset_id, score_threshold, top_k, split_mode, chunk_size, overlap, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (dataset_id, score_threshold, top_k, split_mode, chunk_size, overlap, utcnow()),
            )
        else:
            conn.execute(
                "UPDATE dataset_params SET score_threshold=?, top_k=?, split_mode=?, chunk_size=?, "
                "overlap=? WHERE dataset_id=?",
                (score_threshold, top_k, split_mode, chunk_size, overlap, dataset_id),
            )


# ---------- 只读分享（v0.5） ----------
def add_share(session_id: int, token_hash: str, expires_at: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO shares (session_id, token_hash, expires_at, created_at) "
            "VALUES (?,?,?,?)",
            (session_id, token_hash, expires_at, utcnow()),
        )
        return cur.lastrowid


def get_share_by_token(token_hash: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM shares WHERE token_hash=?", (token_hash,)
        ).fetchone()


def get_share(id_: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM shares WHERE id=?", (id_,)).fetchone()


def revoke_share(id_: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM shares WHERE id=?", (id_,))


# ---------- 审计日志（v0.5） ----------
def add_audit(actor_id: int | None, username: str, action: str, target: str = "", detail: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor_id, username, action, target, detail, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (actor_id, username, action, target, detail, utcnow()),
        )


def list_audit(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- 会话令牌（持久化，重启后不踢下线；仅存 sha256 哈希） ----------
def add_token(token_hash: str, user_id: int, expires_at: float):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tokens (token_hash, user_id, expires_at) VALUES (?,?,?)",
            (token_hash, user_id, expires_at),
        )


def get_token(token_hash: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT user_id, expires_at FROM tokens WHERE token_hash=?", (token_hash,)
        ).fetchone()


def delete_token(token_hash: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM tokens WHERE token_hash=?", (token_hash,))


# ---------- 模型配置库（多模型，每条可含独立 API Key，激活一条为当前 LLM） ----------
def list_model_configs() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, display_name, provider, model, base_url, "
            "CASE WHEN api_key <> '' THEN 1 ELSE 0 END AS has_key,"
            " is_active, updated_at FROM model_configs ORDER BY id"
        ).fetchall()


def get_active_model_config():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM model_configs WHERE is_active=1 ORDER BY id LIMIT 1"
        ).fetchone()


def get_model_config(id_: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM model_configs WHERE id=?", (id_,)).fetchone()


def add_model_config(display_name, provider, model, base_url, api_key, is_active) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO model_configs "
            "(display_name, provider, model, base_url, api_key, is_active, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (display_name, provider, model, base_url, api_key, 1 if is_active else 0, utcnow()),
        )
        if is_active:
            conn.execute("UPDATE model_configs SET is_active=0 WHERE id!=?", (cur.lastrowid,))
        return cur.lastrowid


def update_model_config(id_: int, fields: dict):
    if not fields:
        return
    # 白名单护栏：只允许更新已声明的列，避免拼接任意字段名形成注入点
    _MODEL_CONFIG_COLS = {
        "display_name", "provider", "model", "base_url", "api_key", "is_active",
    }
    allowed = {c: fields[c] for c in fields if c in _MODEL_CONFIG_COLS}
    if not allowed:
        return
    set_sql = ", ".join(f"{c}=?" for c in allowed)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE model_configs SET {set_sql}, updated_at=? WHERE id=?",
            [*allowed.values(), utcnow(), id_],
        )


def activate_model_config(id_: int):
    with get_conn() as conn:
        conn.execute("UPDATE model_configs SET is_active=0")
        conn.execute(
            "UPDATE model_configs SET is_active=1, updated_at=? WHERE id=?", (utcnow(), id_)
        )


def delete_model_config(id_: int) -> bool:
    """删除模型配置；若删除的正是当前激活项则返回 True，供上层把剩余项激活。"""
    with get_conn() as conn:
        row = conn.execute("SELECT is_active FROM model_configs WHERE id=?", (id_,)).fetchone()
        conn.execute("DELETE FROM model_configs WHERE id=?", (id_,))
        return bool(row and row["is_active"])


# ---------- 用户资源配额 ----------
def count_sessions(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE user_id=?", (user_id,)
        ).fetchone()["c"]


def sum_kb_bytes_for_user(user_id: int) -> int:
    """该用户作为成员参与的所有数据集的文件字节之和（用于存储容量配额）。"""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT COALESCE(SUM(f.size), 0) s
            FROM kb_files f
            JOIN dataset_members m ON m.dataset_id = f.dataset_id
            WHERE m.user_id=?
            """,
            (user_id,),
        ).fetchone()["s"]


def list_user_quotas() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, is_active, max_sessions, max_kb_mb FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def set_user_quota(user_id: int, max_sessions: int | None = None, max_kb_mb: int | None = None):
    sets, params = [], []
    if max_sessions is not None:
        sets.append("max_sessions=?")
        params.append(max_sessions)
    if max_kb_mb is not None:
        sets.append("max_kb_mb=?")
        params.append(max_kb_mb)
    if not sets:
        return
    params.append(user_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", params)


def usage_stats() -> dict:
    """管理概览：全局统计 + 逐用户文件/会话用量。"""
    with get_conn() as conn:
        totals = {
            "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
            "active_users": conn.execute("SELECT COUNT(*) c FROM users WHERE is_active=1").fetchone()["c"],
            "sessions": conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"],
            "messages": conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"],
            "questions": conn.execute("SELECT COUNT(*) c FROM messages WHERE role='user'").fetchone()["c"],
            "datasets": conn.execute("SELECT COUNT(*) c FROM datasets").fetchone()["c"],
            "team_datasets": conn.execute("SELECT COUNT(*) c FROM datasets WHERE type='team'").fetchone()["c"],
        }
        f = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM kb_files"
        ).fetchone()
        totals["files"] = f["c"]
        totals["bytes"] = f["s"]
        users = conn.execute(
            """
            SELECT u.id, u.username, u.is_active, u.max_sessions, u.max_kb_mb,
              (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id) AS sessions,
              (SELECT COUNT(*) FROM messages m JOIN sessions s ON s.id=m.session_id
                WHERE s.user_id=u.id) AS messages,
              (SELECT COUNT(*) FROM messages m JOIN sessions s ON s.id=m.session_id
                WHERE s.user_id=u.id AND m.role='user') AS questions,
              (SELECT COUNT(*) FROM datasets d WHERE d.owner_id=u.id) AS datasets,
              (SELECT COUNT(*) FROM kb_files f JOIN datasets d ON d.id=f.dataset_id
                WHERE d.owner_id=u.id) AS files,
              (SELECT COALESCE(SUM(f.size),0) FROM kb_files f JOIN datasets d ON d.id=f.dataset_id
                WHERE d.owner_id=u.id) AS bytes,
              (SELECT MAX(m.created_at) FROM messages m JOIN sessions s ON s.id=m.session_id
                WHERE s.user_id=u.id) AS last_active
            FROM users u ORDER BY u.id
            """
        ).fetchall()
    return {"totals": totals, "users": [dict(r) for r in users]}


def daily_usage(days: int = 14) -> list[dict]:
    """近 N 天逐日使用情况（问答题数 / 新增会话 / 活跃用户数），用于趋势与优化依据。按 UTC+8 自然日统计。"""
    today = datetime.now(TZ8).date()
    start = today - timedelta(days=days - 1)
    days_list = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    with get_conn() as conn:
        q_map = {
            r["d"]: r["c"]
            for r in conn.execute(
                "SELECT substr(created_at,1,10) d, COUNT(*) c FROM messages WHERE role='user' GROUP BY d"
            )
        }
        s_map = {
            r["d"]: r["c"]
            for r in conn.execute("SELECT substr(created_at,1,10) d, COUNT(*) c FROM sessions GROUP BY d")
        }
        a_map = {
            r["d"]: r["c"]
            for r in conn.execute(
                "SELECT substr(m.created_at,1,10) d, COUNT(DISTINCT s.user_id) c "
                "FROM messages m JOIN sessions s ON s.id=m.session_id GROUP BY d"
            )
        }
    return [
        {
            "date": d,
            "questions": q_map.get(d, 0),
            "new_sessions": s_map.get(d, 0),
            "active_users": a_map.get(d, 0),
        }
        for d in days_list
    ]
