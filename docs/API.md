# Enterprise RAG API 指南

适用版本：`1.0.0-rc.1`。运行时 OpenAPI `<http://127.0.0.1:19021/docs>` 是字段级契约的权威来源；本文说明鉴权、资源边界、异步状态和 SSE 语义。

## 基础约定

- API 前缀：`/api`。
- 请求/响应：JSON；文件上传为 `multipart/form-data`；流式问答为 `text/event-stream`。
- 业务 API 使用 `Authorization: Bearer <JWT>`。
- 客户端可发送 `X-Request-ID`，服务端会在响应头回传；500/504 JSON 也包含 `request_id`。
- 其他租户对象与不存在对象统一返回 404；当前主体缺少权限返回 403。

常见错误：

| 状态 | 含义 |
| ---: | --- |
| 400 | 输入、文件结构、URL 或权限码无效 |
| 401 | token 缺失/失效，或登录凭据错误 |
| 403 | 用户/租户停用或权限不足 |
| 404 | 资源不存在或不属于当前租户 |
| 409 | 重复内容、任务状态冲突、最后管理员/系统角色保护 |
| 413 | 上传或 URL 响应超过限制 |
| 429 | 当前租户/IP 超过速率限制 |
| 503 | Redis fail-closed、依赖或补偿操作不可用 |
| 504 | 请求超过应用总超时 |

## 登录

```http
POST /api/auth/login
Content-Type: application/json

{
  "tenant_slug": "demo",
  "email": "admin@example.com",
  "password": "ChangeMe123!"
}
```

响应包含 `access_token`、用户、租户、角色和服务端计算出的权限。后续权限不会只相信 token，而会从数据库重新计算。

`tenant_slug` 对新客户端是必填语义。为兼容早期客户端，省略时仅在该邮箱全库唯一时登录；同邮箱存在于多个租户时返回 401，不会选择第一条记录。

```text
GET /api/auth/me
```

用于恢复前端会话并确认用户/租户仍启用。

## 资源 API

### 租户管理

前缀 `/api/admin`，需要 `admin:manage`：

| 方法与路径 | 行为 |
| --- | --- |
| `GET /permissions` | 权限字典 |
| `GET/POST /roles` | 角色列表/创建 |
| `PATCH/DELETE /roles/{role_id}` | 更新/删除非系统角色 |
| `GET/POST /users` | 用户列表/创建 |
| `PATCH /users/{user_id}` | 改显示名、密码、状态和角色 |
| `GET /audit-logs` | 按 action/outcome 分页查询当前租户审计 |

用户与角色引用必须属于当前租户。系统管理员角色、当前账号和最后一名有效管理员受保护。

### 知识库

前缀 `/api/knowledge-bases`：

| 方法与路径 | 行为 |
| --- | --- |
| `GET/POST /` | 列表/创建 |
| `GET/PATCH/DELETE /{kb_id}` | 详情/更新/删除 |
| `POST /{kb_id}/reindex` | 用新的 provider/model/dimension 构建 revision collection |
| `GET /{kb_id}/reindex-jobs` | 重建任务历史 |
| `POST /{kb_id}/reindex-jobs/{job_id}/cancel` | 请求取消 |
| `POST /{kb_id}/reindex-jobs/{job_id}/retry` | 重试失败/取消任务 |

重建成功前旧 collection 始终活动；切换后清理失败会保留可诊断任务状态。

### 文档、版本与入库任务

前缀 `/api/knowledge-bases/{kb_id}/documents`：

| 方法与路径 | 行为 |
| --- | --- |
| `POST /upload` | 新文档文件上传 |
| `POST /url` | 新文档网页抓取 |
| `GET /` | 当前知识库文档列表 |
| `GET/DELETE /{document_id}` | 详情/补偿式删除 |
| `POST /{document_id}/versions/upload` | 上传文件新版本 |
| `POST /{document_id}/versions/url` | 抓取网页新版本 |
| `GET /{document_id}/versions` | 版本历史，倒序 |
| `GET /{document_id}/jobs` | 入库/版本/对账任务历史 |
| `GET /{document_id}/chunks?version_id=...` | 读取指定版本原文 Chunk |
| `POST /{document_id}/jobs/{job_id}/cancel` | 请求取消 |
| `POST /{document_id}/jobs/{job_id}/retry` | 重试允许的任务 |
| `POST /{document_id}/reembed` | 用当前 KB 配置创建新版本并重嵌入 |
| `POST /{document_id}/reconcile` | 对账活动 Chunk 与向量 |

文档创建接口返回 201 只表示任务已接受。客户端应轮询详情或任务，直到：

```text
status=done
progress=100
consistency_status=consistent
```

`failed` 会带错误原因；`pending_cleanup` 表示新活动版本已可用但旧向量仍待清理。相同内容 SHA-256 不生成重复向量。

## 检索

```http
POST /api/retrieve
Authorization: Bearer ...
Content-Type: application/json

{
  "kb_id": "...",
  "query": "正式员工每年有多少天带薪年假？",
  "top_k": 5
}
```

结果来源包含：`chunk_id`、`document_id`、`document_name`、`chunk_index`、`page`、`content`、vector/BM25/RRF/rerank 分数和 `injection_risk`。

`diagnostics` 记录 vector、BM25、fusion、rerank 的状态、候选数与耗时，以及是否 degraded 和原因。结果数组是检索候选；生成问答还会应用独立的最低证据门槛。

## 问答与 SSE

```http
POST /api/chat
Authorization: Bearer ...
Content-Type: application/json

{
  "kb_id": "...",
  "question": "现在有多少天年假？",
  "conversation_id": null,
  "request_id": "client-generated-id",
  "top_k": 5,
  "stream": true
}
```

`request_id` 长度 8–64，用于同一用户回合幂等。断线重试必须复用同一 ID；更换 ID 会创建新回合。

SSE 事件顺序：

| 事件 | 内容 |
| --- | --- |
| `meta` | conversation/request ID、实际检索 query、diagnostics |
| `sources` | 初始结构化来源 |
| `token` | 草稿文本增量 `{"text":"..."}` |
| `replace` | 服务端引用校验后的最终 answer/sources；可能替换草稿 |
| `done` | message ID、最终 answer/sources、diagnostics |
| `error` | 可显示错误；该流没有成功完成 |

客户端不能把 `token` 草稿当作最终答案；只有 `replace/done` 经过引用范围校验。连接没有 `done` 即视为中断。

`stream=false` 返回同样经过校验的 `answer`、`sources` 和 diagnostics。无可靠证据时 answer 为“不知道”，sources 为空。

## 会话

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/knowledge-bases/{kb_id}/conversations` | 当前租户/知识库会话 |
| `GET /api/conversations/{conversation_id}/messages` | 消息与结构化来源 |
| `GET /api/conversations/{conversation_id}/export.md` | 经过测试的 Markdown 导出 |
| `DELETE /api/conversations/{conversation_id}` | 删除会话 |

v1 不提供 PDF 导出端点。

## 系统端点

| 路径 | 语义 |
| --- | --- |
| `/api/health` | 运行配置摘要和 Mock/model 标识，不探测依赖 |
| `/api/health/live` | 进程存活 |
| `/api/health/ready` | PostgreSQL、Qdrant/Memory、Redis、Embedding、LLM 实际就绪 |
| `/metrics` | Prometheus 指标；生产环境应限制访问 |
| `/docs`、`/openapi.json` | FastAPI OpenAPI |

## 安全注意

不要在日志、截图或问题报告中保存完整 token、密码、API Key、未脱敏文档或 Prompt。URL 入库拒绝私网/metadata 目标；其他租户对象应始终表现为 404。发现不同表现时按 [运行手册](../RUNBOOK.md) 的 P0 流程处理。
