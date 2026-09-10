import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# 推理模型
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

# Embedding 模型
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "") or LLM_API_KEY
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "") or LLM_BASE_URL
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# 存储路径
VECTOR_DIR = (BASE_DIR / os.getenv("VECTOR_DIR", "./storage/vectordb")).resolve()
DOCS_DIR = (BASE_DIR / os.getenv("DOCS_DIR", "./docs")).resolve()

# 检索参数（阈值默认 0.3，与运行时可调/DB settings 覆盖保持一致，防止旧值 0.5 过滤掉合理结果）
TOP_K = int(os.getenv("TOP_K", "4"))
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.3"))

# ---- v0.5：检索质量与对话优化 ----
# 多轮对话注入最近多少轮历史（每轮含 user+assistant）
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "6"))
# 进入 prompt 的资料 token 预算（超过则按相关度裁剪并标注截断）
CONTEXT_TOKEN_BUDGET = int(os.getenv("CONTEXT_TOKEN_BUDGET", "4000"))
# 检索前是否用会话历史改写问题（默认关闭，避免额外模型调用与延迟）
QUERY_REWRITE = os.getenv("QUERY_REWRITE", "0").lower() in {"1", "true", "yes", "on"}
# 是否对召回启用多样性（MMR），lambda 越小越多样
MMR_ENABLED = os.getenv("MMR_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.7"))
# 重排序：enabled=1 时尝试 rerank，否则回退
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
# N4：重排通道 local=本机 CrossEncoder（需 sentence-transformers）｜external=OpenAI 兼容 /v1/rerank
RERANK_CHANNEL = os.getenv("RERANK_CHANNEL", "local")
RERANK_EXTERNAL_URL = os.getenv("RERANK_EXTERNAL_URL", "")
RERANK_API_KEY = os.getenv("RERANK_API_KEY", "")
# 混合检索：稠密 + BM25，按 alpha 融合
HYBRID_ENABLED = os.getenv("HYBRID_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.7"))
# 混合检索融合方式：weighted=加权求和（alpha 权重）｜rrf=倒数排名融合
HYBRID_METHOD = os.getenv("HYBRID_METHOD", "weighted")
# 检索命中后进入 LLM 上下文的来源数量上限（默认 TOP_K）
MAX_CONTEXT_HITS = int(os.getenv("MAX_CONTEXT_HITS", "6"))
# 超长文档分层摘要（R-05）：默认关闭；开启后重建索引时对超长文档生成摘要分块
LONG_DOC_SUMMARY = os.getenv("LONG_DOC_SUMMARY", "0").lower() in {"1", "true", "yes", "on"}
LONG_DOC_CHARS = int(os.getenv("LONG_DOC_CHARS", "20000"))

# ---- 网页工作台（服务端）配置 ----
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
# 邀请码：成员注册时需填写（F6）
INVITE_CODE = os.getenv("INVITE_CODE", "magicer-demo")
# 超级管理员用户名（该账号自动提权、免邀请码，user 管理知识库全权限）
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
# 数据目录：SQLite 数据库、上传文档
DATA_DIR = (BASE_DIR / os.getenv("DATA_DIR", "./data")).resolve()
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "magicer.db")))
UPLOAD_DIR = DATA_DIR / "uploads"

# 图片 OCR：tesseract 在非常规目录时用 TESSERACT_CMD / TESSDATA_PREFIX 指定（可选）
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")
TESSDATA_PREFIX = os.getenv("TESSDATA_PREFIX", "")

# P2-2：Word 内嵌图片是否提取「图注描述」（Word 图片「描述/Alt 文本」）并入文本，无则图位占位
DOC_IMAGE_CAPTION = os.getenv("DOC_IMAGE_CAPTION", "1").lower() in {"1", "true", "yes", "on"}

# v0.5 安全加固：模型 API Key 落库加密的密钥。
# 未显式配置时，从 EMBEDDING_API_KEY 推导（保证开箱即用）；建议生产独立设置 SECRET_KEY。
SECRET_KEY = os.getenv("SECRET_KEY", "")


def require_env(name: str) -> str:
    """校验关键配置，避免裸 API 调用时报错难定位。"""
    value = {
        "LLM_API_KEY": LLM_API_KEY,
        "EMBEDDING_API_KEY": EMBEDDING_API_KEY,
    }[name]
    if not value or value.startswith("sk-xxxx"):
        raise RuntimeError(
            f"缺少必要配置：{name}。请填写真实 key 后再运行。"
        )
    return value
