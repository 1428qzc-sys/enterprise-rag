# Enterprise RAG 部署指南

适用版本：`1.0.0-rc.1`。

仓库内的 Docker Compose 是可重复的单机试用和发布验收拓扑：Nginx/Vue、FastAPI、PostgreSQL 16、Qdrant 1.18.2、Redis 7.4.9。它会自动执行 Alembic，但不自动配置公网 TLS、WAF、集中日志、告警或长期备份。

## 拓扑与持久数据

```text
Browser -> frontend:19020 -> backend:8000
                              |-> PostgreSQL:5432  元数据、Chunk、会话、审计
                              |-> Qdrant:6333      活动版本向量
                              |-> Redis:6379       多副本共享限流计数
                              |-> /app/data        上传原文件
                              |-> Embedding / LLM provider
```

Compose 宿主机映射限定为：frontend `19020`、backend `19021`、Qdrant `19022`、PostgreSQL `19023`、Redis `19024`。恢复演练临时使用 `19025-19026`。

生产平台应把数据库、向量库和 Redis 放在受控网络中，避免直接暴露宿主机端口；前端或边缘代理是唯一公网入口。

## 本地零密钥部署

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

Windows 使用 `copy .env.example .env`。默认 `fake/echo` 会明确显示为 Mock，不需要 API Key。

验证：

```bash
curl -fsS http://127.0.0.1:19021/api/health/ready
docker compose exec -T backend alembic current
docker compose exec -T backend python scripts/release_smoke.py --base-url http://127.0.0.1:8000 --frontend-url http://frontend
```

## 生产配置门禁

以 `.env.example` 复制出未提交的环境文件，再替换所有演示值。生产模式会在进程启动前拒绝以下配置：

- 少于 32 字符或已知演示值的 `AUTH_SECRET_KEY`。
- 默认管理员密码、`CORS_ORIGINS=*` 或 `DATABASE_AUTO_CREATE=true`。
- SQLite、演示数据库凭据、memory 向量库。
- memory/fail-open 限流。
- `fake` Embedding、`echo` LLM。
- OpenAI-compatible provider 缺少对应 API Key。

生产最小配置结构：

```dotenv
ENVIRONMENT=production
AUTH_SECRET_KEY=<独立随机值，至少 32 字符>
BOOTSTRAP_ADMIN_EMAIL=<运维管理员邮箱>
BOOTSTRAP_ADMIN_PASSWORD=<独立强密码>
CORS_ORIGINS=https://rag.example.com

POSTGRES_USER=<独立用户>
POSTGRES_PASSWORD=<独立强密码>
POSTGRES_DB=enterprise_rag
DATABASE_AUTO_CREATE=false

VECTOR_BACKEND=qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=<如果 Qdrant 启用鉴权>

RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REDIS_FAILURE_MODE=fail_closed
REDIS_URL=redis://redis:6379/0

EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=<实际模型>
EMBEDDING_DIM=<实际向量维度>
EMBEDDING_BASE_URL=<实际 OpenAI-compatible endpoint>
EMBEDDING_API_KEY=<secret manager 注入>

LLM_PROVIDER=openai
LLM_MODEL=<实际模型>
LLM_BASE_URL=<实际 OpenAI-compatible endpoint>
LLM_API_KEY=<secret manager 注入>
```

不要把真实值写回 `.env.example` 或提交 `.env`。目标平台应通过 secret manager 注入密码和 Key。

## 迁移和启动顺序

backend 镜像入口为：

```text
alembic upgrade head && uvicorn app.main:app ...
```

启动顺序由健康依赖控制：PostgreSQL/Qdrant/Redis healthy → backend 自动迁移、BM25 预热和 readiness → frontend healthy。数据库缺少 Alembic 版本表时，Docker 模式不会用 `create_all` 静默造表。

发布前：

```bash
docker compose exec -T backend alembic heads
docker compose exec -T backend alembic current
docker compose exec -T backend alembic upgrade head --sql
```

先在恢复副本运行迁移与发布 smoke，再升级正式环境。迁移与回滚细节见 [RUNBOOK.md](RUNBOOK.md)。

## TLS 与边缘代理

应用本身只提供 HTTP。公网部署必须由受维护的负载均衡器、Ingress、Caddy 或 Nginx 终止 TLS，并满足：

- 仅转发预期域名和 HTTPS；HTTP 重定向到 HTTPS。
- `/api/chat` 禁用代理缓冲并允许长连接，保留 SSE `text/event-stream`。
- 上传大小不低于应用 `MAX_UPLOAD_MB`，同时在边缘设置相同或更严格上限。
- 传递 `X-Request-ID`，设置 `X-Forwarded-Proto`，不覆盖应用安全响应头。
- `/metrics`、PostgreSQL、Qdrant 和 Redis 不对公网开放。

TLS、DNS 与真实域名不在本地发布候选验收中伪造；部署方必须在目标环境实际检查证书链、SSE、上传和安全头。

## 模型与数据边界

- Embedding 模型、名称和维度必须匹配。改变任何一项后通过知识库重建生成新 revision collection，不得原地混写不同向量空间。
- `HEALTH_EXTERNAL_CHECKS=true` 时，OpenAI-compatible readiness 会访问 `/models`；不支持该端点的供应商需要关闭此探针并配置平台侧真实调用探针。
- 发送给外部模型的数据包含检索到的文档片段。部署前必须确认数据授权、地域、保留和供应商训练策略。
- 默认 lexical reranker 不访问外部网络；cross-encoder 需要额外依赖和模型缓存，必须单独验证启动时间与资源。

## 扩容边界

PostgreSQL、Qdrant、Redis 和上传文件必须由所有副本共享。Redis 限流支持多副本；JWT 无服务端 session。

当前入库/重建任务在应用进程内执行并在重启后恢复，但没有分布式队列租约。多 backend 副本部署前必须采用以下之一：

1. 只让一个 worker 执行入库任务，其他副本只服务读请求；或
2. 接入带租约和幂等键的外部任务队列。

未完成这一约束前，不应把单机 Compose 直接水平扩展为多个写 worker。

## 发布验证

CI 在全新 checkout 中执行：

1. 后端依赖一致性检查与 pytest。
2. 前端高危依赖审计、ESLint、Vitest、类型检查和生产构建。
3. 校验 Compose，构建并启动空卷五服务，等待 readiness。
4. 检查 Alembic current，运行与 README 相同的发布长链路。
5. 失败时输出服务日志，始终删除测试卷。

目标环境发布后仍需执行：

```text
/api/health/live
/api/health/ready
登录 -> 建库 -> 上传 -> 检索 -> SSE -> 引用定位 -> 版本更新 -> 删除
metrics 与结构化日志采集
备份恢复抽样
```

## 回滚

1. 停止写入并保留失败版本的 request ID、日志和镜像 digest。
2. 如果只涉及无迁移的应用代码，回到上一镜像并运行 readiness/发布 smoke。
3. 如果涉及数据模型、Embedding 空间或不可逆迁移，从同一恢复点恢复 PostgreSQL、Qdrant snapshots 和 uploads。
4. 恢复后核对活动版本 Chunk 数、向量数、原文件 SHA-256、检索事实和引用。

禁止只恢复 PostgreSQL 或只恢复 Qdrant；这会破坏数据/向量一致性。

## 外部上线条件

仓库内已验证的是本机 Docker Mock 发布链路。以下事项只能在实际部署环境验收：公网 HTTPS/WAF、托管组件 SLA、集中日志和告警、长期备份保留、真实模型效果与成本、组织数据合规和容量规划。
