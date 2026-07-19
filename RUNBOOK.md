# Enterprise RAG 运行手册

运维入口与部署拓扑见 [DEPLOYMENT.md](DEPLOYMENT.md)；公网 HTTPS 验收证据模板见 [DEPLOYMENT.md §8](DEPLOYMENT.md#8-公网-https-部署证据验收模板)。

## 边缘代理（HTTPS）

| 组件 | 参考 |
| --- | --- |
| Nginx TLS 终止 | [DEPLOYMENT.md §4](DEPLOYMENT.md#4-https--反向代理) |
| Caddy 自动 HTTPS | [`deploy/Caddyfile.example`](deploy/Caddyfile.example) |
| 前端容器内反代 | [`frontend/nginx.conf`](frontend/nginx.conf)（仅 HTTP，生产 TLS 在边缘） |

SSE 流式问答要求边缘层关闭响应缓冲（见 Caddy `flush_interval` / Nginx `proxy_buffering off`）。

## 证书续期

- **Caddy**：默认自动续期；检查 `docker logs caddy` 无 ACME 错误。
- **Certbot + Nginx**：`certbot renew --dry-run` 通过后加入 cron；续期后 `nginx -s reload`。
- **云 LB 证书**：在控制台跟踪到期告警；轮换后复跑 [DEPLOYMENT §8.2–8.3](DEPLOYMENT.md#82-tls-与证书链) smoke。

## 日常检查

```bash
docker compose ps
curl -f http://127.0.0.1:${BACKEND_HOST_PORT:-8000}/api/health
docker compose logs --tail=200 backend
```

关键健康信号：

- backend 容器 `healthy`
- PostgreSQL 可连接
- Qdrant 可连接
- `/api/health` 返回 `status=ok`
- 登录 `/api/auth/login` 成功
- `/api/admin/audit-logs` 可查看登录、建库、上传、检索、问答和拒绝事件

## 备份

PostgreSQL：

```bash
docker compose exec postgres pg_dump -U rag enterprise_rag > backup.sql
```

Qdrant：

```bash
curl -X POST http://localhost:${QDRANT_HOST_PORT:-6333}/collections/<collection>/snapshots
```

上传文件：

```bash
tar -czf uploads.tar.gz backend/data/uploads
```

## 恢复

1. 停止写入流量。
2. 恢复 PostgreSQL dump。
3. 恢复 Qdrant snapshot。
4. 恢复上传目录。
5. 重启 backend。
6. 执行建库列表、文档列表、检索、问答 smoke。

## 常见故障

| 现象 | 判断 | 处理 |
| --- | --- | --- |
| 登录失败 | 管理员密码错误或账号停用 | 用数据库后台重置密码哈希，或重新设置引导账号后迁移 |
| 文档一直处理中 | 解析/Embedding 失败 | 查看 backend 日志与 Document.error |
| 问答超时 | LLM 网关慢或不可用 | 检查 LLM_BASE_URL、超时、供应商状态 |
| URL 入库被拒绝 | SSRF 防护命中 | 确认目标不是 localhost、内网、链路本地、metadata endpoint，且重定向后仍是公网 |
| 429 请求过频 | 应用限流命中 | 检查调用方重试策略，必要时提高 `RATE_LIMIT_REQUESTS_PER_MINUTE` 或迁移到网关/Redis 限流 |
| 检索无结果 | 文档未入库或向量库异常 | 查看文档状态、Qdrant collection、BM25 索引 |
| 跨租户数据异常 | 重要安全事件 | 立即下线服务，保留日志，核查 tenant_id 过滤与测试 |

## 发布回滚

### 发布前

1. 标记镜像：`backend:git-<sha>`、`frontend:git-<sha>`。
2. 备份 PostgreSQL、Qdrant snapshot、上传目录（见下文「备份」）。
3. `alembic upgrade head` 仅在 forward 迁移已 review 后执行。

### 回滚步骤

1. **切流量**：LB/Nginx 指向上一版 compose stack 或 K8s ReplicaSet revision。
2. **降镜像**：`docker compose pull` 上一 digest 或 `kubectl rollout undo deployment/backend`。
3. **数据库**：若本次发布含破坏性迁移，恢复发布前 `pg_dump`；否则仅回滚应用层。
4. **向量库**：必要时从 Qdrant snapshot 恢复对应 collection。
5. **验证**：按 [DEPLOYMENT §8.3](DEPLOYMENT.md#83-经-https-的应用-smoke) 跑 HTTPS smoke；检查审计日志无异常洪峰。

### 回滚后 30 分钟观察

- `/api/health` 连续 green
- 5xx 率（见「监控占位」）不升高
- 登录 / 检索 / 问答抽样通过

## 监控占位

生产环境接入集中监控时，建议至少覆盖：

| 信号 | 占位 / 建议 |
| --- | --- |
| **可用性** | Uptime 探测 `GET https://<domain>/api/health`（60s） |
| **延迟** | P95 `POST /api/chat` 与 SSE TTFB |
| **错误率** | FastAPI 5xx、Nginx/Caddy 502/504 |
| **资源** | backend CPU/内存、PostgreSQL 连接数、Qdrant 磁盘 |
| **安全** | 429 限流命中、审计日志 `denied` / SSRF 拒绝计数 |
| **证书** | TLS `notAfter` 距今天数 < 14 告警 |

## 监控占位

生产环境接入集中监控时，建议至少覆盖：

| 信号 | 占位 / 建议 |
| --- | --- |
| **可用性** | Uptime 探测 `GET https://<domain>/api/health`（60s） |
| **延迟** | P95 `POST /api/chat` 与 SSE TTFB |
| **错误率** | FastAPI 5xx、Nginx/Caddy 502/504 |
| **资源** | backend CPU/内存、PostgreSQL 连接数、Qdrant 磁盘 |
| **安全** | 429 限流命中、审计日志 `denied` / SSRF 拒绝计数 |
| **证书** | TLS `notAfter` 距今天数 < 14 告警 |

### Prometheus + Grafana（占位配置）

`prometheus.yml` 片段：

```yaml
scrape_configs:
  - job_name: enterprise-rag
    metrics_path: /metrics          # 若启用 Prometheus 导出
    static_configs:
      - targets: ['backend:8000']
  - job_name: enterprise-rag-blackbox
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets: ['https://rag.example.com/api/health']
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - target_label: __address__
        replacement: blackbox-exporter:9115
```

Grafana 面板占位（Dashboard 行）：

| Panel | 查询 / 说明 |
| --- | --- |
| Availability | `probe_success{job="enterprise-rag-blackbox"}` |
| API latency P95 | histogram from reverse proxy or APM |
| 5xx rate | nginx/caddy `status=~"5.."` |
| PG connections | `pg_stat_activity_count` 或云 RDS 指标 |
| Qdrant disk | 节点磁盘使用率 |
| Audit denied | 日志聚合 `action=denied` 计数 |

### 无 Prometheus 时的 healthcheck 轮询

```bash
# cron 每 5 分钟；失败时 webhook / 邮件（替换 URL 与告警脚本）
*/5 * * * * curl -fsS --max-time 10 https://rag.example.com/api/health >/dev/null \
  || echo "enterprise-rag health FAIL $(date -Iseconds)" | mail -s alert ops@example.com
```

Docker Compose 内置 healthcheck 仅覆盖容器内进程；**公网路径**仍需边缘探测（见上表）。

日志聚合占位：JSON stdout（`SPRING_PROFILES_ACTIVE=json` 等价 profile）→ Loki / ELK / CloudWatch。

## 备份恢复演练 checklist

建议**每季度**在 staging 执行一次完整演练，并在变更单归档证据（文件名 + 日期）：

| 步骤 | 操作 | 通过标准 |
| --- | --- | --- |
| 1 | `pg_dump` 全库 + Qdrant snapshot + `uploads.tar.gz` | 三类备份文件大小 > 0，带时间戳 |
| 2 | 新建空白 staging 栈（或停写隔离库） | compose / K8s 栈 healthy |
| 3 | 恢复 PostgreSQL dump | `alembic current` 与预期一致 |
| 4 | 恢复 Qdrant snapshot | collection 文档数与备份前一致 |
| 5 | 恢复 uploads 目录 | 随机抽样文件可下载 |
| 6 | 重启 backend，跑 smoke | 登录 → 建库列表 → 文档列表 → 检索 → 问答 |
| 7 | 记录 RTO/RPO | 自停写到 smoke 通过的耗时；数据丢失窗口说明 |

演练失败时：保留失败日志，**不要**在未复盘前覆盖生产备份。

## 发布检查清单

- [ ] `python -m pytest`
- [ ] `alembic heads` / `alembic upgrade head`
- [ ] `npm audit --audit-level=high`
- [ ] `npm run type-check`
- [ ] `npm run build`
- [ ] `docker compose --env-file .env.production up -d --build`
- [ ] 公网 HTTPS smoke（[DEPLOYMENT §8](DEPLOYMENT.md#8-公网-https-部署证据验收模板)）
- [ ] 登录、建库、上传、检索、问答、跨租户越权测试
