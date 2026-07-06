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
k6 run ^
  -e BASE_URL=http://127.0.0.1:8000 ^
  -e ADMIN_EMAIL=admin@example.com ^
  -e ADMIN_PASSWORD=ChangeMe123! ^
  performance/k6-smoke.js
```

## 当前状态

已完成：

- 本地后端测试覆盖 13 个用例。
- 前端生产构建通过。
- Docker Compose 可运行基础栈。
- 已提供 k6 smoke 脚本。

待完成：

- 在稳定 Docker / 云主机环境运行 100 并发压测。
- 输出真实 P95、P99、错误率、吞吐量。
- 根据压测结果调优连接池、索引、Qdrant 参数和 LLM 超时。

## 已检查的性能设计点

- 后端数据库引擎启用 `pool_pre_ping=True`。
- 文档解析与入库通过后台任务执行，不阻塞 HTTP 响应。
- RAG 检索同步阻塞部分通过 `run_in_threadpool` 放入线程池。
- 前端 Vite 生产构建体积约 154KB JS 入口，可用于公网演示。

## 后续优化清单

- PostgreSQL 增加复合索引：`tenant_id + kb_id`、`tenant_id + created_at`。
- Qdrant collection 按 KB 隔离，后续可按租户归档与冷热分层。
- 增加限流与请求超时。
- 增加 Redis 缓存热门 KB 元数据与会话列表。
- 增加 OpenTelemetry 指标。
