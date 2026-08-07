# Enterprise RAG 技术速览

适用版本：`1.0.0-rc.1`。本文不使用主观评分或宣传结论；细节与证据见 [架构说明](architecture.md)、[README](../README.md) 和 [验收索引](../DIMENSION-AUDIT.md)。

## 三个核心问题

### 为什么使用向量 + BM25 + RRF？

向量召回覆盖语义改写，BM25 覆盖术语、编号和专名；RRF 用名次融合，避免把两种不同量纲分数直接相加。可选 reranker 只对候选精排，执行状态和降级原因会进入 diagnostics。

### 如何保证引用不是模型编造的？

模型只看到编号上下文。服务端在保存前解析 `[n]`，检查编号范围并只保留实际引用来源；来源再绑定活动版本的数据库 Chunk、原文和页码。无可靠证据时不调用模型编造事实，而是返回“不知道”。

### PostgreSQL 与 Qdrant 无法同事务时如何一致？

新版本使用暂存 Chunk/向量，数据库激活失败会删除新向量并保留旧活动版本；激活后再清理旧向量。清理失败进入可重试对账状态。模型维度变更在新 collection 完整构建和计数核对后才切换。

## 可复现入口

```bash
docker compose up -d --build --wait
docker compose exec -T backend python scripts/release_smoke.py --base-url http://127.0.0.1:8000 --frontend-url http://frontend
```

固定评估、性能、恢复与浏览器证据均有独立脚本；默认 fake/echo 只证明工程闭环，不证明真实模型泛化。
