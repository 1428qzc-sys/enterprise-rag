# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- 多租户与权限基础：`Tenant` / `User` / `Role` / `Permission` / JWT Bearer 登录。
- 业务 API 后端权限校验与租户隔离：知识库、文档、检索、问答、会话接口默认需要登录。
- 租户内用户管理 API：`GET/POST /api/admin/users`。
- 前端登录页、租户/用户展示、退出入口、Axios 与 SSE Bearer token。
- GitHub Actions CI：后端测试、前端审计/类型检查/构建、Docker Compose build smoke。
- 生产化文档：`DEPLOYMENT.md`、`RUNBOOK.md`、`MULTI_TENANCY.md`、`SECURITY_AUDIT.md`、`PERFORMANCE_REPORT.md`。
- k6 压测脚本：`performance/k6-smoke.js`。

### Security

- 增加未登录访问拒绝测试与跨租户知识库/检索越权测试。
- 将 `.env.example` 中容易触发 secret 扫描误报的 `sk-*` 占位符替换为普通占位文本。

### Planned

- 文档版本管理与增量更新
- 导出对话为 Markdown / PDF

## [0.1.0] - 2026-07-04

### Added

- 企业知识库 RAG 问答系统 MVP：Python 3.11 + FastAPI + Vue 3
- 多格式文档接入：PDF / Word / Excel / Markdown / TXT / CSV / HTML / 网页 URL
- 混合检索：Qdrant 向量召回 + BM25（jieba）关键词召回 → RRF 融合 → 可选 BGE 交叉编码器重排
- SSE 流式 RAG 问答，带 `[1][2]` 引用溯源（文档名、页码、原文片段、相关度分数）
- 多知识库管理（创建 / 隔离 / 删除）与文档管理（列表 / 删除 / 重嵌入）
- 对话记忆与多轮问题 condense 改写
- 可插拔 Embedding / LLM 提供方（OpenAI 兼容 / DeepSeek / Ollama / 本地 BGE）
- 零依赖本地模式：SQLite + memory 向量库 + fake/echo 提供方，pytest 离线可跑
- `docker-compose.yml` 一键部署（PostgreSQL + Qdrant + backend + frontend）
- 检索评估脚本 `backend/scripts/evaluate.py`（Hit@k / MRR，可选 RAGAS）
- pytest 端到端测试（分块 / 检索 / API 全链路）
- 中文 README、架构文档、`sample-docs/` 示例文档
- `VERSION`、`CHANGELOG.md`、`docs/USAGE.md` 开源发布配套文档
