# Enterprise-RAG 源码学习笔记

> 本仓库为 [enterprise-rag](https://github.com/Hou-mingyuan/enterprise-rag) 的 fork 学习仓库，在 `study` 分支记录源码阅读过程。

## 学习目标

系统性掌握企业级 RAG 系统全链路实现，覆盖文档摄入、混合检索、重排序、LLM 生成四大核心模块，
能够独立设计并部署一套生产级 RAG 系统。

## 项目架构概览

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│   FastAPI    │────▶│  PostgreSQL  │
│   (Vue3)     │     │   Backend    │     │  (Metadata)  │
└─────────────┘     └──────┬───────┘     └──────────────┘
                           │
                    ┌──────┴───────┐
                    │              │
             ┌──────▼──────┐ ┌────▼─────┐
             │   Qdrant    │ │  Redis   │
             │ (向量数据库) │ │ (缓存层) │
             └─────────────┘ └──────────┘
```

## 后端三层架构

| 层级 | 文件 | 职责 |
|------|------|------|
| **API 层** | `api/auth.py`, `chat.py`, `documents.py`, `knowledge_bases.py`, `deps.py`, `main.py` | HTTP 路由、请求校验、依赖注入 |
| **Services 层** | `services/rag.py`, `retrieval.py`, `ingestion.py`, `knowledge_base.py`, `document.py` | 业务编排、流程协调 |
| **Core 层** | `core/embeddings.py`, `vector_store.py`, `reranker.py`, `llm.py`, `parsing.py`, `chunking.py` | 核心算法：向量化、检索、重排、LLM 调用 |

## 学习进度

| 阶段 | 内容 | 文件 | 状态 |
|------|------|------|------|
| Phase 1 | 项目架构 & 入口 | `main.py`, `deps.py` | ✅ 已完成 |
| Phase 2 | 文档摄入管线 | `parsing.py`, `chunking.py`, `ingestion.py` | ✅ 已完成 |
| Phase 3 | 检索 & RAG 生成 | `retrieval.py`, `rag.py`, `vector_store.py` | ✅ 已完成 |
| Phase 4 | 重排序 & LLM | `reranker.py`, `llm.py` | ✅ 已完成 |
| Phase 5 | 知识库 & 文档管理 | `knowledge_base.py`, `document.py` | ⬜ 待开始 |
| Phase 6 | API 路由 & 前端 | `chat.py`, `documents.py`, Vue3 前端 | ⬜ 待开始 |

## 笔记索引

- [Day 6 — RAG 查询全链路深度解析](day06-rag-query-pipeline.md)

## 学习方法

采用**三层颗粒度教学法**：概览（系统定位）→ 精读（函数级拆解）→ 串联（调用链还原），
每个知识点关联面试话术，确保能讲清设计决策和工程权衡。
