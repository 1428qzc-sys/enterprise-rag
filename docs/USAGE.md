# Enterprise RAG 使用指南

面向日常部署与问答操作的简明步骤。架构细节见 [architecture.md](./architecture.md)，完整说明见 [README.md](../README.md)。

## 1. 准备环境

- **Docker Desktop**（推荐），或 Python 3.11+ / Node.js 20.19+ 或 22.12+ 本地开发
- 至少一组模型密钥（OpenAI 兼容），或本地 **Ollama**（无需密钥）
- 复制环境变量模板：

```bash
cd enterprise-rag
cp .env.example .env
```

> 切勿提交含真实密钥的 `.env` 文件。

### 模型配置速查

| 场景 | 关键变量 |
| --- | --- |
| OpenAI（最省事） | `EMBEDDING_PROVIDER=openai` `LLM_PROVIDER=openai` + `*_API_KEY` |
| DeepSeek 做 LLM | `LLM_BASE_URL=https://api.deepseek.com/v1` `LLM_MODEL=deepseek-chat` |
| 全本地 Ollama | `EMBEDDING_PROVIDER=ollama` `LLM_PROVIDER=ollama` + `OLLAMA_BASE_URL` |

详见 [`.env.example`](../.env.example) 中方案 A / B / C 注释块。

## 2. 启动（Docker Compose 推荐）

```bash
docker compose up -d --build
```

首次构建需数分钟。启动后：

| 服务 | 地址 |
| --- | --- |
| 前端界面 | http://localhost:8080 |
| 后端 API 文档 | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/api/health |
| Qdrant 控制台 | http://localhost:6333/dashboard |

如果端口冲突，可在 `.env` 中覆盖：

```env
FRONTEND_HOST_PORT=18087
BACKEND_HOST_PORT=18086
QDRANT_HOST_PORT=16333
```

对应入口改为：

| 服务 | 地址 |
| --- | --- |
| 前端界面 | http://localhost:18087 |
| 后端 API 文档 | http://localhost:18086/docs |
| 健康检查 | http://localhost:18086/api/health |
| Qdrant 控制台 | http://localhost:16333/dashboard |

没有真实模型密钥时，可用离线 smoke 配置验证 Docker 与 RAG 链路：

```env
EMBEDDING_PROVIDER=fake
EMBEDDING_MODEL=fake
EMBEDDING_DIM=64
LLM_PROVIDER=echo
LLM_MODEL=echo
```

该模式不产生真实推理效果，但能跑通建库、上传、检索、SSE 问答与引用溯源。

查看日志：

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

停止服务：

```bash
docker compose down
```

## 3. 导入知识库

1. 浏览器打开 http://localhost:8080
2. 进入 **知识库管理** → **新建知识库**（填写名称与描述）
3. 进入 **文档管理** → **上传文件**，支持 PDF / Word / Excel / Markdown / TXT 等
4. 或粘贴 **网页 URL** 抓取入库
5. 等待文档状态变为 **已就绪**（后台自动解析 → 分块 → 向量化）

项目自带示例文档，可直接上传 `sample-docs/` 目录下的文件：

- `员工手册.md`
- `产品FAQ.md`
- `公司简介.txt`

## 4. 开始问答

1. 进入 **智能问答**，选择目标知识库
2. 输入自然语言问题，例如：
   - 「员工每年有多少天年假？」
   - 「差旅报销流程是什么？」
3. 回答以 **SSE 流式**输出，先展示检索来源，再逐 token 生成
4. 答案中的 `[1][2]` 编号对应下方引用卡片（文档名、页码、原文片段）
5. 支持多轮追问，系统自动注入对话记忆并改写指代问题

## 5. 常用 API（curl）

```bash
# 健康检查
curl http://localhost:8000/api/health

# 创建知识库
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Content-Type: application/json" \
  -d '{"name":"演示库","description":"测试"}'

# 上传文档（将 <KB_ID> 替换为实际 ID）
curl -X POST http://localhost:8000/api/knowledge-bases/<KB_ID>/documents/upload \
  -F "file=@sample-docs/公司简介.txt"

# 检索预览（不走 LLM，调试用）
curl -X POST http://localhost:8000/api/retrieve \
  -H "Content-Type: application/json" \
  -d '{"kb_id":"<KB_ID>","query":"年假有多少天"}'

# 非流式问答
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"kb_id":"<KB_ID>","question":"年假有多少天？","stream":false}'
```

## 6. 本地开发（零外部依赖）

无需 Docker 与 API Key，使用 SQLite + 内存向量库：

```bash
# 后端
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload

# 前端（新终端）
cd frontend
npm install
npm run dev    # http://localhost:5173 ，/api 代理到 :8000
```

## 7. 检索评估

```bash
# 先启动后端并在某知识库导入 sample-docs
python backend/scripts/evaluate.py \
  --api http://localhost:8000 \
  --kb-id <KB_ID> \
  --dataset backend/scripts/sample_eval.jsonl \
  --top-k 5
```

输出 Hit@k 与 MRR；加 `--ragas` 可评估答案质量（需 `requirements-optional.txt`）。

## 8. 运行测试

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

覆盖分块、RRF 融合、中文分词，以及建库 → 上传 → 入库 → 检索 → 问答端到端链路。

## 9. 常见问题

| 现象 | 处理 |
| --- | --- |
| 文档一直「处理中」 | `docker compose logs backend` 查看解析/嵌入错误；确认 Embedding API Key 有效 |
| 问答无引用或答非所问 | 确认文档状态为「已就绪」；检查是否选对了知识库 |
| 更换 Embedding 模型后检索异常 | 对文档执行「重嵌入」，或删除后重新上传 |
| Qdrant 连接失败 | `docker compose ps` 确认 qdrant 容器 running；检查 `QDRANT_URL` |
| 前端 502 / 无法访问 API | 确认 backend 健康检查通过；`docker compose restart backend` |
| Ollama 在 Docker 内连不上 | Windows/Mac 使用 `host.docker.internal:11434`；Linux 需额外 host 映射 |

## 10. 版本信息

当前版本见项目根目录 [`VERSION`](../VERSION)，变更记录见 [`CHANGELOG.md`](../CHANGELOG.md)。
