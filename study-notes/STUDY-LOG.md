# 学习日志

## Day 7 — 2026-09-13

### 今日学习内容

| 文件 | 核心知识点 | 掌握程度 |
|------|-----------|---------|
| `core/embeddings.py` | 文本向量化：策略模式 + 三 provider | ✅ 深度掌握 |
| `core/vector_store.py` | 向量库封装：HNSW、余弦相似度、单例 | ✅ 深度掌握 |
| `core/reranker.py` | RRF 融合 + Bi/Cross-Encoder 重排 | ✅ 深度掌握 |
| `core/llm.py` | 大模型封装：流式输出 + 异步 | ✅ 深度掌握 |

### 核心收获

1. **HNSW 索引**：多层跳表，从粗到细定位，百万向量毫秒级检索
2. **余弦相似度**：看方向不看长度，文本语义场景优于欧氏距离
3. **Bi-Encoder vs Cross-Encoder**：先召回（各自编码）后精排（配对编码）
4. **降级设计**：Rerank 挂了返回 applied=False 退回 RRF 排序，服务不崩
5. **策略 + 工厂模式**：core 层四文件统一范式，换模型只改 .env 不改代码
6. **流式输出 + 异步**：astream 逐 token 吐对接 SSE，async 扛高并发

### 易错点纠正

- vector_store.py **只连 Qdrant 不连 PostgreSQL**，取正文是 retrieval.py 的活
- RRF 融合看**排名**，不是看词重叠（词重叠是 lexical_similarity 另一条线）
- 用 DeepSeek 是改 **.env 配置**，不是改 llm.py 的 openai 函数代码

### 面试题完成

- Q25：向量库为什么快（HNSW）
- Q26：余弦相似度 vs 欧氏距离
- Q27：为什么需要单独 Rerank 模型
- Q28：Rerank 降级设计
- Q29：策略 + 工厂模式切换模型
- Q30：流式输出 + 异步

### 详细笔记

- [Day 7 — Core 层深度解析](day07-core-layer.md)

---

## Day 6 — 2026-09-12

### 今日学习内容

| 文件 | 核心知识点 | 掌握程度 |
|------|-----------|---------|
| `services/rag.py` | RAG 编排层：10 步查询链路 | ✅ 深度掌握 |
| `services/retrieval.py` | 混合检索：向量 + BM25 + RRF 融合 | ✅ 深度掌握 |
| `core/parsing.py` | 文档解析：多格式提取 + 元数据 | ✅ 掌握 |
| `core/chunking.py` | 文本切块：固定长度 + 重叠窗口 | ✅ 掌握 |
| `services/ingestion.py` | 摄入编排：解析→切块→向量化→入库 | ✅ 掌握 |

### 核心收获

1. **RRF 融合算法**：理解了为什么用排名而非绝对分数来融合异构检索结果
2. **Bi-Encoder vs Cross-Encoder**：掌握了漏斗架构的工程权衡
3. **Chunking 策略**：理解了 chunk_size 和 overlap 对检索质量的影响
4. **BM25 缓存**：Redis 缓存 BM25 索引，避免重复构建

### 面试题完成

- Q1：RAG 完整查询链路描述
- Q2：RRF vs 加权求和的选型理由
- Q3：Bi-Encoder vs Cross-Encoder 的阶段分工

---

## Day 5 — 2026-09-11

### 今日学习内容

| 文件 | 核心知识点 | 掌握程度 |
|------|-----------|---------|
| `api/main.py` | FastAPI 应用入口、路由注册、中间件 | ✅ 掌握 |
| `api/deps.py` | 依赖注入：数据库会话、当前用户 | ✅ 掌握 |

### 核心收获

1. **FastAPI 依赖注入**：理解了 `Depends()` 的工作机制
2. **应用生命周期**：startup/shutdown 事件管理数据库连接池

---

*更多学习记录将持续更新...*
