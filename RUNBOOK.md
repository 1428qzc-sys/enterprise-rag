# Enterprise RAG 运行手册

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
| 检索无结果 | 文档未入库或向量库异常 | 查看文档状态、Qdrant collection、BM25 索引 |
| 跨租户数据异常 | 重要安全事件 | 立即下线服务，保留日志，核查 tenant_id 过滤与测试 |

## 发布检查清单

- [ ] `python -m pytest`
- [ ] `npm audit --audit-level=high`
- [ ] `npm run type-check`
- [ ] `npm run build`
- [ ] `docker compose --env-file .env.production up -d --build`
- [ ] 登录、建库、上传、检索、问答、跨租户越权测试
