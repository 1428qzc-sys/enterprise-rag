# Enterprise RAG - 中文部署指南

## 环境要求

- Docker Desktop（已配置国内镜像加速）
- Windows 10/11 或 macOS

## 快速启动

### 1. 配置 Docker 镜像加速

由于国内网络原因，需要配置 Docker 镜像加速器。在 Docker Desktop 的 Settings → Docker Engine 中添加：

```json
{
  "registry-mirrors": ["https://docker.m.daocloud.io"]
}
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

默认配置使用 Mock 模式（fake embedding + echo LLM），无需 API Key 即可体验完整流程。

### 3. 启动项目

```bash
docker-compose up -d
```

等待所有容器健康检查通过（约 1-2 分钟）。

### 4. 访问服务

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:19020 |
| 后端 API | http://localhost:19021 |
| Qdrant 向量库 | http://localhost:19022 |
| PostgreSQL | localhost:19023 |
| Redis | localhost:19024 |

## 架构说明

本项目包含 5 个 Docker 容器，通过 `docker-compose.yml` 编排：

- **postgres**：关系型数据库，存储租户、用户、知识库元数据、文档分块等
- **qdrant**：向量数据库，存储文档的向量表示，用于语义检索
- **redis**：缓存层，加速高频查询
- **backend**：FastAPI 后端，提供 REST API
- **frontend**：Vue3 前端，提供 Web 界面

容器启动顺序由 `depends_on` + `healthcheck` 控制：postgres/qdrant/redis 先启动并健康检查通过后，backend 才启动；backend 健康后 frontend 才启动。

## 核心功能

### 多租户隔离
每个租户（公司/组织）的数据完全隔离，通过 `tenant_id` 字段实现。不同租户的知识库、文档、聊天记录互不可见。

### RBAC 权限控制
支持细粒度权限管理：
- `admin:manage` - 管理员权限
- `chat:use` - 使用聊天功能
- `doc:read/write/delete` - 文档操作权限
- `kb:read/write/delete` - 知识库操作权限

### RAG 检索流程
文档上传 → 解析 → 分块（CHUNK_SIZE=800, CHUNK_OVERLAP=120）→ 向量化 → 存储到 Qdrant → 用户提问 → 向量检索 + BM25 检索 → RRF 融合排序（RRF_K=60）→ Rerank → LLM 生成回答

### 一致性对账
系统会自动校验 PostgreSQL 中的分块数量与 Qdrant 中的向量数量是否一致，确保数据完整性。

## 接入真实大模型

修改 `.env` 文件：

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxxxxxxx
```

## 常见问题

**Q: 容器启动失败？**
检查 Docker Desktop 是否运行，以及镜像加速是否配置正确。

**Q: 前端页面打不开？**
等待 backend 容器健康检查通过后再访问，backend 启动需要 30 秒左右。

**Q: 聊天没有回答？**
Mock 模式下 LLM 会原样返回你的问题。接入真实大模型后才能获得智能回答。
