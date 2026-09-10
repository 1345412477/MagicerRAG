# MagicerRAG

一套可运行、可扩展的**本地 / 私有化 RAG（检索增强生成）知识问答工作台**：把团队或个人的私有知识做成可检索问答，支持**网页工作台**与**命令行**两条使用路径，共用同一套 `rag/` 检索管道，可 Docker 化部署到云服务器。

## 核心特性

- **会话按知识库分类管理**：新建会话时选择目标知识库，会话在左侧按知识库分组展示；进入会话后固定在该知识库内检索，二次会话无需再选，且支持多轮上下文连续问答。借鉴主流产品交互，降低跨库上下文污染。
- **混合检索**（稠密向量 + BM25 倒排索引）：按 `alpha` 加权融合或 RRF 倒数排名融合，兼顾语义与关键词命中。
- **重排序 / 多样性**：支持本地 Cross-Encoder（`bge-reranker`）或外接 OpenAI 兼容 `/v1/rerank` 服务重排；可启用 MMR 提升多样性与检索前查询改写。
- **中文友好的 token 预算**：按中文分词粒度估算 token，避免长文被低估导致上下文截断。
- **多用户权限体系**：注册邀请码、个人知识库与团队知识库、成员/管理员/所有者角色、`require_dataset_manager` / `require_dataset_owner` 鉴权中间件、超级管理员账号。
- **多格式文档解析**：PDF / Word / Excel / PPT / Markdown / TXT / 图片 OCR（tesseract）。
- **流式回答 + 来源卡片**：SSE 流式生成，回答附带可展开回查的来源片段。
- **会话导出与只读分享**：一键导出 Markdown；生成带有效期、仅存哈希的只读分享链接。
- **安全与可观测**：模型 API Key 用 Fernet 加密落库、请求 ID / 安全响应头、操作审计、读多写少的并发控制。
- **自动化测试与 CI**：`pytest` 附带隔离存储夹具（临时 SQLite / upl网 / 向量库）+ GitHub Actions。

## 技术栈

- 后端：Python · FastAPI · SQLite（SQLAlchemy/原生） · SSE
- 检索：Chroma 向量库 · langchain · 自研 BM25 倒排索引 · RRF / 加权融合
- 前端：原生 HTML / CSS / JavaScript 单页（深 / 浅主题），零构建依赖
- 编排：Docker · docker-compose

## 项目结构

```
MagicerRAG/
├── config.py              # 配置（模型/路径/检索/服务端），读取 .env
├── ingest.py              # 入口①（CLI）：离线建索引
├── query.py               # 入口②（CLI）：在线问答
├── app/                   # ★ 网页工作台（FastAPI + 原生前端）
│   ├── main.py            # FastAPI 入口
│   ├── routers/           # 鉴权 / 会话与 SSE 问答 / 知识库
│   ├── service/           # RAG 服务封装 + 知识库重建
│   ├── db.py              # SQLite（账号/会话/消息/知识库文件）
│   ├── limits.py          # 登录/注册限流
│   ├── websecurity.py     # 请求 ID / 安全响应头
│   └── static/            # 前端单页（深/浅主题）与第三方 vendor
├── rag/                   # 核心 RAG 管道
│   ├── ingestor.py        # 文档加载 + 切分（Chunking）
│   ├── hybrid.py          # 稠密 + BM25 混合检索（倒排索引 / 缓存）
│   ├── retriever.py       # Embedding + Chroma + 检索（读多写少并发）
│   ├── rerank.py          # 本地 / 外部重排序
│   ├── summarize.py       # 超长文档分层摘要
│   ├── generator.py       # 提示词构造 + LLM 生成
│   └── pipeline.py        # 一键式编排封装
├── docs/                  # PRD / 部署文档 / 使用手册 / 评测样本
├── scripts/               # 备份脚本、检索效果评测
├── tests/                 # pytest 测试（隔离存储夹具）
├── .github/workflows/     # GitHub Actions CI
├── data/                  # 运行时数据（SQLite + 上传文档，不入库）
├── storage/vectordb/      # 向量库持久化（自动生成）
├── requirements*.txt / Dockerfile / docker-compose.yml
├── .env.example / .gitignore / .dockerignore
└── DEVLOG.md              # 开发日志
```

## 网页工作台（推荐）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 .env（见 .env.example）
Copy-Item .env.example .env   # Windows
# 3. 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000 ：

1. **注册**（需填写 `INVITE_CODE` 邀请码；`ADMIN_USERNAME` 指定的账号自动提权为超级管理员）→ 登录。
2. 左侧「📚 知识库」上传文档并**重建索引**。
3. **新建会话**：在弹出的知识库卡片中选择要使用的知识库（或选择“一切有权限的知识库”）。
4. 会话在左侧按知识库分组展示；`+` 新建话题后可继续提问，**此后固定在该知识库内检索**，无需再选。
5. 支持流式回复、来源卡片展开回查、多会话切换、会话重命名/删除、导出 Markdown 与只读分享、深/浅主题切换。

> 说明：`docs/`（PRD、部署文档、使用手册）与运行时 `data/`、`storage/` 属于本地私有内容，**不随仓库发布**，可按需自行备份。

## 快速开始（命令行）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置密钥——复制并如实填写
Copy-Item .env.example .env   # Windows PowerShell

# 3. 把 PDF / MD / TXT 知识文档放进 docs/ 目录
# 4. 建索引
python ingest.py

# 5. 提问
python query.py "账号是如何注册的？"
```

## 配置项一览（关键）

见 `.env.example` 完整注释。主要分组：

| 分组 | 说明 |
|---|---|
| `LLM_*` | 推理模型：模型名 / API key / base_url / 温度 |
| `EMBEDDING_*` | Embedding 模型（建索引与问答必须一致） |
| `VECTOR_DIR` / `DOCS_DIR` / `DATA_DIR` | 向量库 / 知识文档 / 运行时数据目录 |
| `TOP_K` / `SCORE_THRESHOLD` | 召回条数与最低相关度阈值 |
| `HYBRID_*` | 混合检索开关、alpha、融合方式（`weighted` / `rrf`） |
| `RERANK_*` | 重排序：开关、model、通道（`local` / `external`） |
| `MMR_*` / `QUERY_REWRITE` | 检索多样性 MMR 与查询改写 |
| `HISTORY_TURNS` / `CONTEXT_TOKEN_BUDGET` | 多轮历史轮数与上下文 token 预算 |
| `LONG_DOC_SUMMARY` | 超长文档分层摘要（默认关闭） |
| `HOST` / `PORT` | 监听地址与端口 |
| `INVITE_CODE` / `ADMIN_USERNAME` | 注册邀请码 / 超级管理员账号 |
| `SECRET_KEY` | 模型 API Key 落库加密密钥（生产建议独立固定） |
| `TESSERACT_CMD` / `TESSDATA_PREFIX` | 图片 OCR（仅在非常规路径时需要） |

## 测试与 CI

```bash
# 运行测试（tests/conftest.py 使用隔离的临时存储，无需真实密钥）
pip install -r requirements-dev.txt
python -m pytest -q
```

GitHub Actions（`.github/workflows/ci.yml`）在每次 push / PR 时：编译全部 Python、`node --check` 前端语法、运行 `pytest`。

## 说明

- Chroma 为本地零配置向量库，数据落盘 `storage/vectordb`，换机器需重新建索引。
- 生产环境请勿把密钥写入仓库（见 `.gitignore`，`.env` 与运行时 `data/`、`storage/` 均不入库）。
- 详细设计请参考项目内 `DEVLOG.md` 与本地 `docs/` 的 PRD 文档。