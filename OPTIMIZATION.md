# MagicerRAG 优化清单

> 本文档沉淀前端/检索的对齐优化计划，便于逐项跟进与交接。
> 对齐对象①：开源 RAG 引擎 [RAGFlow](https://github.com/infiniflow/ragflow) 的三大方法论——**模板化可控分块、引用溯源、多路召回 + 融合重排**。
> 对齐对象②（章节八起）：腾讯 [WeKnora](https://github.com/Tencent/WeKnora) 可低成本复用的体验能力——**推荐问题 + 答后追问、RAG 分阶段进度、引用抽屉**。
> 每项标注**现状**，避免重复已落地能力；**状态**用勾选跟踪，完成后同步到 `DEVLOG.md`。

***

## 一、现状盘点（避免重复推荐）

| 能力                       | 现状                                            | 结论           |
| ------------------------ | --------------------------------------------- | ------------ |
| 引用可追溯（内联 `[n]` + 来源卡片回查） | ✅ 已实现，P1-2 已增强为命中句子高亮                          | 已对齐   |
| 多路召回（稠密 + BM25 混合）       | ✅ 已实现（`rag/hybrid.py`，`HYBRID_ENABLED=0` 默认关） | 需开放配置        |
| 重排（Cross-Encoder rerank） | ✅ 已实现（`rag/rerank.py`，可选开关）                   | 需开放配置        |
| Markdown 标题感知分节          | ✅ 已实现（`ingestor.py`，保留 `section` 节名）          | 是"模板化"雏形     |
| 分块参数用户化 / 分块预览           | ✅ 已实现（09-04，知识库级模板/参数 + `/chunks-preview`） | **P0 完成**  |
| 融合算法 RRF 排名融合            | ✅ 已实现（09-04，`fuse()` 支持 `method=rrf`）          | **P0 完成**       |
| 混合/重排可视化配置（管理中心开关+参数）    | ✅ 已实现（09-04，「融合方式」加权/RRF 下拉 + 混合开关/α + 重排开关/模型） | **P1 完成** |
| 引用高亮定位（`[n]` → 命中句子）     | ✅ 已实现（09-04，P1-2，`_bestSpan` + `mark.cite-hit`） | **P1 完成** |
| 图 / 表格多模态解析              | ✅ 已实现（09-04，P2-2，Word 内嵌图片图注 `[图 N：描述]`；PDF 图注留作远期） | **P2 完成** |

***

## 二、P0 · 高价值 / 低风险（推荐先做）

### P0-1 分块策略用户化（对标"模板化可控分块"）

- [x] **已实现 + 已验证（09-04）**：`rag/ingestor.py` 的 `chunk_documents()` 新增 `mode`（`markdown` 标题感知默认 / `recursive` 递归 / `whole` 大段不拆），`load_and_chunk()` 透传 `chunk_size/overlap/mode`；`app/db.py` 的 `dataset_params` 新增 `split_mode/chunk_size/overlap`（NULL=回落全局默认）；`app/routers/kb.py` 扩展 `GET/PUT /params` 读写分块参数 + 新增 `GET /datasets/{id}/chunks-preview` 只读预览接口；`app/service/kb_service.rebuild_index()` 读取该库分块参数透传 `ingestor`；前端新增「分块设置 / 分块预览」按钮与模态框。**验证**：`python test_chunk_params.py` 全绿（写入/回读、`recursive` 预览生效、非法模板 422、`whole` roundtrip、默认回落）；浏览器自动化确认两模态框打开、三模板 + 参数输入框齐全、预览区块正常渲染。

- [x] **目标**：开放**知识库级**分块配置，让"怎么切"可控、可解释。

- [x] **落地**：
  - `ingestor.py` 在已有标题分节基础上透传 `chunk_size / overlap`；

  - 每知识库新增「分块模板」选项：`标题感知`（默认，兼容老行为）/ `递归切分` / `大段不拆`；

  - 重建索引 / 上传时携带该参数，前端知识库设置面板提供选择。

- [x] **影响面**：`rag/ingestor.py` + 知识库 schema + 上传/重建接口 + 前端设置面板。

- [x] **工作量**：中　**风险**：低（默认值保持老行为不变）。

### P0-2 融合算法支持 RRF 排名融合

- [x] **已实现 + 已验证（09-04）**：`rag/hybrid.py` 的 `fuse()` 新增 `method="weighted"|"rrf"`，`rrf` 走 RRF 排名融合 `Σ 1/(k+rank)`（k=60）；`retrieve()` / `_hybrid_retrieve()` 透传 `hybrid_method`。验证：`python test_rrf.py` 通过。

- [ ] **目标**：提供与 RRF（Reciprocal Rank Fusion）对齐的**排名分融合**，对术语化/指令词问题更稳健。

- [ ] **落地**：
  - `fuse()` 增加 RRF 模式：`score = Σ 1/(rank + k)`，`k=60`；

  - 默认仍走加权、可切换，`retriever.py` 透传 `fusion` 参数（`weighted | rrf`）。

- [ ] **影响面**：`rag/hybrid.py` + `rag/retriever.py`（新增开关，不清现有逻辑）。

- [ ] **工作量**：小　**风险**：低。

***

## 三、P1 · 中价值（按需）

### P1-1 混合检索 / 重排纳入可视化配置

- [x] **已实现 + 已验证（09-04）**：管理中心「模型管理」检索参数区新增「融合方式」下拉（`weighted` 加权 α / `rrf` 排名），连同已有的「混合检索开、混合权重 α、重排开关+模型」一并可视化配置、即时生效。端到端实现：`config.py HYBRID_METHOD` 默认 `weighted` → `runtime_settings` 透出/编辑 → `admin.py` `GET/PUT /model` 读写 + `pattern` 校验 → `rag_service.retrieve` 传 `hybrid_method`。验证：`python test_p111.py` 全绿（默认 weighted、PUT rrf 生效、非法值 422、切回生效、`load()` 透出）；浏览器自动化确认三控件齐全、切 RRF 保存 toast「参数已保存」、刷新持久化、Console 无报错、无 4xx/5xx。静态资源版本 v21→v22。

- [x] **落地**：在**管理中心 → 模型/检索设置**暴露「混合检索」「融合权重 alpha」「加权 / RRF 切换」「重排开关 + 模型」，交互式调整、即时生效。

- [x] **价值**：让已有的混合 + 重排能力真正在用，而非停留在代码里。

- [x] **工作量**：小　**风险**：低。

### P1-2 引用高亮定位增强（强化追根溯源）

- [x] **已实现 + 已验证（09-04）**：点击内联 `[n]` 后，来源卡片内会高亮「与问题最相关的句子」并滚动到可视区。实现：`app.js` 新增 `_tokenize()` / `_bestSpan()`（拆句 + 问题词项命中打分选出命中句子）/ `highlightHit()`（清除旧高亮后仅包住命中句，`scrollIntoView` 居中），引用点击事件回传当前用户问题文本；`style.css` 新增 `mark.cite-hit`（品牌色淡底 + 底部内嵌下划线）。**验证**：`node --check app.js` 通过，浏览器端到端确认高亮与定位生效。

- [x] **落地**：来源卡片中用片段高亮当前 `[n]` 对应内容，让「答案 → 证据」更直观（命中段落由问题词项打分就地推断，无需后端回传偏移）。

- [x] **影响面**：前端 `static/app.js` + `static/style.css`。

- [x] **工作量**：小-中　**风险**：低。

***

## 四、P2 · 进阶 / 可选尝鲜

### P2-1 分块结果预览与手动微调

- [x] **已实现 + 已验证（09-04，预览标红部分）**：重建索引前「分块预览」列表会**标红/标黄提示异常块**（空块=红「空块」、超长=橙「超长 N」）。实现：后端 `/chunks-preview` 区块响应新增 `length` 字段；前端按 `c.length` 与 `effective.chunk_size` 判定，`kp-warn` 警示标签 + `kp-long`/`kp-empty` 边框淡底。**验证**：`python test_p112.py`（preview 含 `length` 断言）通过，浏览器端到端确认异常块带标。
- [ ] **手动微调作为远期愿景**（较重编辑器交互），勿与 P0-1 的「模板 + 参数」混做。

### P2-2 图表多模态解析

- [x] **已实现 + 已验证（09-04，Word 图注）**：`extractors.py` 为 Word 内嵌图片可选生成「图注描述」并作为独立段并入文本（有 `descr`/Alt 描述 → `[图 N：描述]`，无描述 → `[图片 N]` 图位占位），由 `config.py` 新增 `DOC_IMAGE_CAPTION`（默认开）控制。**验证**：`python test_p112.py` 全绿（有/无描述/无图三种用例）。
- [ ] **PDF 图注解析作为后续延伸**：当前仅覆盖 DOCX `inline/anchor` 图形节点；PDF 图表语义提取可后续按需补做（依赖 OCR/版面解析，成本较高）。

***

## 五、建议实施顺序

1. `P0-2` RRF 融合（改动最小、见效快）——✅
2. `P0-1` 分块参数用户化（核心理论收益）——✅
3. `P1-1` 混合 / 重排可视化配置——✅
4. `P1-2` 引用高亮定位——✅
5. `P2-1` / `P2-2` 按需——✅（P2-1 预览标红 / P2-2 Word 图注；手动微调与 PDF 图注留作远期）

***

## 六、状态跟踪

- [x] P0-1 完成　- [x] P0-2 完成

- [x] P1-1 完成　- [x] P1-2 完成

- [x] P2-1 完成　- [x] P2-2 完成

> 每一项落地后：更新对应勾选状态，并在 `DEVLOG.md` 追加一条（含根因 / 实现 / 验证 / 版本号）。全部六项已于 09-04 完成并同步 DEVLOG（阶段 8–11）。

***

## 七、新一轮优化方案（09-05 · 多用户稳定运行 + 回答质量）

> 背景：面向 **10 用户的并发稳定运行** 与 **回答质量** 的代码级评审，基于对 `app/`、`rag/` 核心链路的实际审查提出。
> 沿用上文勾选风格，新增独立编号 `N1–N7`，避免与已完成的 P0–P2 混淆。

### 7.1 现状再盘点（本轮聚焦）

| 能力 | 现状 | 结论 |
| --- | --- | --- |
| BM25 混合召回 | ✅ 已实现（`rag/hybrid.py`，`HYBRID_ENABLED` 运行时可开） | 但有性能隐患 → N1 |
| 重排 Rerank | ✅ 已实现（`rag/rerank.py`，仅**本地 CrossEncoder**，默认关） | 缺外部 `v1/rerank` 通道 → N4 |
| 检索并发 | ⚠️ 单把 RLock 串行化所有读取 | 多用户瓶颈 → N2 |
| 上下文 token 预算 | ⚠️ `_est_tokens` 按「字符÷4」低估中文 | 预算失真/截断 → N3 |
| query rewrite | ✅ 已实现（`rag_service.rewrite_question`，默认关） | 可评估开启 → N5 |
| 阈值默认值 | ⚠️ `config.py` `SCORE_THRESHOLD=0.5`，与预期默认 0.3 不一致 | N6 |
| 流式生成 | ⚠️ 同步/异步双份重复实现 | N7 |

### 7.2 N1 · BM25 索引缓存化（高价值 · 低风险）【性能】

- [x] **已实现 + 已验证（09-05）**：`BM25` 内新增「词→文档下标」**倒排索引** `_postings`，`score()` 只遍历命中文档，复杂度 O(全库)→O(命中)；`retriever.py` 新增 `_corpus_cache`/`_bm_cache` 按 `dataset_id` 子集缓存语料与 BM25 索引（上限 64 键），`rebuild_dataset/clear_dataset` 后 `_invalidate_caches()` 失效刷新。**验证**：`test_rrf.py` 仍绿。
- [ ] **现状**：`rag/retriever._hybrid_retrieve()` 每次提问都新建 `BM25(corpus)`（`rag/hybrid.py`），把整库文档重新分词、重算 DF；`BM25.score()` 又对每条查询 token 遍历全量 `_doc_tokens`。知识库越大，单次提问 CPU 越重，多用户并发更明显。
- [ ] **目标**：BM25 稀疏索引（词 → 文档的**倒排表**）建成缓存，按数据集重建时失效刷新；查询只做倒排查找 + 打分，复杂度从 O(全库) 降到 O(命中)。
- [ ] **落地（轻量版，推荐先做）**：
  - 在 `BM25` 内额外构造倒排表 `{term: [doc_idx]}`，`score()` 仅遍历查询词命中的 doc，跳过无关文档；
  - 按 `dataset_id` 缓存索引对象（复用 `_corpus_cache` 的键，重建/上传后失效），避免重复分词与 DF 计算。
- [ ] **落地（进阶版，可选）**：换用 `bm25s` / `rank_bm25` 稀疏向量库，几行集成，海量文档也不怕；同时实现 `_content_hash` 稳定去重。
- [ ] **影响面**：`rag/hybrid.py` + `rag/retriever.py`（不动检索语义，只改性能）。**建议优先级：①**
- [ ] **工作量**：中　**风险**：低。**验证**：`test_rrf.py` 仍绿；用大语料做一次「缓存命中 vs 重建」耗时对比。

### 7.3 N2 · 检索读写锁分离（中价值 · 有并发风险）【性能】

- [x] **已实现 + 已验证（09-05）**：`retriever.py` 新增 `_RWLock`（读读并发 / 写写·读写互斥，写模式可重入防死锁），查询走 `_rw.reader()`、重建 `clear_dataset/rebuild_dataset/get_or_create_store` 走 `_rw.writer()`，解决多用户并发提问被单一 RLock 串行化的问题。**验证**：并发重建 + 查询压力场景无中间态。
- [ ] **现状**：`retriever.py` 仅一把 `_store_lock`（RLock），为隔绝重建 clear+add，把所有**降维读取**也串行化；10 用户并发提问会被同一把锁排队。
- [ ] **目标**：读读并发、写写/读写互斥，重建索引时独占、普通查询并发放行。
- [ ] **落地**：用 `threading.RLock` 之外的**读写锁**（或 `asyncio` native lock）包裹 `get_shared_store / rebuild_dataset` 与查询路径，区分读临界区与写临界区。
- [ ] **影响面**：`rag/retriever.py`（并发语义变更，需回归）。**建议优先级：②**
- [ ] **工作量**：中　**风险**：中（并发竞态需小心）。**验证**：并发重建 + 查询压力脚本无中间态。

### 7.4 N3 · 中文 token 预算修正（高价值 · 低风险）【质量】

- [x] **已实现 + 已验证（09-05）**：`rag_service._est_tokens` 改为**中文感知混合估算**——CJK 汉字/全角按字≈1 token、其余按字符÷4，修正旧 `len//4` 对中文低估约 4 倍的问题；`_fit_history/fit_context/build_messages` 逻辑不变仅换估算函数。**验证**：中文长文本估算值显著高于旧值、预算内保留条数正确。
- [ ] **现状**：`rag_service._est_tokens()` 用 `len(text) // 4` 估 token，对中文过于乐观（中文约 1–2 字≈1 token），`CONTEXT_TOKEN_BUDGET`（默认 4000）实际可能塞入 1 万+ token 中文，**裁剪形同虚设、甚至超上下文被截断**。
- [ ] **目标**：让预算估算贴近真实 token 数，保证"裁剪进 LLM 的资料"受控。
- [ ] **落地**：`_est_tokens` 改为**中文按字数计 + 英文/数字按 `÷4` 计**的混合估算（或引入 `tiktoken`）；`_fit_history / fit_context / build_messages` 逻辑不变，只换估算函数。
- [ ] **影响面**：`app/service/rag_service.py`（仅估算函数）。**建议优先级：①**
- [ ] **工作量**：小　**风险**：低。**验证**：构造中文长文本断言估算值明显高于旧值、预算内保留条数正确。

### 7.5 N4 · 外部 OpenAI 兼容 Rerank 通道（中高价值 · 中风险）【质量】

- [x] **已实现 + 已验证（09-05）**：`rag/rerank.py` 的 `rerank_hits` 新增 `channel="external"` 分支，走 **OpenAI 兼容 `/v1/rerank`**（`_external_rerank`：POST `model/query/documents/top_n`，解析 `results|data` 的 `index+relevance_score|score` 重排并归一化，超时 20s），未配置/失败时安全降级为原召回顺序；`config.py`/`runtime_settings.py` 新增 `RERANK_CHANNEL/RERANK_EXTERNAL_URL/RERANK_API_KEY`，管理台「重排序」区新增通道下拉（本机 CrossEncoder｜外部 API）+ 地址/Key 配置（Key 不回显、留空保持不变）。**验证**：`test_rerank_external.py` 4 用例全绿（重排/空结果/连接失败降级/local 回退）；`node --check app.js`；admin PUT 持久化 + GET 透出 `rerank_channel/external_url/has_key` 实测生效。
- [ ] **现状**：`rag/rerank.py` 仅支持本地 `sentence-transformers` CrossEncoder，默认关闭（`RERANK_ENABLED=0`）；国内容器装它=重依赖+下载模型，导致该能力停在代码里。
- [ ] **目标**：补齐**即开即用**的重排通道，直接改善"参考资料混入不相关片段"的既有痛点。
- [ ] **落地**：在 `rerank_hits` 上分支——本机模型优先，其次增加 **OpenAI 兼容 `/v1/rerank` 外部服务**（`ExternalReranker` 模式：POST `model/query/documents/top_n`，按 `result.index/score` 重排）；管理台 `rerank_model` 追加"外部 API"类型与地址/Key 配置。
- [ ] **影响面**：`rag/rerank.py` + `runtime_settings` + 管理中心模型配置 + 前端。**建议优先级：③**（作为后续功能增量）
- [ ] **工作量**：中-大　**风险**：中（依赖外部服务可用性，需超时/降级）。**验证**：mock `/v1/rerank` 服务断言重排顺序与降级回退化。

### 7.6 N5 · 历史 + 上下文总预算联合校验（中价值 · 低风险）【质量】

- [x] **已实现 + 已验证（09-05）**：`build_messages` 增加 `total_budget` 参数做**联合总预算校验**——`history_budget + 资料 + system 开销 ≤ 总预算`，超限时 `_fit_history` 以 `cap = min(history_budget, total_budget-overhead)` 更紧压缩历史、资料保底完整（遵循「宁可截历史不截资料」）；`stream_answer_async` 传入 `total_budget=context_token_budget*2`。**验证**：填入超长历史断言历史被压缩、当前资料完整进入。
- [ ] **现状**：`build_messages` 用固定 `history_budget=8000` 注入历史，而 `CONTEXT_TOKEN_BUDGET`（默认 4000）单独管资料；两者与 system 相加可能超模型 context window——**宁可截历史，也不可截当前资料**。
- [ ] **落地**：
  - 在 `build_messages` 内做一次**总预算校验**：`history_budget + 资料预算 + system/对话开销 ≤ 模型上下文`；
  - 超限时优先压缩历史（`_fit_history` 更紧），资料保底完整；
  - 顺带评估将 `query_rewrite`（多轮追问改写，`rag_service.rewrite_question`）按需开启以缓解多轮漂移。
- [ ] **影响面**：`app/service/rag_service.py`。**建议优先级：②**
- [ ] **工作量**：小-中　**风险**：低。**验证**：填入超长历史断言历史被压缩、当前资料完整进入。

### 7.7 N6 · 阈值默认值一致性（小改动 · 快收益）【一致性】

- [x] **已实现 + 已验证（09-05）**：`config.py` `SCORE_THRESHOLD` 默认值由 `0.5` 同步为 **0.3**，与运行时可调/DB settings 覆盖及 DEVLOG（阶段 13）一致；运行时覆盖机制保持不变。**验证**：清空 settings 后 `load()` 返回 0.3。
- [ ] **现状**：`config.py` `SCORE_THRESHOLD=0.5`，但既定预期默认已调至 **0.3**（当前靠 DB `settings` 运行时覆盖兜底）；换环境或清库时旧默认会"复活"导致检索过严。
- [ ] **落地**：将 `config.py` 的 `SCORE_THRESHOLD` 默认值同步为 **0.3**，与 `runtime_settings._DEFAULTS` 及 DEVLOG（阶段 13）一致；保留运行时覆盖机制。
- [ ] **影响面**：`config.py`（默认值）。**建议优先级：①**
- [ ] **工作量**：极小　**风险**：低。**验证**：清空 settings 后 `load()` 返回 0.3。

### 7.8 N7 · 合并同步/异步流式重复实现（小改动 · 可维护性）【可维护性】

- [x] **已实现 + 已验证（09-05）**：`rag_service` 删除同步 `stream_answer`，统一只保留 `stream_answer_async`（AsyncOpenAI 缓存复用）；`chats.py@ask` 为唯一调用方直接 async 消费。全局 `rg` 确认无同步调用残留。**验证**：SSE 问答流式 + `node --check app.js`。
- [ ] **现状**：`rag_service.stream_answer`（同步）与 `stream_answer_async`（异步）几乎双份，自建 `_ahttp` 客户端缓存；逻辑重复、易漂移。
- [ ] **落地**：统一为**单个 async 实现**（AsyncOpenAI），删去同步旧版与重复清点；有同步调用方则薄封装走 `run_in_executor`。
- [ ] **影响面**：`app/service/rag_service.py`。**建议优先级：③**
- [ ] **工作量**：小　**风险**：低。**验证**：SSE 问答 + `node --check` 前端回归。

### 7.9 N8 · 会话按知识库分类管理（重点功能 · 体验 + 结构化）【体验】

- [x] **已实现 + 已验证（09-05，重点功能）**：
  - **后端**：`sessions` 表新增 `dataset_id INTEGER`（`db._migrate` 用 `ALTER TABLE ADD COLUMN` 平滑加列，存量会话保持 `NULL`）；`SessionCreate` 带 `dataset_id`，`create_session` 写入并用 `_validate_bind_dataset` 校验库属用户可访问集合；`_session_out` 透出 `dataset_id`；`ask` 在新建会话时绑定所选库、续聊时 `_resolve_session_datasets` 强制沿用会话绑定库（忽略前端 `dataset_ids`）。
  - **前端**：左侧会话列表**按知识库两级分组**（`renderSessions`，`dsName/dsIco` 映射库名/图标，`__all__`=全部知识库组）；新建会话先选知识库再进入对话；`openSession` 打开会话时同步知识库选择器为绑定库。
  - **验证**：`runtime_settings.load` 正常、`db._migrate` 后旧库不报错；实机 GET `/api/chats/sessions` 返回 `dataset_id` 字段；新建会话绑定库 A → 提问只检索库 A，续聊不带 dataset 仍只用库 A，跨库两会话上下文互不串扰；`node --check app.js`。
- [ ] **现状**：会话与知识库**无绑定**——
  - `sessions` 表只有 `user_id/title/created_at`，**没有 `dataset_id`**（`app/db.py` §42）；
  - `AskIn.dataset_ids` 由前端**每次提问时传入**（`app/schemas.py`），空值即检索"全部有权限知识库"（`app/routers/chats.py@ask`）；
  - 会话在**首次提问时懒创建**，标题取问题首句（`chats.py@ask`），左侧会话列表**平铺展示、无分类**；
  - 多轮上下文已实现（R-01 取 `list_recent_messages(sid)` 历史 + R-02 `rewrite_question`，`chats.py`），本项不改这条链路。
- [ ] **目标**：
  1. **新建会话时才选择知识库**，会话与知识库**建立绑定**；
  2. 左侧**按知识库分组**展示会话（两级：知识库 → 该库下的会话）；
  3. **续聊（二次会话）不再选择知识库**，沿用该会话绑定库；
  4. 保持支持上下文连续，且历史检索天然限定在同一知识库内，避免跨库上下文污染。
- [ ] **落地 / 后端**：
  - `sessions` 表新增 `dataset_id INTEGER`（`app/db.py._migrate` 用 `ALTER TABLE ADD COLUMN` 平滑加列，存量会话可为 `NULL`）；
  - `SessionCreate` 增加 `dataset_id` 字段；`create_session` 写入并校验该库在用户可访问集合内；`_session_out` 透出 `dataset_id`。
  - `AskIn` 绑定改版：**续聊**（带 `session_id`）时忽略前端 `dataset_ids`，改用其会话绑定的 `dataset_id`；**仅新建会话**允许传 `dataset_ids` 建立绑定。
  - 兼容兜底：存量/未绑定会话按"检索全部可访问库"处理（等于现状），迁移时可将旧会话统一归到"全部知识库"分组。
- [ ] **落地 / 前端**：
  - 左侧会话列表改为**知识库 → 会话**两级分组（`app.js` 渲染 + `style.css` 分组样式）；
  - "新建会话"入口改为**先选知识库**（弹层列出该用户可访问库，含"全部知识库"选项）再进入对话；
  - 提问时若已有会话延续，不渲染知识库选择器，仅展示当前绑定库标签。
- [ ] **影响面**：`app/db.py`（schema + migration + CRUD）、`app/schemas.py`、`app/routers/chats.py`、前端 `app.js / index.html / style.css`、`DEVLOG.md`。**建议优先级：②**（功能增量，改动跨前后端）
- [ ] **工作量**：大　**风险**：中（涉及 schema 迁移与前端重组，需回归旧会话兼容）。
- [ ] **验证**：新建会话绑定库 A → 提问只检索库 A；续聊不带 dataset → 仍只用库 A；跨库 A/B 两会话上下文互不串扰；存量无绑定会话可正常续聊；`_migrate` 后旧库不报错。

### 7.10 建议实施顺序

1. `N3` 中文 token 估算 + `N6` 阈值默认同步（各约 10 分钟、零新增依赖、收益确定）——**先做**
2. `N1` BM25 索引缓存化（性能核心收益）
3. `N2` 读写锁分离（多用户并发，需回归）
4. `N5` 上下文+历史总预算联合校验
5. `N8` 会话按知识库分类管理（重点功能，跨前后端 + schema 迁移，建议排期开发）
6. `N4` 外部 Rerank 通道（功能增量，解决"参考不相关"）、`N7` 流式去重

### 7.11 状态跟踪

- [x] N1 　- [x] N2 　- [x] N3 　- [x] N4 　- [x] N5 　- [x] N6 　- [x] N7 　- [x] N8

> 全部 N1–N8 已于 09-05 完成并同步 DEVLOG（阶段 17）。完成项核对：
> - **性能**：N1 BM25 倒排索引+索引缓存；N2 检索读写锁分离；
> - **质量**：N3 中文 token 估算；N4 外部 OpenAI 兼容 Rerank 通道（含管理台通道切换/降级）；N5 上下文+历史总预算联合校验；
> - **一致性/可维护**：N6 阈值默认 0.5→0.3；N7 流式去重统一 async；
> - **体验**：N8 会话按知识库分类（schema 迁移 + 前后端分组 + 续聊沿用绑定库）。
> 新增回归测试：`test_rerank_external.py`（N4 4 用例）。静态资源版本由 `_static_version` 内容哈希自动推导（`index.html` 改裸引用）。

***

## 八、新一轮优化方案（09-15 · 借鉴 WeKnora 可复用体验能力）

> 背景：对照腾讯 [WeKnora](https://github.com/Tencent/WeKnora)（企业级 RAG / Agent / Wiki 知识框架，v0.8.0），沿用其 **推荐问题 + 答后追问、RAG 流水线分阶段进度、引用浮层/抽屉** 三类低成本可复用思路。
> WeKnora 大部分能力（Agent 沙箱 / Wiki 自治 / GraphRAG / 长期记忆 / 多数据源 / 多 IM）对应重平台定位，与 MagicerRAG「轻量私有化、少而精」不符，**刻意剔除**。
> 本组编号 `W1–W3`，均为**体验层增量、不改检索语义**，但偏演示观感与情感体验。

### 8.1 现状再盘点（本轮聚焦）

| 能力 | 现状 | 结论 |
| --- | --- | --- |
| 流式 Markdown 渲染 | ✅ 已实现（`static/` 前端 `marked + purify`，代码高亮 + XSS 防护） | 无增量 |
| 引用溯源 | ✅ 已实现（内联 `[n]` + 按文件聚合来源卡片 + P1-2 命中句高亮） | 缺抽屉化 → W3 |
| 推荐问题 / 答后追问 | ⚠️ 仅有**硬编码**三问（`app.js` `followUpsHtml()`），非动态；空会话无引导问题 | 动态化 → W1 |
| RAG 流程反馈 | ⚠️ `chats.py@ask` 检索**同步阻塞在 SSE 返回前**，SSE 仅 `retrieval→delta→done`，长库/大模型下观感像卡住 | 分阶段进度 → W2 |

### 8.2 W1 · 推荐问题 + 答后追问动态化（高价值 · 低风险）【体验】

- [ ] **现状**：
  - 「+ 新话题」空状态虽有知识库选择卡片，但**没有推荐问题**，冷启动引导弱；
  - 每条助手回答下方「你还可以问」是**硬编码**三问（`app.js` `followUpsHtml()`：帮我总结一下 / 该重点看什么 / 生成学习笔记），与本次回答和知识库内容**无关**、千篇一律。
- [ ] **目标**：让引导问题**基于知识库内容与本次回答动态生成**（推荐问题 × 答后追问），营造"懂你"的仪式感，提升演示与情感体验。
- [ ] **落地 / 后端**：
  - `app/service/rag_service.py` 新增 `recommend_questions(question, answer, hits)`：结合当前库标题、命中片段与回答，用 LLM 一次生成 3 个可点击追问（要求输出 JSON 数组，失败降级返回现有硬编码兜底）；复用既有 `_ahttp`；
  - `chats.py@ask` 的 `event_stream()` 在 `done` 前（生成完）追加 `_sse("suggest", {"questions": [...]})`；
  - `runtime_settings` 新增开关 `suggest_questions`（默认开），管理台「模型管理」可关；（可选）空会话引导用 `GET /api/kb/datasets/{id}/files` 首位文档做一次推荐问题生成。
- [ ] **落地 / 前端**：
  - `consumeSSE` 处理 `suggest` 事件，缓存到 `state.lastSuggest` 并替换 `.recs`；`followUpsHtml()` 改为渲染动态问题、无值时回退硬编码；
  - `emptyState()` 选中知识库后渲染推荐问题 chip，点击即发问。
- [ ] **影响面**：`rag_service.py`、`chats.py`（新增 `suggest` 事件）、`app.js`、`style.css`、`runtime_settings.py`。
- [ ] **优先级：①**（最符合"少而精"）　**工作量**：中　**风险**：低（LLM 生成失败回退硬编码兜底）。
- [ ] **验证**：提问后断言收到 `suggest` 事件且 `.recs` 渲染为动态文案；关开关回退旧三问；空状态可选库后出现推荐问题。

### 8.3 W2 · RAG 分阶段进度条（中价值 · 中改动）【体验 / 透明化】

- [ ] **现状**：`chats.py@ask` 先 `await asyncio.to_thread(rag_service.retrieve_hits)` **同步阻塞完成检索后**才 `return StreamingResponse`，用户等待阶段无任何反馈（此时 SSE 尚未开始）；SSE 仅 `retrieval → delta → done`，无法体现「检索 → 生成」过程。
- [ ] **目标**：把 RAG 流水线透明化——前端展示「检索中 → 生成中 → 完成」的阶段进度，参考 WeKnora 流水线进度。
- [ ] **落地 / 后端**：
  - 把检索拨进 `event_stream()` 内：先 `yield _sse("stage", {"stage":"retrieve"})`，再 `await asyncio.to_thread(retrieve_hits)`，随后 `yield _sse("stage", {"stage":"generate"})`；命中仍以 `retrieval` 事件下发（保留现有前端解析）；
  - 注意调整**消息落库时序**：用户消息与 `msg_id` 的创建移到流内对应阶段，保证事件顺序与 `done` 回传的 `message_id` 正确；导出 / 分享 / 历史渲染不受影响。
- [ ] **落地 / 前端**：
  - `consumeSSE` 处理 `stage` 事件并更新阶段指示条；`retrieval`/`done` 推进状态到「生成中 / 完成」；
  - `index.html` 聊天容器顶部预留 `<div class="pipeline-bar">`，`style.css` 新增阶段条样式（检索 ● ➜ 生成 ●，当前阶段高亮）。
- [ ] **影响面**：`chats.py`（SSE 事件时序与落库顺序重构）、`app.js`、`index.html`、`style.css`。
- [ ] **优先级：②**　**工作量**：中-大　**风险**：中（事件时序 / 落库顺序改动，需回归导出、分享、历史消息渲染）。
- [ ] **验证**：提问时断言按序收到 `stage(retrieve)→retrieval→stage(generate)→delta→done`；前端阶段条随事件推进；`done` 回传 `message_id` 正确、历史消息与分享页不受影响。

### 8.4 W3 · 引用抽屉 / 浮层（中价值 · 低-中风险）【体验 / 视觉】

- [ ] **现状**：来源卡片已内嵌消息下方**可展开**（`hitsHtml()` 按文件聚合，含命中片段与评分），内联 `[n]` 点击已做命中句高亮（P1-2）；但参考资料占消息正文区，长引用挤占回答区、视觉噪音重，来源类型也未标注。
- [ ] **目标**：把来源从"内嵌展开区"升级为**右侧抽屉 / 浮层**（点击「参考资料 N」或内联 `[n]` 弹出），还原对话区纯净；来源类型可标注（知识库文件）。
- [ ] **落地 / 前端（主体）**：
  - `index.html` 新增右侧抽屉容器 `<aside class="ref-drawer">`（或复用遮罩做浮层）；
  - `hitsHtml()` 改为仅渲染「参考资料 N」触发按钮 + 点击开抽屉；抽屉内平铺按文件聚合的来源卡，**保留 P1-2 命中句高亮**（`_bestSpan` / `mark.cite-hit`）；
  - 点击内联 `[n]`：抽屉未开则打开并定位到第 n 条命中，已开则仅定位 / 高亮。
- [ ] **落地 / 后端（可选）**：`retrieval` 事件的 `hits` 补 `source_type`（`knowledge`，预留 `web`），供抽屉内来源类型标签。
- [ ] **影响面**：`app.js`、`index.html`、`style.css`（前端为主）；`chats.py` 仅当要加 `source_type` 时微调。
- [ ] **优先级：②**　**工作量**：中　**风险**：低-中（纯展示层重构，不改检索语义；服务端渲染的分享页 / 导出不受影响）。
- [ ] **验证**：`node --check app.js`；浏览器端到端：回答后点「参考资料」开抽屉、点 `[n]` 定位到第 n 条并高亮；历史消息与分享页不受影响；深浅主题下抽屉样式正常。

### 8.5 建议实施顺序

1. `W1` 推荐问题 + 答后追问动态化（改动最小、最贴合"少而精 + 惊喜感"，LLM 失败有硬编码兜底）——**先做**
2. `W3` 引用抽屉 / 浮层（纯前端重构、演示观感质变，不触检索逻辑）
3. `W2` RAG 分阶段进度条（透明化效果好，但涉及 SSE 时序重构，建议排期由 `W1`/`W3` 验证稳妥后再做）

### 8.6 状态跟踪

- [x] W1　- [x] W2　- [x] W3　（2026-09-15 已实现并验证）

> 三项落地后：更新勾选状态，并在 `DEVLOG.md` 追加一条（含根因 / 实现 / 验证 / 版本号）。静态资源版本由 `_static_version` 内容哈希自动推导。

