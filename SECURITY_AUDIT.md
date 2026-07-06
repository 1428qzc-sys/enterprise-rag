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

当前 `/documents/url` 允许抓取 http/https URL。生产前必须增加：

- 禁止内网 IP、localhost、metadata endpoint。
- DNS 解析后校验目标 IP。
- 限制重定向次数。
- 限制下载大小与超时。

## AI / RAG 风险

| 风险 | 处理 |
| --- | --- |
| Prompt Injection | 系统提示限制“只依据已知信息”，但仍需增加文档级不可信内容标记 |
| RAG 数据泄露 | 通过租户级 KB 校验降低跨租户泄露风险 |
| 引用伪造 | 前端展示结构化 sources，答案仍需人工判断 |
| 外部 URL 恶意内容 | 需结合 SSRF 与内容安全策略继续加强 |

## 剩余风险

- 尚未完成真实公网生产环境的 HTTPS、WAF、集中日志和告警验证。
- 尚未完成 100 并发压测。
- 尚未接入 Alembic 正式迁移。
- 尚未接入集中式审计日志。
