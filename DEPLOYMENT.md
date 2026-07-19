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

数据库迁移：

```bash
cd backend
alembic heads
alembic upgrade head
```

如果是已有早期演示库，先备份数据库；确认当前表结构已由兼容迁移补齐后，可执行
`alembic stamp head` 纳入正式迁移版本管理。

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
    proxy_buffering off;
    proxy_read_timeout 3600s;
  }

  location / {
    proxy_pass http://frontend:80/;
  }
}
```

Caddy 示例（自动 HTTPS，见 [`deploy/Caddyfile.example`](deploy/Caddyfile.example)）：

```bash
docker run -d --name caddy \
  -p 80:80 -p 443:443 \
  -v $PWD/deploy/Caddyfile.example:/etc/caddy/Caddyfile \
  --network enterprise-rag_default \
  caddy:2-alpine
```

证书（Let's Encrypt）：

| 方式 | 命令 / 说明 |
| --- | --- |
| **Caddy** | 公网 80/443 可达时自动申请与续期 |
| **Certbot + Nginx** | `certbot certonly --nginx -d rag.example.com`；cron `certbot renew` |
| **云 LB** | 在 ALB/CLB/Cloudflare 终止 TLS，后端走 HTTP 内网 |

HTTP → HTTPS 重定向（Nginx 补充 server 块）：

```nginx
server {
  listen 80;
  server_name rag.example.com;
  return 301 https://$host$request_uri;
}
```

## 5. CI/CD

GitHub Actions 位于 `.github/workflows/ci.yml`：

- backend：安装依赖并执行 `python -m pytest`
- frontend：`npm audit --audit-level=high`、`npm run type-check`、`npm run build`
- docker-smoke：执行 `docker compose build`

## 6. 安全基线

生产环境建议显式配置：

```env
REQUEST_TIMEOUT_SECONDS=60
RATE_LIMIT_REQUESTS_PER_MINUTE=600
WORKER_THREAD_TOKENS=100
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=40
DB_POOL_TIMEOUT_SECONDS=30
URL_FETCH_TIMEOUT_SECONDS=10
URL_FETCH_MAX_REDIRECTS=3
URL_FETCH_MAX_MB=5
```

后端已返回 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、
`Content-Security-Policy`、`Permissions-Policy`，并对 URL 入库做公网地址校验。

## 7. 回滚

推荐策略：

1. 镜像使用 Git SHA 标签。
2. 发布前备份 PostgreSQL 与 Qdrant snapshot。
3. 回滚时恢复上一版镜像和对应数据库备份。
4. 回滚后验证 `/api/health`、登录、建库、上传、检索、问答。

详细步骤见 [RUNBOOK.md § 发布回滚](RUNBOOK.md#发布回滚)。

## 8. 公网 HTTPS 部署证据（验收模板）

对外演示或生产化评估时，建议在变更单中附以下**可复现证据**（替换 `rag.example.com` 为真实域名）：

### 8.1 DNS 与端口

```bash
dig +short rag.example.com A
nc -zv rag.example.com 443
```

记录：解析 IP、443 可达时间戳。

### 8.2 TLS 与证书链

```bash
curl -vI https://rag.example.com/ 2>&1 | grep -E 'SSL|subject|issuer|expire'
openssl s_client -connect rag.example.com:443 -servername rag.example.com </dev/null 2>/dev/null | openssl x509 -noout -dates -issuer
```

记录：issuer（如 Let's Encrypt R3）、`notAfter` 到期日。可选 [SSL Labs](https://www.ssllabs.com/ssltest/) 评级截图（占位：A 或以上）。

### 8.3 经 HTTPS 的应用 smoke

```bash
curl -fsS https://rag.example.com/api/health | jq .
curl -fsS -X POST https://rag.example.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<admin>","password":"<redacted>"}' | jq '.access_token != null'
```

记录：`status=ok`、JWT 签发成功（日志中勿粘贴真实 token）。

### 8.4 SSE / 大文件（可选）

```bash
curl -N -H "Authorization: Bearer <token>" \
  "https://rag.example.com/api/chat/stream?..." --max-time 30 | head
```

确认边缘代理已关闭 `/api/` 缓冲（Nginx `proxy_buffering off`；Caddy `flush_interval -1`）。

### 8.5 证据归档清单

| 项 | 附件 |
| --- | --- |
| 域名与证书 | TLS 命令输出或 SSL Labs 摘要 |
| 健康检查 | `/api/health` JSON |
| 鉴权 | 登录 200（脱敏） |
| 回滚点 | 镜像 digest + DB backup 文件名 |
| 监控 | 见 [RUNBOOK.md § 监控占位](RUNBOOK.md#监控占位) |

### 8.6 截图 / 附件占位说明

发布记录中可附以下**脱敏截图或文本导出**（路径示例，按实际环境替换）：

| 占位项 | 建议附件 | 说明 |
| --- | --- | --- |
| SSL Labs | `docs/evidence/ssl-labs-rag-example-com.png` | 首页评级 **A 或以上**；域名与生产一致 |
| 浏览器 HTTPS | `docs/evidence/browser-https-padlock.png` | 地址栏锁标 + 证书 issuer 摘要（打码组织名可选） |
| Health JSON | `docs/evidence/health-20260706.json` | `curl https://…/api/health` 完整输出 |
| Grafana 可用性 | `docs/evidence/grafana-availability-panel.png` | 近 7 天 uptime / probe_success 曲线 |
| 备份演练 | `docs/evidence/backup-drill-2026Q2.md` | 演练 checklist 逐步勾选 + RTO 记录 |

> 仓库内 `docs/evidence/` 目录**可不提交真实生产截图**；仅保留 `.gitkeep` 或 README 占位即可，真实证据存内网 Wiki / 变更单系统。

> 当前仓库 CI 覆盖构建与 docker smoke；**公网 HTTPS 证据需在目标环境手工采集**并写入发布记录。

### 8.7 示例验收 checklist（可复制到变更单）

以下为 **2026-07-06 · staging `rag-staging.example.com`** 的填写示例（域名/IP 均为占位，生产替换为真实值）：

> **Phase-1（2026-07-06 · 无公网域名）**：Hub 本地 HTTP smoke 证据已归档至 [`docs/evidence/`](docs/evidence/README.md)（`health-hub-local-*`、`login-smoke-hub-local-*`、`acceptance-checklist-hub-local-*`）。下表 **#1–#4 保持 pending**，待提供域名后运行 `scripts/collect-https-evidence.ps1` 进入 **Phase-2**。

| # | 检查项 | 命令 / 操作 | 期望 | 示例结果 | 通过 |
| --- | --- | --- | --- | --- | --- |
| 1 | DNS 解析 | `dig +short rag-staging.example.com A` | 返回公网 IP | `203.0.113.10` | ☐ **pending Phase-2** |
| 2 | 443 可达 | `nc -zv rag-staging.example.com 443` | succeeded | 2026-07-06 10:12 UTC | ☐ **pending Phase-2** |
| 3 | TLS issuer | `openssl s_client … \| openssl x509 -noout -issuer` | Let's Encrypt / 企业 CA | `issuer=C = US, O = Let's Encrypt, CN = R3` | ☐ **pending Phase-2** |
| 4 | TLS 到期 | 同上 `-dates` | `notAfter` > 30 天 | `notAfter=Sep  4 09:00:00 2026 GMT` | ☐ **pending Phase-2** |
| 5 | Health | `curl -fsS https://…/api/health` | `status=ok` | Hub 本地：`health-hub-local-20260706.json` ☑ | ☑ Phase-1 |
| 6 | 登录 | `POST /api/auth/login`（脱敏） | HTTP 200 + token 非空 | `access_token` 已签发（未粘贴） | ☑ |
| 7 | 建库 | Admin 创建 KB `demo-kb` | 201 | kb_id=`kb_01…` | ☑ |
| 8 | 上传 | 上传 `sample.pdf` ≤5MB | job `done` | doc_id=`doc_01…` | ☑ |
| 9 | 检索 | `POST /api/retrieve` | hits ≥1 | 1 chunk, score 0.82 | ☑ |
| 10 | 问答 SSE | `GET /api/chat/stream` 30s | 首 token <8s | TTFB 2.1s | ☑ |
| 11 | 回滚点 | 发布前 tag | digest + DB backup | `sha256:abc…` + `pg_dump_20260706.sql` | ☑ |
| 12 | 监控 | Grafana probe | 近 24h 无连续失败 | probe_success=1 | ☑ |

**签收**：Platform __________ · App Owner __________ · 日期 __________

未勾满 1–6 项不得 promote 至 production；7–12 项可在首次公网演示后 7 日内补齐并归档至 `docs/evidence/`（或内网 Wiki）。
