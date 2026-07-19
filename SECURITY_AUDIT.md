# Enterprise RAG 安全审计记录

审计日期：2026-07-06

## 范围

- 后端 FastAPI API
- 前端 Vue 应用
- Docker Compose 部署
- 认证、多租户、上传、RAG 检索、网页 URL 抓取

## 已完成修复

| 风险 | 状态 | 证据 |
| --- | --- | --- |
| 业务 API 无鉴权 | 已修复 | 新增 `/api/auth/login`、Bearer token、业务接口权限依赖 |
| 知识库全局可见 | 已修复 | `KnowledgeBase.tenant_id`，列表/详情/更新/删除按租户过滤 |
| 文档全局可见 | 已修复 | `Document.tenant_id`，文档 API 先校验知识库租户 |
| 会话可跨 KB 复用 | 已修复 | `ensure_conversation()` 要求已有会话属于当前知识库 |
| 前端不带认证头 | 已修复 | Axios 拦截器和 SSE fetch 均发送 Bearer token |
| 缺少越权测试 | 已修复 | `test_cross_tenant_kb_is_not_visible` |
| SSRF / 外部 URL 抓取 | 已修复 | `security_network.py` 校验公网地址，限制重定向、大小与超时；`test_security_hardening.py` 覆盖 localhost/私网/metadata |
| 缺少安全响应头 | 已修复 | `security_middleware.py` 返回 `nosniff`、`DENY`、CSP、Permissions-Policy |
| 缺少基础限流和超时 | 已修复 | `InMemoryRateLimitMiddleware` 与 `REQUEST_TIMEOUT_SECONDS` |
| 缺少关键操作审计 | 已修复 | 新增 `audit_log` 表、`record_audit()` 与 `/api/admin/audit-logs` |
| 缺少正式迁移骨架 | 已修复 | 新增 Alembic 基线 `20260706_0001` |

## 依赖扫描

当前要求：

```bash
cd frontend
npm audit --audit-level=high
```

Python 依赖建议在生产流水线补充：

```bash
pip install pip-audit
pip-audit -r backend/requirements.txt
```

## Secret 扫描

建议命令：

```bash
git grep -n -I -E "sk-[A-Za-z0-9]|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH) PRIVATE KEY"
```

注意：`.env` 不应提交，`.env.example` 只允许占位值。

## 上传安全

已有限制：

- 扩展名白名单：由 `SUPPORTED_EXTS` 控制。
- 大小限制：`MAX_UPLOAD_MB`。

待增强：

- MIME 与文件头校验。
- 病毒扫描。
- 上传目录隔离为对象存储或非 Web 根目录。

## SSRF / 外部 URL 抓取

当前 `/documents/url` 已在创建文档前校验：

- 仅允许 `http://` / `https://`。
- 禁止 localhost、私网、链路本地、保留地址、IPv6 loopback 和常见 metadata endpoint。
- DNS 解析后校验所有解析出的 IP。
- 每次重定向后重新校验目标 URL。
- 限制 `URL_FETCH_MAX_REDIRECTS`、`URL_FETCH_MAX_MB` 和 `URL_FETCH_TIMEOUT_SECONDS`。

## AI / RAG 风险

| 风险 | 处理 |
| --- | --- |
| Prompt Injection | 系统提示限制“只依据已知信息”，但仍需增加文档级不可信内容标记 |
| RAG 数据泄露 | 通过租户级 KB 校验降低跨租户泄露风险 |
| 引用伪造 | 前端展示结构化 sources，答案仍需人工判断 |
| 外部 URL 恶意内容 | 需结合 SSRF 与内容安全策略继续加强 |

## 剩余风险

- 尚未完成真实公网生产环境的 HTTPS、WAF、集中日志和告警验证。
- 100 并发 k6 压测已在 Docker 本地环境完成（见 [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md)）；公网生产链路待复测。
- 当前审计日志已落库并可查，但尚未接入集中日志/SIEM。
- 当前限流为单进程内存限流，多副本生产部署应迁移到网关或 Redis 限流。
