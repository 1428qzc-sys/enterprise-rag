# 学习日志

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
