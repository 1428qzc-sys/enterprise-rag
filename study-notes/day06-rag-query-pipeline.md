# Day 6 — RAG 查询全链路深度解析

> **日期**：2026-09-12  
> **核心文件**：`services/rag.py`, `services/retrieval.py`, `core/parsing.py`, `core/chunking.py`, `services/ingestion.py`  
> **关键词**：RAG Pipeline、Hybrid Retrieval、BM25、RRF Fusion、Bi-Encoder vs Cross-Encoder、Chunking Strategy

---

## 一、RAG 查询全链路（10 步调用链）

```
用户提问
  │
  ▼
① api/chat.py          ← HTTP 入口，接收 query
  │
  ▼
② services/rag.py       ← 编排层：协调检索→重排→生成
  │
  ▼
③ services/retrieval.py ← 混合检索：向量检索 + BM25
  │
  ├─▶ core/vector_store.py  ← Qdrant 向量相似度搜索
  │
  ├─▶ core/embeddings.py    ← Query → Embedding 向量
  │
  ▼
④ RRF (Reciprocal Rank Fusion) ← 融合两路结果
  │
  ▼
⑤ core/reranker.py      ← Cross-Encoder 精排
  │
  ▼
⑥ core/llm.py           ← 拼接 Prompt → LLM 生成
  │
  ▼
⑦ 返回答案（含引用来源）
```

### 面试话术

> "这个 RAG 系统的查询链路分为 10 步：用户提问后，API 层接收请求，RAG 服务编排整个流程。
> 首先做混合检索——同时走向量检索和 BM25 关键词检索，然后用 RRF 算法融合两路结果，
> 再用 Cross-Encoder 做精排，最后把 top-K 的上下文拼进 Prompt 交给 LLM 生成答案。
> 整个链路的核心设计思想是 **粗排保召回、精排保精度**。"

---

## 二、混合检索 & RRF 融合算法

### 2.1 为什么需要混合检索？

| 检索方式 | 优势 | 劣势 |
|----------|------|------|
| **向量检索（Dense）** | 语义理解强，能匹配同义词 | 对精确关键词/编号不敏感 |
| **BM25（Sparse）** | 精确匹配关键词，速度快 | 无语义理解，无法处理同义替换 |

**互补原理**：用户问 "GPT-4 的价格" 时，向量检索能找到 "大模型定价方案"（语义相似），BM25 能精确匹配 "GPT-4" 这个关键词。两路融合效果 > 任何单路。

### 2.2 RRF 算法实现

```python
# Reciprocal Rank Fusion 核心公式
def rrf_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """
    RRF 公式：score(d) = Σ 1/(k + rank_i(d))
    k=60 是经验常数，防止高排名项的分数差距过大
    """
    scores = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
```

**RRF 的优势**：
- 不需要对两路分数做归一化（向量余弦相似度 vs BM25 TF 分数，量纲不同）
- 只依赖排名，不依赖绝对分数，鲁棒性强
- k=60 是经验值，来自 TREC 实验

### 面试话术

> "混合检索用了 RRF 融合算法，核心公式是 score(d) = Σ 1/(k + rank_i(d))。
> 选 RRF 而不是加权求和的原因是：向量检索的余弦相似度和 BM25 的 TF 分数量纲不同，
> 直接加权需要归一化，而 RRF 只用排名不用绝对分数，天然跨量纲。
> k=60 是 TREC 论文里的经验值，防止 top-1 和 top-2 的分差过大。"

---

## 三、Bi-Encoder vs Cross-Encoder

### 3.1 架构对比

```
Bi-Encoder（用于向量检索）:
  Query → Encoder → [query_vec]  ─┐
                                   ├─▶ cosine_similarity → score
  Doc   → Encoder → [doc_vec]    ─┘
  
  ✅ 可预计算文档向量，检索速度快（毫秒级）
  ❌ Query 和 Doc 独立编码，交互不够深

Cross-Encoder（用于 Rerank）:
  [Query + Doc] → Encoder → score
  
  ✅ Query 和 Doc 联合编码，注意力机制充分交互，精度高
  ❌ 不能预计算，每对 (query, doc) 都要过一遍模型，慢
```

### 3.2 为什么分两阶段？

这是经典的 **漏斗架构**：

```
全量文档 (10万+)
    │ Bi-Encoder 粗排（毫秒级）
    ▼
Top-50 候选
    │ Cross-Encoder 精排（百毫秒级）
    ▼
Top-5 最终上下文
    │ LLM 生成
    ▼
最终答案
```

**工程权衡**：Bi-Encoder 快但精度有限，Cross-Encoder 精但慢。先用 Bi-Encoder 从 10 万文档筛到 50 个，再用 Cross-Encoder 从 50 个精排到 5 个。这样既保证速度又保证精度。

### 面试话术

> "检索用了经典的漏斗架构：Bi-Encoder 做粗排，因为它能预计算文档向量，检索是毫秒级的向量内积，
> 但精度有限因为 Query 和 Doc 独立编码；Cross-Encoder 做精排，Query 和 Doc 拼接后联合编码，
> 注意力机制能充分交互，精度高但不能预计算所以慢。
> 10 万文档 → Bi-Encoder 筛到 50 → Cross-Encoder 精排到 5 → 喂给 LLM。
> 这是一个工程上的经典权衡：用两阶段模型把 O(N) 的高精度计算降到 O(50)。"

---

## 四、文档摄入管线（Ingestion Pipeline）

### 4.1 三阶段流程

```
原始文档（PDF/Word/TXT）
    │
    ▼
① parsing.py — 文档解析
    │ 提取纯文本 + 元数据（标题、页码、作者）
    │
    ▼
② chunking.py — 文本切块
    │ 按语义边界切分，保持上下文完整性
    │ 策略：固定长度 + 重叠窗口（overlap）
    │
    ▼
③ ingestion.py — 向量化 & 入库
    │ 调用 Embedding 模型 → 生成向量
    │ 写入 Qdrant（向量）+ PostgreSQL（元数据）
    │
    ▼
完成 ✅
```

### 4.2 Chunking 策略详解

```python
# 核心参数
chunk_size = 512    # 每个块的目标 token 数
chunk_overlap = 64  # 相邻块的重叠 token 数

# 为什么需要 overlap？
# 假设 chunk_size=512, overlap=64:
# Chunk 1: [token_0   ... token_511]
# Chunk 2: [token_448 ... token_959]
# Chunk 3: [token_896 ... token_1407]
#
# 关键句子如果恰好在切分边界被截断，
# overlap 保证它至少在一个完整块中出现
```

**chunk_size 的工程权衡**：

| chunk_size | 优势 | 劣势 |
|-----------|------|------|
| 小（128-256） | 检索精度高，噪声少 | 上下文碎片化，丢失段落语义 |
| 中（512-1024） | 平衡精度和上下文 | — |
| 大（2048+） | 上下文完整 | 检索噪声大，LLM 上下文窗口浪费 |

本项目选 512 是行业经验值，适合大多数企业文档。

### 面试话术

> "文档摄入分三步：解析、切块、向量化入库。
> 切块用了固定长度 + 重叠窗口的策略，chunk_size=512, overlap=64。
> overlap 的目的是防止关键句子在切分边界被截断。
> chunk_size 选 512 是行业经验值——太小会丢失上下文，太大会引入检索噪声。
> 实际项目中可以根据文档类型调优，比如法律合同可以大一些，FAQ 可以小一些。"

---

## 五、BM25 索引缓存机制

```python
# 核心设计：BM25 索引缓存在 Redis 中
# 避免每次查询都重新构建 BM25 索引

# 缓存键格式：bm25_index:{knowledge_base_id}
# TTL：文档更新时主动失效

# 查询流程：
# 1. 先查 Redis 缓存 → 命中则直接用
# 2. 未命中 → 从 PostgreSQL 加载文档 → 构建 BM25 索引 → 写入 Redis
# 3. 文档更新/删除 → 主动清除缓存 → 下次查询重建
```

**为什么用 Redis 缓存？**

BM25 索引构建需要遍历知识库所有文档的切块，对于大知识库（几千个 chunk）需要秒级时间。
用 Redis 缓存后，只有首次查询或文档更新后的查询需要重建索引，后续查询直接命中缓存，降到毫秒级。

---

## 六、版本切换机制

系统支持多版本知识库切换，核心设计：

```python
# knowledge_base 表中有 version 字段
# 查询时通过 knowledge_base_id + version 定位文档集合
# 摄入新版本时不删除旧版本，而是标记为 archived
# 支持秒级回滚：只需切换 version 指针
```

**设计优势**：
- 零停机更新：新版本入库完成后切换指针
- 可回滚：旧版本保留，随时切回
- A/B 测试：可以同时维护多个版本

---

## 七、今日面试题

### Q1：请描述 RAG 系统的完整查询链路

**参考答案（30 秒版）**：
> 用户提问后，API 层接收请求，RAG 服务编排整个流程。
> 首先做混合检索——向量检索捕捉语义相似，BM25 捕捉关键词精确匹配，
> 两路结果用 RRF 融合，消除量纲差异。
> 然后 Cross-Encoder 精排 top-K，最后拼接上下文交给 LLM 生成答案。
> 核心设计是漏斗架构：粗排保召回，精排保精度。

### Q2：为什么用 RRF 而不是加权求和来融合检索结果？

**参考答案（30 秒版）**：
> 因为向量检索的余弦相似度和 BM25 的 TF 分数量纲不同，
> 加权求和需要先做归一化，而归一化方法的选择会影响最终排序。
> RRF 只用排名不用绝对分数，天然跨量纲，鲁棒性强。
> 而且 RRF 只有一个超参数 k，调优成本低。

### Q3：Bi-Encoder 和 Cross-Encoder 分别用在什么阶段？为什么？

**参考答案（30 秒版）**：
> Bi-Encoder 用在粗排阶段，因为 Query 和 Doc 可以独立编码，
> 文档向量可以预计算，检索时只做向量内积，毫秒级完成。
> Cross-Encoder 用在精排阶段，Query 和 Doc 拼接后联合编码，
> 注意力机制充分交互，精度更高但不能预计算。
> 10 万文档用 Bi-Encoder 筛到 50，再用 Cross-Encoder 精排到 5，
> 这是速度和精度的经典权衡。

---

## 八、知识图谱

```
                    ┌─────────────────────────────────────────┐
                    │           RAG 查询全链路                  │
                    └─────────────────┬───────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
     ┌────────▼────────┐    ┌────────▼────────┐    ┌────────▼────────┐
     │   文档摄入管线    │    │   混合检索引擎   │    │   生成 & 重排    │
     │                  │    │                  │    │                  │
     │ parsing.py       │    │ vector_store.py  │    │ reranker.py      │
     │ chunking.py      │    │ embeddings.py    │    │ (Cross-Encoder)  │
     │ ingestion.py     │    │ BM25 + RRF       │    │ llm.py           │
     │                  │    │ (Redis 缓存)     │    │ (Prompt 工程)    │
     └─────────────────┘    └─────────────────┘    └─────────────────┘
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      │
                           ┌──────────▼──────────┐
                           │  基础设施层           │
                           │  PostgreSQL + Qdrant │
                           │  + Redis             │
                           └─────────────────────┘
```
