# Day 7 — Core 层深度解析：向量化 / 向量库 / 重排 / LLM

> **日期**：2026-09-13
> **核心文件**：`core/embeddings.py`, `core/vector_store.py`, `core/reranker.py`, `core/llm.py`
> **关键词**：Strategy Pattern、Factory Pattern、HNSW、Cosine Similarity、Bi-Encoder vs Cross-Encoder、Streaming、Async、Graceful Degradation

---

## 一、Core 层定位

Core 层是后端三层架构（API → Services → Core）的最底层，职责单一：**封装外部 AI 服务与基础设施**，对上层暴露统一接口。

| 文件 | 封装对象 | 核心方法 |
|------|---------|---------|
| `embeddings.py` | Embedding 模型 | `embed_documents(texts)` |
| `vector_store.py` | 向量数据库（Qdrant / 内存） | `search(query_vector, top_k)` |
| `reranker.py` | RRF 融合 + 重排模型 | `rerank_with_scores(query, docs, top_n)` |
| `llm.py` | 大语言模型 | `astream(messages)` |

**统一设计范式**：四个文件全部采用 **策略模式（抽象基类/Protocol 定义接口）+ 工厂模式（按配置创建实例）**，实现"换模型只改配置不改代码"。

---

## 二、embeddings.py — 文本向量化

### 2.1 结构

```
BaseEmbeddings (ABC)              ← 统一接口
├── embed_documents(texts)        ← 抽象方法：文本列表 → 向量列表
└── embed_query(text)             ← 复用 embed_documents([text])[0]

三个实现：
├── OpenAIEmbeddings   ← OpenAI 兼容 /embeddings，分批（batch_size=32）
├── OllamaEmbeddings   ← 本地 Ollama /api/embed（httpx）
└── FakeEmbeddings     ← 词袋哈希，零外部依赖，测试用

make_embeddings(provider, model, dim)  ← 工厂函数
```

### 2.2 FakeEmbeddings 词袋哈希原理

不依赖任何模型：文本拆词（英文按单词、中文按二字组 + 三字组），每个词做 MD5 哈希映射到向量的某一维，加正负号后归一化。**相同的词永远映射到相同维度**，因此含相同词的文本余弦相似度天然偏高——虽非真语义理解，但足够跑通开发测试链路。

### 2.3 面试话术

> "Embedding 模块用策略模式封装，抽象基类定义 `embed_documents` 接口，下面有 OpenAI 兼容、Ollama 本地、Fake 三种实现。工厂函数按配置的 provider 创建实例，所以切换 Embedding 模型只改配置不改代码。另外每个知识库会快照自己的 provider/model/dim，不同知识库可以用不同模型，互不干扰。"

---

## 三、vector_store.py — 向量数据库封装（面试高频）

### 3.1 接口

```
BaseVectorStore
├── ensure_collection(name, dim)   ← 建集合（类比建表）
├── upsert(name, points)           ← 插入/更新向量
├── search(name, query_vector, top_k) ← 核心：返回最相似 top_k
├── delete_document/version/collection ← 三粒度删除
└── count / health

两个实现：MemoryVectorStore（numpy 暴力遍历） / QdrantVectorStore（生产）
工厂：get_vector_store() —— 单例
```

### 3.2 HNSW 索引：向量库为什么快

| 方式 | 100 万向量的计算量 | 延迟 |
|------|------------------|------|
| 暴力遍历（Memory 版） | 100 万次相似度计算 | 秒级，不可用 |
| HNSW（Qdrant） | 几十次 | 毫秒级 |

**HNSW（Hierarchical Navigable Small World）** 是多层跳表结构：顶层节点稀疏、跨度大，底层节点密集、精度高。查询从顶层入口开始，每层贪心地朝更接近查询向量的邻居跳，逐层下降缩小范围，最后底层精确定位。代价是"近似"——可能漏掉极个别最优点，但召回率通常 95%+，用一点点精度换巨大速度提升。

### 3.3 余弦相似度 vs 欧氏距离

余弦相似度 = 点积 ÷ 各自模长，衡量**方向夹角**，值域 [-1, 1]，越接近 1 越相似。欧氏距离衡量**直线距离**。文本 Embedding 关心"语义方向"而非"绝对长度"——一段文本和它的扩写版方向一致但模长不同，余弦判相似、欧氏判不相似。所以 Qdrant 建 collection 时 `distance=COSINE`。

代码印证（Memory 版）：`score = np.dot(q, v) / (qn * vn)`

### 3.4 关键分层认知（易错点）

**vector_store.py 只连 Qdrant，不连 PostgreSQL。** `search()` 返回 `ScoredPoint(id, score, payload)` 即结束，它不知道 PostgreSQL 的存在。"拿 chunk_id 回 PostgreSQL 取正文"是上层 `retrieval.py` 的职责。

> 类比：vector_store 是图书馆索引卡片柜（告诉你书在几排几架），retrieval.py 才是走到书架取书的图书管理员。Core 层原则：一个文件封装一个外部服务，职责不混。

### 3.5 单例模式

`get_vector_store()` 用全局变量 `_INSTANCE`，第一次调用创建并存起来，之后返回同一对象——整个应用只有一个向量库连接实例。类比公司共用一台打印机。好处：复用一条网络连接，避免重复建连。

---

## 四、reranker.py — RRF 融合 + 重排器

> ⚠️ 这个文件装了**两个独立的东西**，面试别搞混。

### 4.1 RRF 融合（看排名，不看词重叠）

```python
score[doc_id] += 1.0 / (k + rank + 1)   # k=60
```

融合向量检索与 BM25 两路结果。**只看排名（rank）不看原始分数**——因为两路分数量纲不同（向量是 0~1 余弦，BM25 无上限），只看排名才能统一。两路都排前面的文档分数叠加，相当于"双票加权"。

### 4.2 两种重排器

| 重排器 | 原理 | 特点 |
|--------|------|------|
| `LexicalReranker` | 算问题与文档的词覆盖率（`lexical_similarity`） | 零依赖、轻量，证据门控阈值 0.11 也来自这里 |
| `CrossEncoderReranker` | 把 (query, doc) 配对送进模型打分 | 真精排，精准但慢 |

### 4.3 Bi-Encoder vs Cross-Encoder（面试必考）

- **Bi-Encoder（向量检索）**：query 和 doc 各自独立编码成向量再算相似度，**无交互**，快，适合海量粗筛召回。类比"各自写自我介绍再对比"。
- **Cross-Encoder（Rerank）**：query 和 doc **拼成一对一起送进模型**做深度交互再输出相关性分数，精准但慢（每对都跑一次模型），只能对召回后的少量候选精排。类比"面对面聊完再打分"。

架构即"**先召回后精排**"：Bi-Encoder 快速捞候选 → Cross-Encoder 精细排序。

### 4.4 降级设计（考工程思维）

`CrossEncoderReranker` 两层 try/except 保护：
1. 模型加载失败 → `applied=False`, `degraded_reason="model_unavailable"`，退回原始顺序
2. 打分预测失败 → `applied=False`, `degraded_reason="prediction_failed"`，退回原始顺序

**Rerank 挂了系统不崩溃**，退回用 RRF 融合排序，并用 `applied=False` 明确告知上层"这次没真正重排"。整条检索链路每层都有降级：向量挂了只用 BM25，BM25 挂了只用向量，Rerank 挂了用融合结果。原则：宁可效果降级，不让服务不可用。

### 4.5 惰性加载

CrossEncoder 模型体积大，不在启动时加载，而是**第一次真正用到才加载并缓存**（`_ensure()`）。若某次查询没走到 rerank，就不白白加载大模型占内存。

---

## 五、llm.py — 大模型封装

### 5.1 结构

```
BaseLLM (ABC)
├── astream(messages)    ← 抽象方法：逐 token yield（流式）
└── acomplete(messages)  ← 收集 astream 所有 token 拼成整段（非流式）

三个实现：OpenAILLM / OllamaLLM / EchoLLM
工厂：make_llm(provider, model)
```

### 5.2 流式输出（Streaming）

`astream()` 用 `yield` **逐 token 产出**，不等整段生成完。这是 Day 4 体验的 SSE 流式响应（`token×N` 事件）的源头——大模型吐 token，`rag.py` 通过 SSE 推给前端，实现"打字机效果"。

**为什么流式**：完整答案生成要数秒，非流式用户盯着空白屏干等；流式让首字延迟极低，体验好。类比服务员做好一道菜端一道，不等一桌全做完。

### 5.3 异步（async/await）

方法皆 `async`，返回 `AsyncIterator`。等大模型返回 token 是 IO 密集空档，异步让程序此时**不阻塞**，可处理其他请求——一个进程扛高并发。类比服务员点完菜不站厨房门口等，去招呼别的桌。

### 5.4 EchoLLM 假模型的巧思

不是随便回显：从 Prompt 抠出【已知信息】段，检索到资料就返回 `根据检索到的资料：xxx [1]`（带引用标记），没检索到就返回 `不知道`。这样不调真模型也能验证整条链路（检索 → 门控 → 引用）。这就是项目默认 `LLM_PROVIDER=echo` 也能跑通完整流程的原因。

---

## 六、贯穿全层的设计模式（面试话术）

> "Core 层四个文件统一用**策略模式 + 工厂模式**。抽象基类定义接口，多个实现类各自封装不同 provider，工厂函数读配置的 provider 字段创建实例。所以切换大模型只改 `.env` 不改代码——比如接 DeepSeek，设 `LLM_PROVIDER=openai`（DeepSeek 兼容 OpenAI 接口）、`LLM_MODEL=deepseek-chat`、配上 base_url 和 api_key，重启即可。上层业务与具体模型解耦，加新模型只扩展工厂和实现类，符合开闭原则。"

---

## 七、今日面试题（Q25–Q30）

| 编号 | 题目 | 考点 |
|------|------|------|
| Q25 | 向量数据库为什么检索这么快？说说 HNSW | ANN 索引原理 |
| Q26 | 为什么用余弦相似度而不是欧氏距离？ | 相似度度量选型 |
| Q27 | 为什么需要单独的 Rerank 模型？ | Bi vs Cross Encoder |
| Q28 | Rerank 挂了系统会怎样？ | 降级思维 |
| Q29 | 怎么支持切换不同大模型？ | 策略 + 工厂模式 |
| Q30 | 什么是流式输出？异步解决什么？ | Streaming + Async |

（详细参考答案见桌面《enterprise-rag学习笔记.docx》面试题汇总）

---

## 八、动手实验（Phase 3）& 真实 Bug 排查

> 环境：Docker 5 容器全 healthy（端口 19020-19024）。后端源码在容器内，配置经 `.env` → `docker-compose.yml` environment 映射 → 容器环境变量 → pydantic settings 加载。

### 8.1 实验一：入库全链路验证

上传 `量子计算机.txt`（459 字）到新库，验证 Day 6 + 今日 core 层：

| 验证点 | 结果 | 对应代码 |
|--------|------|---------|
| chunk 生成 | 1 块（459<800） | `chunking.py` |
| is_active | True（已激活） | `ingestion.py` 版本切换 |
| injection_risk | False（过注入检测） | `ingestion.py` 正则检测 |
| DB chunk 数 = Qdrant 向量数 | 1 = 1 ✅ | `ingestion.py` 一致性对账 |
| Qdrant distance | **Cosine** | `vector_store.py` ensure_collection |
| Qdrant dim | 1536 | embeddings 配置 |

> 关键手段：后端**不打逐步入库日志**（只有 http_request 事件），验证入库要用 API —— `GET .../documents/{id}`（看 status/chunk_count/consistency_status）、`GET .../chunks`（看切块内容）、`curl localhost:19022/collections/{name}`（直接数 Qdrant 向量）。

### 8.2 实验二：问答全链路验证

提问"谷歌的悬铃木处理器实现了什么"，SSE `meta` 事件 diagnostics 实测：

```
total 50ms
├─ vector  36.4ms  candidate=1  min_score=0.05   ← Qdrant HNSW 检索
├─ bm25     8.1ms  candidate=1                    ← 内存索引，快
├─ fusion   3.4ms  method=rrf  rrf_k=60           ← RRF 融合，k=60 实锤
├─ rerank   0.36ms provider=lexical               ← 注意是 lexical 非 cross_encoder
└─ evidence_gate  min_score=0.11  accepted=1       ← 门控阈值 0.11 实锤
```

sources 事件分数拆解（**混合检索价值的活教材**）：
- `vector_score=0.093`（低！fake embedding 无真语义，向量检索几乎失效）
- `bm25_score=0.857`（高！"悬铃木/谷歌/处理器"关键词精确命中）
- → 向量这一路废了，BM25 把正确片段捞回来，印证"两路互补"

token 事件逐个吐字（"谷歌"→"的"→"悬"→"铃"…）= `llm.py` astream 流式输出。
最终答案：`谷歌的"悬铃木"处理器在2019年宣称实现量子霸权，用200秒…[1]` —— 忠实原文无幻觉，带 [1] 引用（引用校验通过）。

> 补充：`RERANK_PROVIDER` 默认 `lexical`（compose 里 `${RERANK_PROVIDER:-lexical}`），所以没走 CrossEncoder，rerank 才只花 0.36ms。要用真 Cross-Encoder 需配 `RERANK_PROVIDER=cross_encoder` 并装 sentence-transformers 模型。

### 8.3 实验三：改 chunk_size 看效果

| chunk_size | 同一篇 459 字文档切成 |
|-----------|---------------------|
| 800 | 1 块 |
| 200 | 3 块（144/173/138 字） |

overlap 本次未明显体现：RecursiveCharacterTextSplitter 先按 `\n\n` 段落切，各块都在句号自然边界断开、未触发"句中超长硬切"，所以看不到重叠字符。要观察 overlap 需把 chunk_size 调到比一句话还短（如 50）逼它硬切。

### 8.4 两个真实 Bug 排查（重点，面试可讲）

**Bug 1：入库卡 progress=45，报 `NotFoundError: 404`**
- 根因：`.env` 里 `EMBEDDING_PROVIDER=openai` + `EMBEDDING_BASE_URL=api.deepseek.com` + `EMBEDDING_MODEL=text-embedding-3-small`。**DeepSeek 不提供 Embedding 接口**（只有 chat），拿 OpenAI 的模型名去 DeepSeek 请求 `/embeddings` → 404，向量化步失败。
- 深层坑：**知识库创建时会把 embedding 配置（provider/model/dim）快照进 DB，优先于全局 .env**。所以改全局 .env 对**已存在的 KB 无效**，必须**新建 KB** 才会用新配置。这也是"换库不串味"设计——防止老库向量维度和新库对不上。
- 修复：`EMBEDDING_PROVIDER=fake`（本地词袋哈希，零外部依赖）+ 新建 KB。注意 LLM 仍可用 DeepSeek（chat 接口存在），只有 Embedding 不行。

**Bug 2：改 `.env` 的 CHUNK_SIZE 不生效**
- 根因：`CHUNK_SIZE`/`CHUNK_OVERLAP` **不在 docker-compose.yml backend 的 environment 映射列表里**，所以 .env 的值传不进容器，容器用代码默认值（config.py `chunk_size=800`）。
- 环境变量传递链：`.env` → compose `environment: ${VAR:-default}` → 容器 env → pydantic settings。中间断一环就失效。
- 修复：在 compose backend environment 补 `CHUNK_SIZE: ${CHUNK_SIZE:-800}` 和 `CHUNK_OVERLAP: ${CHUNK_OVERLAP:-120}`，重建容器后生效。

### 8.5 安全提醒

`.env` 里 DeepSeek API Key 明文存储且已在排查中暴露，实验后应在 DeepSeek 后台**重置密钥**。`.env` 已被 gitignore（不会进仓库），`.env.example` 模板默认 `fake` 是正确示范。
