# Enterprise RAG 生产部署指南

本文档用于把 Enterprise RAG 部署为可对外演示和生产化评估的服务。真实生产环境必须使用云主机、托管数据库、HTTPS 域名、集中日志、备份和监控。

## 1. 部署拓扑

推荐拓扑：

```text
Internet
  ↓ HTTPS
Nginx / Caddy / Cloud Load Balancer
  ↓
frontend nginx container
  ↓ /api
backend FastAPI container
  ↓
PostgreSQL + Qdrant + object/file storage
```

## 2. 生产环境变量

复制模板：

```bash
cp .env.example .env.production
```

必须覆盖：

```env
ENVIRONMENT=production
CORS_ORIGINS=https://rag.example.com
AUTH_SECRET_KEY=<long-random-secret>
BOOTSTRAP_ADMIN_EMAIL=<admin-email>
BOOTSTRAP_ADMIN_PASSWORD=<strong-one-time-password>
DATABASE_URL=postgresql+psycopg://...
VECTOR_BACKEND=qdrant
QDRANT_URL=https://...
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=<secret>
LLM_PROVIDER=openai
LLM_API_KEY=<secret>
```

不要提交 `.env.production`。

## 3. Docker Compose 部署

本地生产形态 smoke：

```bash
docker compose --env-file .env.production up -d --build
docker compose ps
curl -f http://127.0.0.1:${BACKEND_HOST_PORT:-8000}/api/health
```

## 4. HTTPS / 反向代理

Nginx 示例：

```nginx
server {
  listen 443 ssl http2;
  server_name rag.example.com;

  ssl_certificate /etc/letsencrypt/live/rag.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/rag.example.com/privkey.pem;

  client_max_body_size 50m;

  location /api/ {
    proxy_pass http://backend:8000/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location / {
    proxy_pass http://frontend:80/;
  }
}
```

## 5. CI/CD

GitHub Actions 位于 `.github/workflows/ci.yml`：

- backend：安装依赖并执行 `python -m pytest`
- frontend：`npm audit --audit-level=high`、`npm run type-check`、`npm run build`
- docker-smoke：执行 `docker compose build`

## 6. 回滚

推荐策略：

1. 镜像使用 Git SHA 标签。
2. 发布前备份 PostgreSQL 与 Qdrant snapshot。
3. 回滚时恢复上一版镜像和对应数据库备份。
4. 回滚后验证 `/api/health`、登录、建库、上传、检索、问答。
