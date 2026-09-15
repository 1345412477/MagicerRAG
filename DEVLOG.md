# MagicerRAG 开发日志

> 本文档沉淀本项目的开发历程与关键决策，便于回顾与交接。
> 阅读建议：先看**「一、版本演进速览」**了解全貌，**「四、当前状态」**看最新进度，其余按阶段查阅细节。

---

## 一、版本演进速览

| 阶段 / 版本 | 日期 | 主题 | 核心产出 |
| --- | --- | --- | --- |
| 骨架 | 09-02 | CLI RAG 原型 | DeepSeek 对话 + 千问 DashScope 向量化 + Chroma，端到端跑通 |
| v0.1 | 09-02 | 网页工作台方案 | PRD + 技术栈确定为 FastAPI + 原生前端单页 + SQLite + Chroma，复用自研 RAG |
| M1–M5 | 09-02 | 网页工作台主体 | 后端 API、会话/账号/知识库、前端界面、Docker 化、多知识库 + 三级权限 + 超管 |
| 迭代期 | 09-02 → 09-03 | 体验与视觉 | 会话式问答、Markdown/净化、多格式上传、知识库/首页重构、To C 品牌化 |
| 稳定性期 | 09-02 → 09-03 | 审查与加固 | 状态码规范、限流、单例/原子重建、配额强制、安全头 |
| v0.5 | 09-03 | 迭代优化 | R-01..R-12：对话质量、检索增强、工程化与安全闭环 |
| P0-检索 | 09-04 | RRF 融合 | `hybrid.fuse` 支持 `method=rrf` 排名融合；`retrieve` 透传 `hybrid_method` |
| P0-分块 | 09-04 | 分块参数用户化 | 知识库级 `split_mode/chunk_size/overlap` + 三模板（标题感知/递归/大段不拆）+ `/chunks-preview` 预览 + 重建透传 |
| P1-配置 | 09-04 | 混合/重排可视化配置 | 管理中心新增「融合方式」加权/RRF 下拉；混合开关/α、重排开关/模型 可视化即时生效 |
| P1-2 | 09-04 | 引用高亮定位 | 点击 `[n]` 在来源卡片内高亮最相关句子（`_bestSpan` + `mark.cite-hit`） |
| P2-1 | 09-04 | 分块预览标红 | 预览区块超长/空块打标（`length` 透出 + `kp-warn/kp-long/kp-empty`） |
| P2-2 | 09-04 | 图表多模态解析 | Word 内嵌图片图注（`descr`/Alt）提取为 `[图 N：描述]` 并入文本（`DOC_IMAGE_CAPTION` 可关） |

> 版本号说明：前端静态资源早期用手动 `?vN`，后改为**内容哈希自动生成**（见阶段 3），此后无需手工升版本号。

---

## 二、开发历程（按阶段）

### 阶段 0 · CLI 骨架与方案确认

- 在既有 CLI 版 RAG 骨架（DeepSeek + 千问 + Chroma）基础上，目标升级为**可 Docker 化云端部署的网页版知识问答工作台**。
- 确定范围：邀请码注册、多会话切换、检索来源可展开回查、深/浅主题、流式回答、知识库管理、Docker 化。
- 里程碑规划：M1 后端 API 层 → M2 会话/账号/知识库 → M3 前端界面 → M4 Docker 打包与部署文档。

### 阶段 1 · 网页工作台主体（M1–M5）

#### M1 & M2 · 后端 API 层 + 会话/账号/知识库

- **结构**：`app/{main,deps,db,websecurity,schemas}` + `service/{rag_service,kb_service}` + `routers/{auth,chats,kb}`。
- **关键决策**：进程内存令牌（小团队量级够用）；SSE 事件流 `retrieval → delta → done`；沿用原生 OpenAI 客户端规避国产兼容问题；重建索引采用「先清空旧向量库再全量重建」，杜绝重复 chunk。
- **验证**：注册（错误邀请码 403 / 正确 200 / 重复 409）、登录、多会话、知识库、未登录 401；SSE 顺序正确、答案完整。

#### M3 · 前端界面与联调

- `index.html / style.css / app.js` 单页：认证页 + 工作台布局 + 知识库滑出面板；`fetch + ReadableStream` 手写 SSE 解析（`EventSource` 不支持 POST）；来源卡片、主题切换、Toast。
- **验证**：注册→工作台、会话/流式回答、来源展开、主题切换、知识库面板均通过浏览器端到端。

#### M4 · Docker 打包与文档

- `Dockerfile`（两阶段构建）+ `docker-compose.yml`（`./data`、`./storage` 卷持久化 + `/api/health` 健康检查）+ `.dockerignore/.gitignore` + `.env.example` + `docs/部署文档.md` + `README.md`。
- **注意**：本机未装 Docker，镜像未本地构建验证（需在容器环境执行 `docker compose build` 确认）。

#### M5 · 多知识库 + 权限 + 超级管理员

- **数据模型**：`datasets`（个人库/团队库）+ `dataset_members`（owner/member→后升级三级）；`users.is_active`、`kb_files.dataset_id`；`_migrate()` 平滑加列。
- **后端**：`require_admin / require_dataset_owner / require_dataset_access`；超管 `ADMIN_USERNAME` 免邀请码；数据集 CRUD、成员邀请/移除；检索隔离（Chroma 单集合 + `dataset_id` metadata 过滤）。
- **前端**：数据集选择器、个人/团队库 Tab、管理员面板、注册邀请码选填。
- **验证**：16 项全 PASS（超管提权、越权 403、自动个人库、Owner/Member 差异、停用即禁登录）。
- **遗留**：邀请码为运行时下发生效，重启回 .env 设定值。

### 阶段 2 · 体验与视觉迭代（v3 → v19）

> 该阶段以「体验优化」为主线，多是纯前端改动（静态资源即时生效，无需重启）。按主题合并记录。

**回归修复（v3–v5）**
- v3：上传 `dataset_id` 由 query 改 `FormData`；登出 `resetChat()` 清空消息区；`/logout` 撤销内存 token；清理死代码。
- v4：主题切换状态与 UI 同步（`state.theme`），支持连续切换。
- v5：新建会话改为**懒创建**（首次提问才建库，取问题首句作标题）。

**会话式问答体验（v9 → v10）**
- Markdown 渲染 + DOMPurify 白名单净化 + 代码高亮（`static/vendor/` 本地化三件套）。
- **内联来源引用**：后端 `_SERVICE_SYSTEM` 要求标注 `[n]`，序号与来源卡片/正文上标一一对应，点击滚动定位并高亮。
- 会话搜索、悬停删除、停止生成（`AbortController`）。

**会话重命名（v10 → v11）**：铅笔图标 + 行内编辑（Enter 保存 / Esc 取消 / 失焦兜底）。

**多格式上传兼容（v11 → v12）**：新增 `rag/extractors.py`；`docx/pdf/xlsx/pptx/图片 OCR/文件夹` 全链路打通；`_MAX_SIZE` 10MB→50MB；含目录树上传统一。

**知识库页 UI 重构（v12 → v13）**：参考 file-manager 设计，左库列表 + 存储计、中快速访问 + 最近文件表、右上操作搜索；移动端折叠。

**首页干净化 + 管理中心优化（v13 → v14）**：空态「你好，我是 MagicerRAG」+ 建议词 chips；管理页升级为系统（概览/人员/模型/资源分配/邀请码）。

**管理细化（v14 → v18）**
- v14→v15：修复邀请码页输入框被压缩/隐形（`.btn-primary` width:100% 与输入框背景同色叠加）。
- v15→v16：模型管理预设下拉 + 自定义 + 测试连接 + 当前模型标识。
- 刷新后保持当前界面；模型管理支持多模型 + 独立 API Key；暴露 .env 默认模型供导入。
- 后台卡片/知识库内容区铺满去留白 + 移动端自适应。
- 版本号改内容哈希自动生成（开发零维护，内容变即换新）。
- UI 丝滑：内部滚动平滑、消息入场动画、`prefers-reduced-motion` 无障碍、密码可见性切换。

**会话工作台按设计稿细化（v14 → v16）**：消息气泡、AI 方形头像、消息操作行（赞/踩/复制）、文件引用卡、会话图标/时间、侧栏折叠；移除误导性的 `⌘K` 提示。

**管理后台使用情况跟踪（v16 → v18）**：近 14 天问答趋势 + 逐用户用量；修复会话列表横向滚动条。

**To C 产品化（v18 → v19）**：品牌定位「你的个人 AI 知识助手」；首页价值化（4 个快捷功能卡 + 最近使用）；上传后自动提问「总结文档」；回答来源加「参考资料 N」标题 + 「你还可以问」推荐问题。

### 阶段 3 · 稳定性与安全加固

> 该阶段聚焦「正确性 / 安全 / 可维护」，多为后端改动，直接服务热加载或重启生效。

- **API 状态码规范 + 轻量限流（429）**：PRD 附录 v0.3 统一状态码语义；新增 `app/limits.py` 原生 ASGI 中间件（避免缓冲破坏 SSE），按「IP + 接口前缀」固定窗口计数；`/login|/register 10/分`、`/chats/ask 20/分`、其余 `/api/* 600/分` 兜底。踩坑：`bytes` 字面量不能含中文。
- **代码审查修复四连（v9）**：① 同名文件防覆盖（追加序号）；② 上传后自动重建索引；③ 删除文件重建失败不返回 500（改标记 pending）；④ 超管删用户级联清理孤儿数据（`_cleanup_user_data` + `cleanup_dataset`）。
- **全量优化（后端）**：token 过期（`dict[token,(user_id,expiry)]`）；反代限流失真（`X-Forwarded-For` 首跳）；配额归集（`count_sessions` / `sum_kb_bytes_for_user`）；**单例 store + 原子重建**（`rebuild_dataset` 事务化）；**异步生成**（`stream_answer_async`）；会话/容量配额强制（429/413）；删除文件同步索引状态。
- **多格式/运维修复**：补齐提取依赖；Docker `COPY docs` 与 `.dockerignore` 冲突修复；OCR 路径可移植化（`TESSERACT_CMD/which/常见位置`）；预览解码改走 `extract_text`；Embedding 批量上限（DashScope 10 条/批）分批提交。
- **版本号内容哈希自动生成 & Token 持久化**：`_static_version()` 对静态资源算 MD5 注入 `?v=`；token 由纯内存改 DB 持久化（SHA256 哈希 + 7 天过期），重启不掉线；检索与重建共用锁消除竞态；style.css 保守清理死代码。
- **已知低危项（记录备查）**：早期 token 明文存 localStorage、模型 Key 明文落库（后续 v0.5 已加密）、CSS 历史重叠定义、检索与重建极端并发竞态（已加锁）。

### 阶段 4 · v0.5 迭代优化（R-01..R-12）

> 依据《docs/PRD-网页知识问答工作台-v0.5.md》实施，围绕「问答质量 + 可信度 + 工程化」。

#### 已完成（后端 + 前端，均含自动化测试）

- **R-01 多轮上下文**：`chats.ask` 组装最近历史（`db.list_recent_messages` + token 预算 `build_messages`），追问/指代可正确应答。
- **R-02 查询改写**：`rag_service.rewrite_question` 基于历史改写用于检索，失败降级原问题；`query_rewrite` 默认关。
- **R-03 检索增强**：`rag/rerank.py`（Cross-Encoder，缺依赖安全回退）+ MMR 多样性（`retrieve` / `_mmr_select`）+ **纯 Python BM25 混合检索**（`rag/hybrid.py`，中英双语含中文 bigram + `fuse` 融合），均可配置开关，默认走纯向量路径。
- **R-04 按库参数 + 评测**：`dataset_params` 库级阈值/top_k 覆盖；`/api/kb/datasets/{id}/params`；`scripts/eval_rag.py` + `docs/eval/sample.jsonl` 开箱可跑。
- **R-05 结构化切分 + 预算 + 长文摘要**：`ingestor` 对 Markdown 标题感知分节（超长节再递归细分）；`fit_context` 按 token 预算裁剪资料；`rag/summarize.py` map-reduce 为超阈值文档生成 `kind=summary` 摘要分块（默认关，失败回退）。
- **R-06 索引异步化 + 进度**：`app/tasks.py` 后台任务 + `/api/kb/tasks/{tid}` 轮询；前端重建按钮显示百分比进度。
- **R-07 Embedding 一致性 + 一键重建**：重建后落库 `index_embedding_model`；`/api/admin/embedding-status` 检测「当前 Embedding vs 索引模型」；`/api/admin/rebuild-all` 后台重建全部数据集；管理台显示一致/告警 + 进度按钮。
- **R-08 上传安全/解析状态**：`_mime_matches` 内容 magic 一致性校验；`kb_files.parse_status`（ok/empty/unsupported/pending）列表展示。
- **R-09 导出与只读分享**：`/api/chats/sessions/{sid}/export` 导出 Markdown；`/share` 生成带过期时间、token 仅存哈希的只读链接；前端头部导出/分享按钮。
- **R-10 自动化测试与 CI**：`tests/`（隔离临时存储 + mock 检索/大模型），`pytest` 全绿；`requirements-dev.txt` + `.github/workflows/ci.yml`。
- **R-11 安全闭环**：`audit_log` 表 + 管理动作落审计；模型 Key **Fernet 加密落库**（`app/crypto.py`，`SECRET_KEY` 或从 `EMBEDDING_API_KEY` 推导，兼容旧明文）；**httpOnly Cookie 会话**（`mr_session`，SameSite=Lax + 条件 Secure，前端不再存 token 于 localStorage）；**CSRF 双提交令牌**（`mr_csrf` 非 HttpOnly + `X-CSRF-Token` 头，`/me` 对存量会话补发）+ 安全响应头。
- **R-12 部署与可观测**：请求 ID + 安全头中间件；Docker 改非 root + 内置 tesseract-ocr/中文包/libgomp1；`scripts/backup.{sh,ps1}` 备份脚本。

#### 验证

- `pytest`：**34 passed**；`py_compile` 全量 0 失败；`node --check app.js` 通过。
- 真实运行（隔离 + 交付实例）端到端：`/api/health` 200、静态资源内容哈希注入、导出/管理端点鉴权（无 token 401）、模型参数持久化、**Cookie 会话**（仅凭 Cookie `/me` 200）、**CSRF**（无头 403 / 带头 200）、Embedding 状态。
- 交付服务已用最新代码在 `0.0.0.0:8000` 运行。

---

## 三、关键技术决策速览

| 领域 | 决策 |
| --- | --- |
| 鉴权 | 密码 PBKDF2-HMAC；令牌 SHA256 落库，7 天过期；v0.5 起优先走 httpOnly Cookie + CSRF 双提交，Bearer 头兼容 |
| 流式 | 原生 ASGI 限流中间件（避免缓冲破坏 SSE）；`fetch+ReadableStream` 手写 SSE（`EventSource` 不支持 POST） |
| 检索 | Chroma 单集合 + `dataset_id` metadata 过滤实现库隔离；重索引用「清空 + 原子重建」+ 共享锁；`build_embeddings` 单例 |
| 版本号 | 静态资源 MD5 内容哈希自动派生 `?v=`，内容变即换新，无需手动累加 |
| 安全 | DOMPurify 白名单渲染；上传文件名消毒 + 同名加序号；magic 内容校验；模型 Key Fernet 加密；管理操作审计 |
| 部署 | 两阶段 Docker 构建 + 非 root 运行 + 数据卷持久化；备份脚本 `backup.{sh,ps1}` |

---

## 四、当前状态 / 已知风险与待办

### 当前状态

- 一个可用的**私有 RAG 知识问答工作台**：多用户、多知识库（个人/团队 + 三级权限）、多会话、流式回答 + 来源回查、多格式上传、管理后台（系统概览 / 人员 / 模型 / 资源配额 / 使用情况 / 邀请码）。
- v0.5 迭代需求 R-01..R-12 全部落地，含自动化测试与 CI，交付服务运行于 `0.0.0.0:8000`。
- P0 检索优化落地：P0-2 RRF 排名融合（09-04）、P0-1 分块参数用户化（09-04，知识库级分块模板/参数 + 预览 + 重建透传）。
- P1-1 混合/重排可视化配置落地（09-04，「融合方式」加权/RRF 下拉、混合开关/α、重排开关/模型 管理中心即时生效）。
- **P1/P2 增强落地（09-04）**：P1-2 引用高亮定位（`[n]`→来源卡片命中句子高亮）、P2-1 分块预览超长/空块标红、P2-2 Word 内嵌图片图注多模态解析。已与 RAGFlow 的三方法论（模板化可控分块 / 引用溯源 / 多路召回+融合重排）完成对齐，见 `OPTIMIZATION.md` 状态全勾选。
- **交互优化与体验统一落地（09-04，阶段 12）**：全局 UTC+8 时间、上传进度条、文件批量删除、登录页优先登录、每个模块独立页面、模型管理表单溢出优化，全部实现并经 API + 浏览器端到端验证。

### 已知风险 / 待办（非阻塞）

- 混合检索用 `rank_bm25` 优化大库倒排（当前纯 Python BM25 对 10 人量级足够）。
- 严格 CSP（引用了 Lucide CDN 与 marked/purify/highlight，需资源白名单；暂以 SameSite+Lax + 自定义头 + DOMPurify 规避）。
- 后台任务为内存型（重启丢任务，10 人量级可接受）。
- 长文 map-reduce 摘要默认关闭，需在管理后台开启后重建索引才生效。

---

## 阶段 6 · P0-2 检索优化（RRF 融合）

- **根因**：混合检索 `fuse()` 仅支持归一化加权求和（`alpha*dense+(1-alpha)*lex`），对分数尺度敏感；术语化/指令词下单一通道分数被整体低估时会失落。
- **实现**：`rag/hybrid.py` 新增 `_ranks()`（按 score 排 1 基名次）与 `_fuse_rrf()`（RRF：`score=Σ 1/(k+rank)`，k=60）；`fuse()` 增加 `method="weighted"|"rrf"`，默认 `weighted` 兼容旧行为；`rag/retriever.py` 的 `retrieve()` 与 `_hybrid_retrieve()` 透传 `hybrid_method`。
- **验证**：`python test_rrf.py` 通过（精确 RRF 分数断言、汇聚去重、镜像名次平局语义、weighted 基线回归、单通道单调）。（无静态资源改动，前端版本号无需手改）

---

## 阶段 7 · P0-1 分块参数用户化

- **根因**：`ingestor.chunk_documents()` 固定 `chunk_size=500, overlap=50`，仅按标题感知/递归切分，用户不可调、不可预览；不同文档（问答集、长报告、表格类）需要不同的切分粒度与边界策略。
- **实现**：
  - `rag/ingestor.py`：`chunk_documents()` 新增 `mode` 参数（`markdown` 标题感知（默认，兼容旧行为）/ `recursive` 强制递归 / `whole` 大段不拆按节成块），`_whole_split()` 新增；`load_and_chunk()` 透传 `chunk_size/overlap/mode`。
  - `app/db.py`：`dataset_params` 表新增 `split_mode/chunk_size/overlap` 字段（NULL=回落全局默认）；`set_dataset_params()` 支持写入。
  - `app/schemas.py`：`DatasetParamsUpdate` 增加三字段（`split_mode` 正则校验、`chunk_size` 64-2000、`overlap` 0-400）。
  - `app/routers/kb.py`：`GET/PUT /datasets/{id}/params` 读写分块参数；新增 `GET /datasets/{id}/chunks-preview` 只读分块预览接口（按当前参数切分，返回概要 + 前若干区块）。
  - `app/service/kb_service.py`：`rebuild_index()` 读取该库分块参数透传 `ingestor`，重建按参数生效（未设置为空则用默认）。
  - 前端 `static/index.html`+`app.js`+`style.css`：工具栏新增「分块设置」「分块预览」按钮与两个模态框；打开设置时回读并回填参数；「保存并重建」PUT 参数后自动触发重建；预览模态框渲染区块卡片（编号/来源·小节/内容片段）。静态资源内容哈希自动换版本。
- **验证**：`python test_chunk_params.py` 全绿（参数写入/回读、`recursive` 预览 effective 生效、非法模板 422、`whole` roundtrip、默认回落）；浏览器自动化确认「分块设置」「分块预览」模态框正常打开、三模板 + 参数输入框齐全、预览区块正常渲染。

---

## 阶段 8 · P1-1 混合检索 / 重排纳入可视化配置

- **根因**：`HYBRID_ENABLED`、混合权重 α、重排开关与模型均埋于配置层，普通用户无感知，混合 + 重排能力停留在代码里；尤其 P0-2 的 RRF 融合缺少「加权 / RRF」切换入口。
- **实现**：
  - `config.py` 新增 `HYBRID_METHOD`（默认 `weighted`）。
  - `app/runtime_settings.py`：`hybrid_method` 进入 `_DEFAULTS` / `_EDITABLE` / `load()`。
  - `app/routers/admin.py`：`AdminSettings` 增加 `hybrid_method`（`pattern="^(weighted|rrf)$"`）；`GET /model` 与 `PUT /model`（请求与响应）透出/持久化该字段。
  - `app/service/rag_service.py`：`retrieve()` 调用补传 `hybrid_method=rt["hybrid_method"]`，打通 RRF 运行时生效。
  - 前端 `app.js`（v21→v22）：模型管理「检索参数」区，在「混合检索」行新增「融合方式」下拉（`加权（α 权重）` / `RRF 排名`），`saveParams()` 一并提交 `hybrid_method`。
- **验证**：`python test_p111.py` 全绿（GET 默认 `weighted`、PUT `rrf` 生效、非法值 422、切回 `weighted` 生效、`runtime_settings.load()` 透出、`rag_service` 实参透传）；浏览器自动化确认「混合检索 / 融合方式 / 混合权重 α」三控件齐全、切换「RRF 排名」保存 toast「参数已保存」、刷新后仍为 RRF（持久化）、Console 无报错、无 4xx/5xx。

---

## 阶段 9 · P1-2 引用高亮定位

- **根因**：内联 `[n]` 点击已能滚动定位到对应来源卡片，但卡片内未高亮「命中段落」，证据定位不够直观。
- **实现**（纯前端，静态资源内容哈希自动换版本）：
  - `app/static/app.js`：新增 `_tokenize()`（中英文 token 化）、`_bestSpan()`（把引用段落按句号/感叹/分号/换行拆句，用问题词项命中打分选出最相关句子并定位其文本起止）、`highlightHit(card, query)`（先清除旧 `mark.cite-hit`，`normalize()` 后仅包住命中句子，`scrollIntoView` 居中）；引用标记点击处理改为取会话当前用户问题文本传入并调用。
  - `app/static/style.css`：新增 `mark.cite-hit` 样式（品牌色淡底 + 底部内嵌下划线，与全局 token 一致）。
- **验证**：引用卡片内命中句子高亮、重复点击内部可替换高亮；`node --check app.js` 通过；浏览器端到端确认点击 `[n]` 后来源卡片对应句子被高亮且滚动到可视区。

---

## 阶段 10 · P2-1 分块预览标红（超长 / 空块）

- **根因**：`/chunks-preview` 已能列出分块，但前端无法识别异常块（超目标区块长度、空块），难以判断是否需调整分块参数。
- **实现**（前端为主 + 后端透出字段）：
  - `app/routers/kb.py`：`GET /datasets/{id}/chunks-preview` 响应区块新增 `length` 字段（该块字符数）。
  - `app/static/app.js`：预览渲染时按 `c.length` 与 `effective.chunk_size` 判定：空块（`trim().length===0`）打「空块」红标并加 `kp-empty`；`length > round(chunk_size*1.6)` 打「超长 N」橙标并加 `kp-long`；无异常则不带标。
  - `app/static/style.css`：新增 `.kp-warn`（胶囊警示标签，超长=橙 / 空块=红）、`.kp-long` / `.kp-empty`（边框 + 淡底区分，与现有蓝色规范色冲突时取语义色）。
- **验证**：`python test_p112.py`（preview 区块含 `length` 字段断言）通过；浏览器端到端确认预览中异常块带标。

---

## 阶段 11 · P2-2 图表多模态解析（Word 图注）

- **根因**：`extractors.py` 对 Word 仅提取文本，文档中的「图」与「表」语义（尤其图表类问答）完全丢失，只能靠正文措辞猜测。
- **实现**：
  - `config.py`：新增 `DOC_IMAGE_CAPTION`（默认 `1`），控制是否提取 Word 内嵌图片「图注描述」（即图片「描述 / Alt 文本」`descr`）并入文本。
  - `rag/extractors.py`：新增 `_docx_image_captions(doc)`（遍历正文中 `inline`/`anchor` 图形节点，读取其 `cNvPr`/`docPr` 的 `descr`，上限 `_IMAGE_CAPTION_LIMIT=60`）；`_read_docx()` 末尾把图注以 `[图 N：描述]`（有描述）或 `[图片 N]`（无描述，图位占位）追加为独立段落。
  - 表提取沿用既有「单元格 `|` 拼接为行」逻辑，未改语义。
- **验证**：`python test_p112.py` 全绿（DOCX 正文提取、有图注描述 → `[图 1：…]`、无描述 → 图位占位、无图文档 → 不含 `[图`）。测试仅断言提取文本，模型对各 `[图 N]` / `[图片 N]` 的利用率留待上线后按需观测。

## 阶段 12 · 交互优化与体验统一（09-04）

六个新增需求一次性落地，覆盖时间统一、上传体验、批量管理、登录默认态与模型表单布局。

- **需求 1 · 每个模块独立页面，不共用首页**：问答＝首页，知识库、管理中心为独立全屏页可返回（同页导航 + 平级页面）。—— 沿用既有多视图切换架构，确认无共用首页冲突。
- **需求 2 · 全局 UTC+8 时间统一**：
  - `db.py`：新增 `TZ8 = timezone(timedelta(hours=8))`；`utcnow()` 生成东八区墙钟时间戳；`daily_usage()` 按 UTC+8 自然日统计。
  - `app.js`：新增 `utc8Parts / utc8Now / fmtUtc8`，后端无时区时间戳一律按 `+08:00` 解析并取东八区墙钟展示，`timeLabel / fmtActive` 全部改走 `utc8Parts`，不受浏览器时区影响。
- **需求 3 · 上传进度条**：改 `XMLHttpRequest` 上传（`upload.onprogress`），`progUpdate` 回调把 `loaded/total` 写成百分比，UI 新增 `#upload-progress` 细胶囊进度条（含填充动效与百分比文字）。
- **需求 4 · 文件批量删除**：
  - 后端 `kb.py`：抽取 `_remove_file / _rebuild_after_delete`，新增 `POST /api/kb/datasets/{id}/files/batch-delete`（循环删除后末尾统一重建一次索引，避免多次重建开销）。
  - `schemas.py`：新增 `DeleteFiles{ids:list[int]}`。
  - 前端：文件行新增复选框列 + 头部全选；`selectedFiles` 集合驱动「删除选中（N）」按钮显隐与选中行高亮；`loadDsDetail / logout` 时清空选择。
- **需求 5 · 登录页优先登录**：`index.html` 登录标签默认 `active`、登录表单默认显示，注册收进切换，默认不做注册优先。
- **需求 6 · 模型管理 UI 溢出优化**：参数表单新增 `.model-col.narrow`（勾选项自适应窄列）与 `.model-col.full`（长输入占满行尾），「重排序模型 / API 地址 / 长文档阈值」改用 `full` 列并配 `min-width:0`，窄屏纵向堆叠，杜绝超出卡片。
- **验证**：`node --check app.js` 通过；后端 `--reload` 重启成功；API 实测 batch-delete（无效数据集 → 404「知识库不存在」、有效数据集 + 不存在文件 → 200 `{ok,removed:0}`）；浏览器端到端确认复选框列、选中行高亮、「删除选中（1）」按钮出现、模型管理参数字段均完整收进卡片无溢出。静态资源版本号升 `v24`（MD5 哈希自动派生基础上显式维护前端引用）。
- **审查修正**：代码审查发现并修复「全选复选框状态残留」——切换知识库 / 搜索过滤 / 文件清空时，头部全选框可能残留勾选态；现于 `renderKb` 内按可视行同步态（`rows.every(r=>selectedFiles.has(r.id))`），空列表强制置为未勾选。

## 阶段 13 · 检索召回缺陷修复（阈值断崖 + 索引状态不同步）

线上故障：选择「中国石油化工行业人工智能+产业生态大会」提问"参展单位有哪些"，提示"资料中没相关内容"。定位为两个叠乘问题。

- **根因 1（检索断崖）· 相关度阈值过严**：全局 `score_threshold=0.5`，而 DashScope `text-embedding-v3` 在 langchain `similarity_search_with_relevance_scores` 下的相关度普遍落在 0.3–0.44，最高候选仅 0.436 → 0.5 阈值把所有候选全部过滤 → `hits=[]` → LLM 只能答"资料中没相关内容"。**修复**：全局阈值调低至 `0.3`（`settings` 表运行时值 + `.env` 的 `SCORE_THRESHOLD` 同步，保持重启一致；用户可在管理台按需回调）。
- **根因 2（状态不同步）· 异步重建不刷索引状态**：前端走 `POST /datasets/{id}/rebuild`（后台任务），其 `_runner` 只调 `rebuild_index`（仅回填 `parse_status`），未把 `kb_files.status` 置 `indexed`（只有同步版 `/rebuild_sync` 会刷），导致 UI 文件长期显示"待索引"。**修复**：`kb.py` 异步任务完成后补调 `_sync_index_status(datasetId)`，与同步版行为一致。
- **验证**：向量库确认 ds30 有 16 条索引、检索最高命中 0.436；修复后 `hits=6` 且能召回"参会/参展单位名单"；SSE 提问多数可直接列出单位。剩余偶发"未明确提到参展单位"属模型对「参会单位 vs 参展单位」措辞差异的保守判断（底层已恢复命中，多处可直接作答）。`doc` 老格式仍为 `unsupported`（不含正文）。

## 阶段 14 · 参考资料按文件去重聚合

问题：提问返回的"参考资料"中出现重名项——同一文件多个命中片段各渲染一张同名卡片（如"大会日程安排及部分名单.pdf"出现 4 张、"中国石油…大会.pdf"出现 2 张），观感重复。

- **根因**：`app.js` 的 `hitsHtml` 对每条 `hit` 各生成一张 `.hit-card`，标题取 `hit.source`（文件名），同文件多段即多张同名卡片。
- **修复（前端）**：`hitsHtml` 改为按 `hit.source` 分组——每个文件仅一张卡片（`data-i` 取该文件首个命中索引），卡片内以 `.hit-seg[data-hit]` 段形式合并该文件全部命中片段；「参考资料 N」改为文件数；多段文件头部显示「N 段」徽标。内联引用 `[n]` 点击改为优先定位到 `.hit-seg[data-hit="{i}"]`（段级高亮 + 父卡片展开/flash），兼容旧式单段卡片（回退到 `.hit-body`）。`highlightHit` 改段级操作，避免误清整卡 DOM。样式新增 `.hit-count`（文件计数徽标）与 `.hit-seg`（段间虚线分隔 + `cite-hit` 高亮）。
- **验证**：`node --check` 通过；纯函数断言（6 段×2 文件 → 卡片=2、段=6、资料数=2）；浏览器 E2E 确认参考资料区为 2 张无重名卡片、同文件多段合并可展开（其余「内联 [n] 无点击」系模型未输出方括号引用标记，非改动问题）。静态版本号 `v24 → v25`。

## 阶段 15 · OCR 中文识别修复 + 老格式二进制文档解析优化（09-04）

补齐两处未闭环的缺陷，使老二进制 Office（`.doc/.xls/.ppt`）真正可解析并正确回填文件状态。

- **缺陷 1 · OCR 中文乱码**：`_read_image_ocr` 原默认 `tesseract` 英文单语言识别中文图片输出乱码。
  - **实现**：传入 `lang` 参数，默认 `TESSERACT_LANGS`（缺省 `"chi_sim+eng"`，中英混排），可用环境变量覆盖单语言。
  - **验证**：实测生成 120px 高中文图片「中国石油化工大会」，OCR 正确返回「中 国 石 油 化 工 大 会」（修复前为乱码），`chi_sim`/`eng` 语言包均已就位。
- **缺陷 2 · 老格式提取函数未接入分发**：`extractors.py` 已实现 `_read_doc_legacy/_read_xls_legacy/_read_ppt_legacy`（OLE 流启发式 + `xlrd` 精确取 `.xls`），但 `extract_text` 的 `suffix` 分发分支漏写 `.doc/.xls/.ppt`，导致老格式上传后从不提取正文。
  - **实现**：在 `extract_text` 末尾为 `.doc/.xls/.ppt` 分别调用对应 legacy 提取（各带异常兜底返回空串）。
  - **验证**：`extract_text(Path('xxx.doc'))` 等不再抛错、对不存在/空白输入优雅返回 `''`；`olefile/xlrd` 等依赖均已就位。
- **缺陷 3 · 老格式状态一律标 unsupported**：`kb_service._sync_parse_statuses` 对 `LEGACY_UNSUPPORTED` 无条件置 `"unsupported"`，即使已提取出正文。
  - **实现**：改为对老格式文件实际调用一次 `extract_text`，能取出非空正文标 `"ok"`，否则标 `"unsupported"`；`kb_service.py` 引入 `extract_text`。
  - **验证**：`py_compile` 通过（`rag/extractors.py`、`app/service/kb_service.py`）。

> 说明：本阶段不再依赖 `soffice` 转换（受限环境下 `libpng` 报错失败），改走纯 Python（`xlrd` + `olefile`）尽力提取；`.doc/.ppt` 采用 UTF-16 可读片段启发式捞取，成功率依文档结构而定，提取不到不影响上传。

### 阶段 15.1 · 纯二进制 `.doc` 正文噪声治理（实测回执表样例）

真实回执表 1.doc（41984B，WPS 生成）实测：能提取出正文（联系人/邮箱/合作方案/金额等），但整条 `WordDocument` 流启发式会把排版/域格式字节一起捞进来，产生 2/3 版面为 `J帀J漀伀J倀儀` 类交替杂码与 `PAGE \* MERGEFORMAT` 域尾噪声，观感像“提取坏了”。

- **根因**：`_ole_readable_runs` 只要落在 CJK/ASCII 区间就收进 run；Word 二进制的布局字节经 UTF-16LE 误解码后恰好落在可打印区间，且与真实正文连成一片。
- **修复（三层过滤）**：
  - 丢弃「同一字符连续重复≥2」的片段（错位双同码，如 `卋卋`）。
  - 新增 `_alternation_ratio`：丢弃「CJK 类↔其他类逐字符高频互跳」的片段（比率>0.5 判为二进制错位），真实纯中文/纯英文/自然中英混排切换很少。
  - `_read_doc_legacy`：正文在 `PAGE \* MERGEFORMAT` 域标记前结束，新增 `_doc_body_end_marker` 按首个 `MERGEFORMAT` 截断，裁掉域尾噪声。
- **验证**：真实回执表 1.doc 提取由「1120 字符含大量噪声」收敛为「417 字符纯净正文」（联系人 李建超/15724711689、邮箱、合作方案、收款账户、开户银行工行北京幸福街支行、参与单位签章），`has_garbage=False`，切分为 1 个语义完整区块；`py_compile` 通过。

### 阶段 15.2 · 解析格式标准化 + 预览 UI 优化（09-04）

把各类文件的解析标记统一为一致协议，并新增「渲染 / 原文」双视图的文件解析预览，提升查阅体验。

- **解析格式标准化（`rag/extractors.py`）**：
  - 单元格分隔符统一为全角竖线 ` │ `（去掉易与 Markdown 表格混淆的窗口分离），表格/工作表/幻灯片/图注各配统一标记：
    - `.docx` 表格 → `[表格]` 行块；`.docx` 图片图注 → `[图 N：描述]` / `[图片 N]`。
    - `.xlsx` / `.xls` → `[工作表：{名}]` 行块（`│` 分隔单元格）。
    - `.pptx` → `[幻灯片 N]` 行块。
  - `extract_text` 分发保持 `.doc/.xls/.ppt` 老格式尽力提取；`.ppt` 老格式因 OLE 流无法天然分页，暂以整段返回（不强行拆分）。
- **预览 UI 优化（前端）**：
  - `index.html` 新增 `#preview-modal` 弹窗：`preview-tools`（`渲染 / 原文` 切换按钮组）、`#preview-stats`（解析状态统计栏）、`#preview-render`（Markdown 渲染视图）、`#preview-body`（原文视图）。
  - `app.js` 新增 `openPreview / previewView / renderPreviewStats / structureCounts / renderParse`：
    - `renderParse` 把统一标记转可读 Markdown（工作表/幻灯片/表格→标题行，图注→引用块），复用 `renderMd`。
    - `renderPreviewStats` 生成胶囊型统计栏（解析状态、文件大小、字符/行数、工作表/幻灯片/表格/图片节数），状态以 `stat-ok / stat-warn / stat-bad` 着色。
  - `style.css` 新增 `.preview-tabs/.preview-tab`（圆角切换）、`.preview-stats/.preview-stat`（胶囊标签）、`.preview-render`（Markdown 排版、标题品牌色左侧竖条）。
  - 后端 `content` 端点保持返回原始提取文本，前端现场派生统计（无需额外元数据）。
- **验证**：`node --check app.js` 通过；`py_compile rag/extractors.py` 通过；静态资源版本号 `v26 → v27`。

> **补充修正 · 粘连 PAGE 清理**：真实回执表 1.doc 提取后末尾残留「…签章PAGE」（无空格粘连），原 `_strip_word_page_residue` 按空白分词无法剥离。改为端点锚定正则 `(?:PAGE|NUMPAGES|SECTIONPAGES)(?:\s*\\?\*\s*MERGEFORMAT)?\d*$`（raw string，`\\?` 表示可选字面反斜杠、兼容 Word 域代码 `\* MERGEFORMAT`）逐次清理，同时保护正文末尾真实数字（如「RMB3500」「账号314」）。实测 content 端点 `endsPAGE=False`，正文末「签章」保留干净。

## 阶段 16 · 预览 UI 加宽 + 参考资料"不相关"治理（09-04）

两处体验问题：文件解析预览弹窗太窄；最近对话的"参考资料"混杂与问题不相关的来源。

- **预览弹窗加宽**：`.preview-card` 由 `width:min(680px,92vw); max-height:82vh` 改为 `width:min(1400px,96vw); height:94vh; margin:auto` 全屏化居中，`.preview-card .kb-head h3` 超长截断。静态版本 `v27 → v28`。
- **参考资料不相关 · 根因诊断**（实测复现"李建超电话多少"）：
  - **混合检索被关闭**：DB settings 存 `hybrid_enabled='0'`（阶段 13 曾开启验证过字面精确召回，后被关）。纯稠密向量下，人名+电话这类字面精确匹配严重漂移——真正含答案的「回执表1.doc」只排第三（0.24），反而把不含"李建超"的「委员名单 PDF」片段（含"李…斌"等近似姓氏）排到第一。
  - **阈值过松**：settings 存 `score_threshold='0.2'`（覆盖 .env 的 0.3），0.2–0.29 的弱相关片段全进参考资料。
  - **量纲**：`text-embedding-v3` 对名单式文本相关度天然偏低（0.2–0.4），且 embedding 无法精细区分近似人名，属固有局限。
- **修复**：
  - 管理台 `PUT /api/admin/model` 持久化 `hybrid_enabled=1`、`hybrid_method=weighted`、`score_threshold=0.3`。
  - `rag/retriever._hybrid_retrieve`：融合后再按相关度下限过滤（`score >= threshold`），剔除混入的低相关碎片；**全滤则回退原始结果**，避免重蹈"查不到/无参考资料"。
- **验证**：
  - "李建超电话多少" → 参考资料收敛为唯一一条「回执表1.doc（含李建超 15724711689）」，此前混入的参会人员/征文要求/管网名单等无关片段全部剔除；
  - "参展单位有哪些""大会日程是怎样的" 相关分数 0.6–1.0 的片段正常保留，无误删；
  - `py_compile rag/retriever.py` 通过；后端重启 `HTTP 200`。

> **阶段 16 补充 · 浏览器实测漏网之鱼**：用 TRAE-browseruse 打开回执表1.doc 预览实测，弹窗实际计算宽度仍只有 **320px**。根因：预览元素 class 同时含 `.team-modal-card`（`width:320px`，位于样式表更靠后、优先级同为单类）与 `.preview-card`（`96vw`），级联按文件顺序后者胜出，320 覆盖了 96vw。修正为**更高特异性选择器** `.kb-card.team-modal-card.preview-card { width:96vw; max-width:1400px; height:94vh; max-height:94vh; }`。实测 872px 视口下弹窗计算宽度 = **837px（≈96vw）**（此前 320），"渲染/原文"双视图切换正常、统计栏（解析状态/文件大小/字符行数）正常，控制台无应用侧报错。静态版本号经 `_static_version` 内容哈希自动换新，无需手改。

## 阶段 17 · 新一轮优化 N1–N8 全部落地（09-05 · 多用户稳定 + 回答质量）

> 基于对 `app/`、`rag/` 核心链路的代码级评审，把 OPTIMIZATION.md 第七章 N1–N8 全部实现并回归。

- **N1 · BM25 索引缓存化**（性能）：`BM25` 新增「词→文档下标」倒排索引 `_postings`，`score()` 只遍历命中文档（复杂度 O(N)→O(命中)）；`retriever.py` 新增 `_corpus_cache`/`_bm_cache` 按 `dataset_id` 子集缓存语料与索引（上限 64 键），`rebuild_dataset/clear_dataset` 后 `_invalidate_caches()` 失效刷新。验证 `test_rrf.py` 仍绿。
- **N2 · 检索读写锁分离**（性能·并发）：`_RWLock`（读读并发、写写/读写互斥、写可重入防死锁），查询走 `reader()`、重建走 `writer()`，消除多用户并发提问被单一 RLock 串行化。实机验证并发查询无中间态。
- **N3 · 中文 token 预算修正**（质量）：`_est_tokens` 改中文感知混合估算（CJK/全角按字 ≈1 token，其余 ÷4），修正 `len//4` 对中文低估约 4 倍导致的预算形同虚设/超窗截断。
- **N4 · 外部 OpenAI 兼容 Rerank 通道**（质量·新增）：`rerank_hits` 新增 `channel="external"` 分支，走 OpenAI 兼容 `/v1/rerank`（`_external_rerank` POST `model/query/documents/top_n`，解析 `results|data` 的 `index+relevance_score|score` 重排并归一化，超时 20s；失败/未配置安全降级原序）；`RERANK_CHANNEL/RERANK_EXTERNAL_URL/RERANK_API_KEY` 入 `config`/`runtime_settings`，管理台「重排序」区新增通道下拉（本机 CrossEncoder｜外部 API）+ 地址/Key（Key 不回显、留空保持不变）。新增 `test_rerank_external.py` 4 用例全绿；实机 admin PUT/GET 验证 `rerank_channel/external_url/has_key` 持久化与透出。
- **N5 · 上下文+历史总预算联合校验**（质量）：`build_messages` 加 `total_budget`，`cap=min(history_budget,total-overhead)` 超限先压历史、资料保底完整；`stream_answer_async` 传 `total_budget=context*2`。
- **N6 · 阈值默认值一致性**：`config.py` `SCORE_THRESHOLD` `0.5→0.3`，与运行时可调及 DB settings 覆盖一致，防空库时旧默认"复活"导致检索过严。
- **N7 · 流式去重**：删除同步 `stream_answer`，统一保留 `stream_answer_async`（AsyncOpenAI 缓存复用），`rg` 确认无同步调用残留。
- **N8 · 会话按知识库分类管理**（重点功能·体验）：`sessions` 表 `ALTER TABLE ADD COLUMN dataset_id`（存量 NULL）；`SessionCreate` 带 `dataset_id` + `_validate_bind_dataset` 校验归属；`ask` 新建会话绑定所选库、续聊 `_resolve_session_datasets` 强制沿用绑定库；前端左侧**按知识库两级分组**（`renderSessions`+`dsName/dsIco`+`__all__` 组），新建会话先选库、`openSession` 同步绑定库。实机 GET `/api/chats/sessions` 返回 `dataset_id`。
- **回归**：`test_rrf.py`/`test_chunk_params.py`/`test_p111.py`/`test_p112.py`/`test_rerank_external.py` 通过；`node --check app.js` 通过；后端重启 `HTTP 200` 健康检查通过。静态资源版本由 `_static_version` 内容哈希自动推导（改 `index.html` 为裸引用，去掉叠加工 suffix）。

## 阶段 18 · 会话知识库选择交互优化（N9 · 体验，借鉴 TraeWork）

> 收到反馈：选择会话知识库的 UI 不够便捷；且希望**只在第一次新话题时才选知识库**，进入会话后固定在该分组的知识库。

- **现状问题**：输入框上方始终有一个**可随时更改**的全局下拉框 `#ds-select`；会话与知识库虽已在 N8 绑定，但 UI 仍然允许任意时刻改选，容易误解当前检索范围。
- **落地**：
  1. **空状态（新话题）知识库选择卡片**：仅在「新建会话、尚未进入会话」时，页面中央展示「从哪个知识库开始对话？」+ 知识库卡片组（`ds-chip`，首项「全部知识库」+ 各知识库），点击高亮选中；底部输入框上方同步为带数据库图标的小胶囊下拉（`ds-tag-new`）。
  2. **进入会话后锁定为只读标签**：一旦发送形成会话，底部切换为 `ds-tag-fixed`（🔒 锁图标 + 该会话绑定库名）**不可编辑、不可再改**；多会话切换时锁定标签随会话绑定库独立显示。
  3. 前端新增 `state.pendingDs`（待绑定库），`updateComposerDs()` 统一在「新话题 / 会话」视图间切换，`#ds-select` change 与新话题卡片点击都写回 `pendingDs`；`send()` 新建会话时用 `pendingDs` 绑定，续聊由后端 `_resolve_session_datasets` 强制沿用绑定库。
- **验证**：`node --check app.js` 通过；浏览器 E2E 全量 PASS——空状态出现选择卡片、点击「中国石油化工行业人工智能+产业生态大会」高亮并与底部胶囊同步、发送问题后切换为锁定的该库标签并隐藏下拉、新建另一会话选「我的知识库」发送后锁定该库、切回团队会话锁定标签独立正确；控制台无应用侧 JS 报错（仅一条流式请求中断 `ERR_ABORTED` 属预期）。
- **阶段 18 补充（显示"知识库#N"缺陷修复）**：左侧会话分组一度把绑定库显示为「知识库#1」「知识库#30」而非真名。根因：`showApp()` 先发 `loadSessions()`、其回调 `renderSessions()` 用 `dsName()` 分组时，异步 `loadDescriptors()` 拉取的知识库 `state.datasets` 尚未就绪 → `dsName` 找不到 id 回退为 `知识库 #id`；且 datasets 加载完成后未重绘会话分组，导致占位持久化。修复：① `loadDescriptors()` 加防重入 promise guard，并在 `state.datasets` 就绪后调用 `renderSessions()` 重绘分组；② `loadSessions()` 开头若 datasets 为空则先 `await loadDescriptors()` 再渲染，让首屏即显示正确名、避免短暂占位闪烁。验证：强刷新登录后，DOM 读取分组 g-label 为「我的知识库」「中国石油化工行业人工智能+产业生态大会」「全部知识库」，无任何 `知识库#N` 占位，控制台无 JS 报错。

## 阶段 19 · WeKnora 借鉴：推荐追问 + 引用抽屉 + RAG 分阶段进度（09-15 · W1/W3/W2）

> 借鉴 Tencent/WeKnora 的体验层设计，三项均为**不改检索语义**的前端/接口增量，贴合「少而精 + 惊喜感」的演示诉求。实现顺序按 W1 → W3 → W2（低风险高价值先行）。

- **W1 · 推荐问题 + 答后追问动态化**：后端 `rag_service.recommend_questions_async()` 基于「用户提问 + 助手回答 + 命中资料」调用 LLM 生成 3 个可点击追问，失败自动回退硬编码三问，不阻塞主回答；`chats.py@ask` 在 `done` 前发 `suggest` 事件。前端 `followUpsHtml()` 优先渲染动态追问，历史消息 / 推荐被关时回退旧三问；空状态选中知识库后展示针对性引导问题。新增 `SUGGEST_QUESTIONS` 配置 + 管理台「建议追问」开关（`runtime_settings` + `admin.py`）。
- **W3 · 引用抽屉 / 浮层**：命中后不再内嵌展开来源卡，改为「参考资料 N」胶囊触发器（`.cite-trigger`），点击打开右侧固定抽屉（`.ref-drawer`）+ 遮罩（`.ref-overlay`），卡住 `Esc`/遮罩/关闭按钮收起；内联引用段仍支持定位并高亮（P1-2 命中句）。还原对话区纯净观感。
- **W2 · RAG 分阶段进度条**：`chats.py@ask` 将检索移入 SSE 流内，先发 `stage:retrieve` → 检索完 `retrieval` → `stage:generate` → `delta` → `suggest` → `done`；流内错误发 `stage:error` 并回显通用文案。前端 `showPipelineBar()/hidePipelineBar()` 渲染 `pipeline-bar`，逐段点亮「检索中 → 生成中」，回答完成自动收起、失败标红。用户不再误以为长库 / 大模型下「卡住」。
- **验证**：`node --check app.js` 通过；`py_compile` 核心文件通过；`import app.main` 冒烟通过；`pytest 40 passed`（新增断言——SSE 含 `stage:retrieve`/`stage:generate`/`suggest` 且动态追问文本正确）。静态版本号由 `_static_version` 内容哈希自动推导。

## 阶段 20 · 推理型模型适配 + DeepSeek-V4.1-Flash 接入（09-15）

> 用户要求把激活模型切换为 DeepSeek-V4.1-Flash。实测 DeepSeek API 不支持该字符串，合法 ID 为 `deepseek-flash` / `deepseek-v4-pro`；故显示名保留「DeepSeek-V4.1-Flash」，API 模型 ID 用 `deepseek-flash`。并顺手修复了推理型模型在应用里「读到空正文」的兼容问题。

- **根因（推理型模型读不到输出）**：`deepseek-flash` 是推理型模型，其输出放在 `reasoning_content`，`message.content` 为空；且推理内容会计入 `max_tokens` 预算。应用两处只读 `content` → 拿到空串 → 兜底：①流式主回答 `stream_answer_async` 读 `delta.content`；②动态推荐 `recommend_questions_async` 读 `message.content` 且 `max_tokens=200` 被思考过程吃光导致 content 为空。
- **落地**（`app/service/rag_service.py`）：
  1. 新增 `_delta_text()` / `_message_text()`：`content` 为空时回退 `reasoning_content`（对普通模型行为不变）。
  2. 推荐 `max_tokens` 200 → 512（给推理预留预算，确保最终正文能返回）。
  3. 新增 `_extract_json_array()`：对推理链夹杂叙述文本的场景，按括号配平截取第一个 `[...]` 解析，兼容 Flash 的非纯 JSON 输出。
- **配置**：激活模型 config(id=9) 改为 `model=deepseek-flash`、`display_name=DeepSeek-V4.1-Flash`、base_url `https://api.deepseek.com/v1`，Key 沿用 `.env` 有效值。
- **验证**：离线直连 `recommend_questions_async` 用 Flash 返回 3 个紧贴上下文的动态追问（如「已有会话如何切换知识库」「按库分组后左侧怎么展示」「跨库污染与变通方案」）；`stream_answer_async` 能出正文；`pytest 40 passed`；`import app.main` 通过。
- **取舍提示**：推理型模型作为对话模型时，**主回答会输出思维链**（观感偏「思考过程」而非精炼答案），适合演示「推荐追问」等推理能力；若追求干净利落的问答正文，对话模型仍建议 `deepseek-chat`（`deepseek-v4-pro` 亦可）。切换仅需改动管理台激活模型，无需重启。
