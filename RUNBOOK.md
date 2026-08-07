# Enterprise RAG 运行手册

适用版本：`1.0.0-rc.1`。本文以仓库根目录的 Docker Compose 单机拓扑为准；生产环境的 TLS、托管数据库、集中日志和告警由部署平台负责。

## 日常状态检查

```bash
docker compose ps
curl -fsS http://127.0.0.1:19021/api/health/live
curl -fsS http://127.0.0.1:19021/api/health/ready
```

- `/api/health/live` 只证明应用进程能响应。
- `/api/health/ready` 实际检查 PostgreSQL、向量库、Embedding、LLM；使用 Redis 限流时也检查 Redis。任一必需组件失败会返回 HTTP 503，并在 `failed_components` 中列出组件名。
- `/api/health` 返回版本、环境、provider 与 `mode=mock|model`，不访问外部依赖。
- Compose 的 frontend 只会在 backend readiness 成功后启动并变为 healthy。

检查当前数据库迁移：

```bash
docker compose exec -T backend alembic current
docker compose exec -T backend alembic heads
```

## 日志与请求关联

```bash
docker compose logs --tail=200 backend
docker compose logs --since=10m backend
```

后端输出单行 JSON，字段包括 `timestamp`、`level`、`event`、`request_id`、路由模板、状态码与耗时。日志不会记录请求体、完整 Prompt、文档正文、Bearer Token 或原始邮箱。

客户端可以发送最多 128 字符的 `X-Request-ID`；合法值会在响应头回传，否则服务端生成新 ID。排障时以该 ID 关联 HTTP、入库与检索日志。

Prometheus 指标：

```text
GET http://127.0.0.1:19021/metrics
```

关键指标：

- `enterprise_rag_http_requests_total`
- `enterprise_rag_http_request_duration_seconds`
- `enterprise_rag_http_requests_in_progress`
- `enterprise_rag_retrieval_requests_total`
- `enterprise_rag_retrieval_stage_duration_seconds{stage="vector|bm25|fusion|rerank"}`

建议告警起点：readiness 连续 3 次失败；5xx 比例连续 5 分钟高于 2%；检索 degraded 持续增加；读接口 p95 超过 300ms；写接口 p95 超过 800ms。目标环境应根据实际容量重新校准。

## 超时和故障策略

| 组件 | 默认控制 | 失败表现 | 处理原则 |
| --- | --- | --- | --- |
| HTTP 请求 | `REQUEST_TIMEOUT_SECONDS=60` | HTTP 504，带 request ID | 查同 ID 日志，判断入库、模型或存储阶段，不盲目重放删除请求 |
| PostgreSQL 连接池 | `DB_POOL_TIMEOUT_SECONDS=30` | readiness 503 或请求 5xx | 检查连接数、锁和磁盘；恢复前停止扩容写流量 |
| readiness 外部检查 | `READINESS_TIMEOUT_SECONDS=2` | 对应组件 `status=error` | 只缩短探针，不会改变业务调用超时 |
| Qdrant | 客户端健康和维度检查 | readiness 503；检索可降级到 BM25，入库会失败/重试 | 恢复 Qdrant 后执行文档对账，禁止直接手工改向量数量 |
| Redis | `fail_closed`（Compose 默认） | 限流依赖不可用时 HTTP 503 | 先恢复 Redis；只有明确接受失去分布式限流时才临时改 `fail_open` |
| Embedding | provider 自身超时 | 新版本失败，旧活动版本保留 | 修复 provider 后从文档任务重试；不要删除旧版本 |
| LLM | provider 自身超时 + HTTP 总超时 | SSE `error` 或请求失败 | 同一 `request_id` 可重试；前端只自动重连一次 |
| URL 抓取 | `10s`、最多 3 次重定向、5 MiB | 入库失败并记录原因 | 核对公网 DNS、响应大小和 SSRF 拒绝日志 |

`HEALTH_EXTERNAL_CHECKS=false` 只适合外部 provider 不允许探测 `/models` 的受控环境；关闭后 readiness 不能证明真实模型可调用，必须另设供应商探针。

## 文档与向量一致性

每个文档版本有独立的 `DocumentVersion`、`IngestionJob` 和 Chunk。正常切换顺序：

1. 解析并生成暂存 Chunk。
2. 向当前 revision collection 写入带 `tenant_id/document_id/version_id` 的向量。
3. 在数据库事务中激活新版本和 Chunk。
4. 清理旧版本向量；清理失败则进入 `pending_cleanup`，活动版本仍可用。

常见状态：

- `consistent`：活动数据库 Chunk 数与活动版本向量数一致，旧版本向量已清理。
- `pending_cleanup`：新版本已生效，但旧向量清理待重试。
- `inconsistent` / failed：对账或入库失败，查看任务错误再处理。

优先在文档详情页点击“数据对账”。API 等价操作：

```text
POST /api/knowledge-bases/{kb_id}/documents/{document_id}/reconcile
GET  /api/knowledge-bases/{kb_id}/documents/{document_id}/jobs
```

模型或维度变化必须使用知识库重建 API/UI。系统会在新 revision collection 构建完整后原子切换；切换前失败时旧 collection 保持活动。不要直接修改数据库中的 `embedding_dim` 或 collection 名称。

## 迁移

容器启动命令会在启动 Uvicorn 前执行：

```bash
alembic upgrade head
```

发布前必须先备份三类持久数据，并在隔离副本演练迁移。查看 SQL：

```bash
docker compose exec -T backend alembic history
docker compose exec -T backend alembic upgrade head --sql
```

`alembic downgrade` 仅用于隔离恢复副本或已确认可逆的变更。生产回滚优先恢复发布前一致时间点的 PostgreSQL、Qdrant 和上传文件，不允许只回滚数据库而保留新向量。

当前迁移往返自动测试会执行 `upgrade head → downgrade base → upgrade head`，并验证旧文档回填为 v1 与租户内邮箱唯一约束。

## 备份与恢复

必须把以下三类数据作为同一恢复点保存：

1. PostgreSQL custom-format dump。
2. 每个活动 Qdrant collection 的 snapshot。
3. backend `ragdata` 卷中的 `/app/data/uploads`。

恢复顺序：停止写入 → 恢复 PostgreSQL → 恢复相同小版本 Qdrant snapshot → 恢复 uploads → 启动 backend → readiness → 登录/原文/检索/引用验收。

Qdrant collection snapshot 需要用与源端相同的 minor 版本恢复；脚本会从源 Compose 容器读取精确镜像，并以 `priority=snapshot` 上传到新的空 Qdrant。不要把不同时间点的数据库、snapshot 和 uploads 混用。

### 自动恢复演练

先在宿主机准备 Python 3.11 开发环境：

```bat
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
cd ..
```

标准项目名启动时：

```bat
backend\.venv\Scripts\python.exe backend\scripts\backup_restore_drill.py
```

自定义 Compose 项目示例：

```bat
backend\.venv\Scripts\python.exe backend\scripts\backup_restore_drill.py --compose-project enterprise-rag-rc --compose-env-file .env.example
```

脚本只使用 `19025` 和 `19026` 启动隔离恢复服务，真实完成以下检查：

- PostgreSQL dump catalog 可读，恢复后的知识库、文档、活动 Chunk 数一致。
- Qdrant snapshot SHA-256 可记录，恢复后的 point 数和 `document_id` payload 一致。
- uploads 归档解压路径安全，原文件 SHA-256 与数据库 `content_hash` 一致。
- 恢复后端 readiness、登录、知识库、文档、原文 Chunk、混合检索、问答和引用全部通过。
- 无论成功或失败，都删除临时容器、隔离数据库、源夹具和源 snapshot；原始备份只存在系统临时目录。

报告默认写入 `artifacts/backup-restore-report.json`，不包含密码或 Token。该脚本是恢复能力演练，不替代长期备份保留策略。

## 常见故障

### 栈长期不 healthy

```bash
docker compose ps
docker compose logs --tail=300 postgres qdrant redis backend frontend
```

先看第一个失败的依赖。backend 会等待三项基础服务健康，并在数据库迁移成功后才启动。首次启动还会预热中文 BM25 词典，完成前 readiness 不会成功。

### HTTP 429 或 503

- 429：租户配额已用完；匿名请求按 IP 计数。检查 `RATE_LIMIT_REQUESTS_PER_MINUTE` 与 Redis key，而不是绕过鉴权。
- 503 且正文提示限流依赖：默认 `fail_closed` 下 Redis 不可用，恢复 Redis。
- 503 readiness：查看 `failed_components`，按组件排障。

### 新版本失败

旧活动版本设计上仍然可检索。查看文档的任务阶段、尝试次数和错误；修复依赖后使用“重试”，不要重复上传相同文件制造额外任务。相同 SHA-256 内容会被判为重复而不生成向量。

### 引用跳转不到原文

记录 chat 响应的 request ID、`source.chunk_id`、`document_id`、`version_id` 和页码。确认该 Chunk 属于当前租户、当前知识库和活动版本。若数据库 Chunk 存在但向量计数不一致，执行文档对账；禁止手工改引用编号。

### 跨租户数据疑似可见

按 P0 安全事件处理：停止外部流量，保留结构化日志和数据库快照，记录主体 tenant claim、路由和对象 ID；不要删除证据。复现前不要改变租户数据。当前 API 对其他租户对象统一返回 404，权限不足返回 403。

## 发布前最小门禁

```text
backend pytest（覆盖率门槛 75%）
frontend npm audit / lint / test / type-check / build
docker compose config
release_smoke.py
固定 31 题 evaluate.py
k6 固定 fixture
迁移往返测试
backup_restore_drill.py
secret scan
git diff --check
```

生产发布还必须在目标环境验证 TLS、集中日志、告警、实际 provider、容量和恢复点保留；本仓库不会把本地 Mock 结果描述为这些外部项已通过。
