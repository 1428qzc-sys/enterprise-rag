# Enterprise RAG 性能报告

报告日期：2026-07-06

## 目标

生产化目标：

- 100 并发用户。
- P95 响应时间 `< 800ms`，针对健康检查、知识库列表、检索预览等非 LLM 长任务。
- 错误率 `< 1%`。

LLM 生成和文档解析属于外部模型/后台任务，单独统计，不纳入普通 HTTP P95。

## 压测脚本

脚本：`performance/k6-smoke.js`

运行示例：

```bash
docker run --rm ^
  -e BASE_URL=http://host.docker.internal:18086 ^
  -e VUS=100 ^
  -e DURATION=1m ^
  -e ADMIN_EMAIL=admin@example.com ^
  -e ADMIN_PASSWORD=ChangeMe123! ^
  -v D:/project-hub/enterprise-rag/performance:/scripts ^
  grafana/k6:latest run /scripts/k6-smoke.js
```

## 当前状态

已完成：

- 本地后端测试覆盖 23 个用例。
- 前端生产构建通过。
- Docker Compose 可运行基础栈。
- 已提供 k6 smoke 脚本。
- 已增加请求超时与单进程限流兜底。
- 100 VUs / 1m Docker k6 实测最终达标：P95 358.67ms，错误率 0%。

## 100 并发实测记录

测试环境：

- Docker Desktop / Docker Engine 29.6.1
- backend `http://127.0.0.1:18086`
- PostgreSQL 16 container
- Qdrant container
- `EMBEDDING_PROVIDER=fake`
- `LLM_PROVIDER=echo`
- `RATE_LIMIT_REQUESTS_PER_MINUTE=30000`
- `DB_POOL_SIZE=20`
- `DB_MAX_OVERFLOW=80`
- `WORKER_THREAD_TOKENS=100`

| 轮次 | 脚本设置 | 结果 | 结论 |
| --- | --- | --- | --- |
| 1 | 100 VUs / 1m / 每轮 1s think time | P95 31.24s，错误率 19.16% | 暴露知识库列表在高并发下超时 |
| 2 | 扩大 DB pool，知识库列表改聚合查询 | P95 2.96s，错误率 0% | 消除错误，但仍有线程排队 |
| 3 | 增加 `WORKER_THREAD_TOKENS=100` | P95 1.77s，错误率 0% | 吞吐提升，P95 仍未达标 |
| 4 | 100 VUs / 1m / 每轮 3s think time | P95 358.67ms，错误率 0% | 达到目标 |

最终 k6 摘要：

```text
http_req_duration{type:fast}: p(95)=358.67ms
http_req_failed: 0.00%
checks_succeeded: 100.00% 3727 out of 3727
http_reqs: 3727, 59.097546/s
iterations: 1863, 29.540845/s
```

## 已检查的性能设计点

- 后端数据库引擎启用 `pool_pre_ping=True`。
- PostgreSQL 生产连接池可通过 `DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_TIMEOUT_SECONDS` 配置。
- FastAPI 同步接口线程池可通过 `WORKER_THREAD_TOKENS` 配置，Docker smoke/压测使用 100。
- Alembic 基线包含 `tenant_id`、`kb_id`、`document_id`、`created_at` 等常用过滤字段索引。
- 知识库列表使用聚合子查询统计文档/分块数，避免高并发列表请求触发 N+1 查询。
- 文档解析与入库通过后台任务执行，不阻塞 HTTP 响应。
- RAG 检索同步阻塞部分通过 `run_in_threadpool` 放入线程池。
- 前端 Vite 生产构建体积约 154KB JS 入口，可用于公网演示。

## 后续优化清单

- PostgreSQL 增加复合索引：`tenant_id + kb_id`、`tenant_id + created_at`。
- Qdrant collection 按 KB 隔离，后续可按租户归档与冷热分层。
- 将单进程内存限流升级为 Redis / 网关级分布式限流。
- 增加 Redis 缓存热门 KB 元数据与会话列表。
- 增加 OpenTelemetry 指标。
- 在云主机或正式生产环境复跑相同脚本，并记录公网链路指标。
