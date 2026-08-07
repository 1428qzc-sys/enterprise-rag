# Enterprise RAG 使用与验证

适用版本：`1.0.0-rc.1`。推荐路径是根目录 Docker Compose；原生环境主要用于开发和测试。

## Docker 快速使用

```bat
copy .env.example .env
docker compose up -d --build --wait
```

浏览器打开 <http://127.0.0.1:19020>，使用：

```text
租户标识：demo
邮箱：admin@example.com
密码：ChangeMe123!
```

默认页面会显示 Mock 模式。fake Embedding 和 echo LLM 只提供确定性工程闭环。

## 核心操作

### 文档入库

1. 创建知识库。
2. 在“文档”页拖入或选择 PDF、DOCX、XLSX、MD、TXT、CSV、HTML；也可以提交公网 HTTP/HTTPS URL。
3. 观察任务阶段、进度和错误。`done + 100% + 数据一致` 后才能用于问答。
4. 失败任务先修复原因，再点击重试；处理中任务可取消。

同一内容的 SHA-256 重复上传不会生成重复活动向量。伪造扩展名、空文件、二进制文本、超限内容和无法提取正文的文件会返回明确错误。

### 版本和对账

在文档详情中上传新版本。旧版本会保留在历史中，但只有一个活动版本可检索。展开原文可查看 Chunk、版本和 PDF 页码。

“数据对账”会以 PostgreSQL 活动 Chunk 为准重建该文档向量并清理旧版本向量。模型或向量维度变化使用知识库重建，不要把普通文档重嵌入当作 collection 维度迁移。

### 问答与引用

问答页支持新会话、历史、停止生成、断线后一次重连、重试、Markdown 导出和会话删除。点击引用会打开对应文档原文并高亮实际 Chunk；PDF 来源同时显示页码。

检索不到可靠证据时，系统回答“不知道”且不提供来源。默认 echo 只根据给定上下文产生可预测文本，不是通用 AI。

### 租户管理

具有 `admin:manage` 的用户可进入“租户管理”：

- 创建、停用、改密和分配用户角色；
- 创建、编辑和删除自定义角色；
- 查看权限字典和租户审计记录。

系统管理员角色、当前账号和最后一名有效管理员不可被破坏。只读用户不会在页面看到写按钮，直接调用 API 也会返回 403。

## 自动发布 smoke

```bash
docker compose exec -T backend python scripts/release_smoke.py --base-url http://127.0.0.1:8000 --frontend-url http://frontend
```

检查范围：readiness、前端入口、request ID、登录、建库、v1 入库、lexical 重排、检索、SSE、引用与原文、Markdown 导出、v2 更新、版本历史、对账、旧 Chunk 不返回、删除与 metrics。脚本失败非零退出，默认清理夹具。

## 固定评估集

Docker 栈内运行可保证评估脚本与服务使用同一 PostgreSQL 配置：

```bash
docker compose exec -T backend python scripts/evaluate.py --api http://127.0.0.1:8000 --tenant-slug demo --bootstrap --provision-cross-tenant --report /app/data/fixed-eval-report.json
```

数据集 `backend/scripts/fixed_eval.jsonl` 有 31 个可审查用例，覆盖精确术语、语义、多轮、无答案、跨租户和提示注入。脚本真实调用 `/api/retrieve` 与 `/api/chat`，输出并门禁：Hit@k、MRR、引用正确率、答案术语、无答案、注入安全、租户隔离和多轮准确率。任何阈值失败都会非零退出，夹具默认清理。

自动化集成门禁：

```bat
cd backend
.venv\Scripts\python.exe -m pytest tests\test_fixed_evaluation.py
```

## 性能

Windows PowerShell：

```powershell
docker run --rm `
  -e BASE_URL=http://host.docker.internal:19021 `
  -e TENANT_SLUG=demo `
  -e SUMMARY_PATH=/workspace/artifacts/k6-summary.json `
  -v D:\project-hub\enterprise-rag:/workspace `
  grafana/k6:2.1.0 run `
  /workspace/performance/k6-smoke.js
```

macOS/Linux 将挂载路径替换为当前绝对路径，并根据 Docker 网络使用 `host.docker.internal` 或宿主机地址。脚本自己创建/入库/预热/清理固定夹具，阈值写在脚本中。真实数据和环境见 [PERFORMANCE_REPORT.md](../PERFORMANCE_REPORT.md)。

## 后端测试

Windows：

```bat
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m pytest
```

macOS/Linux：

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest
```

pytest 配置包含 `--cov-fail-under=75`。局部调试可以临时用 `-o addopts=`，但发布结论必须来自完整默认命令。

## 前端质量门

```bash
cd frontend
npm ci
npm audit --audit-level=high
npm run lint
npm test
npm run type-check
npm run build
```

Node.js 使用 22。不要设置 `NODE_TLS_REJECT_UNAUTHORIZED=0` 运行最终依赖审计。

## 迁移测试

Docker 启动会自动 upgrade。手工查看：

```bash
docker compose exec -T backend alembic current
docker compose exec -T backend alembic history
```

迁移往返与旧数据回填由：

```bat
cd backend
.venv\Scripts\python.exe -m pytest tests\test_migrations.py
```

在临时数据库执行。不要在包含正式数据的环境直接运行 `downgrade base`。

## 备份恢复演练

宿主机安装 backend 开发依赖后：

```bat
backend\.venv\Scripts\python.exe backend\scripts\backup_restore_drill.py
```

自定义 Compose 项目通过 `--compose-project` 指定。脚本使用 `19025-19026` 的隔离服务恢复 PostgreSQL、Qdrant snapshot 和 uploads，并验证恢复后的登录、原文、检索、问答和引用。详见 [RUNBOOK.md](../RUNBOOK.md)。

## 数据清理

保留持久数据：

```bash
docker compose down
```

永久删除当前 Compose 项目的 PostgreSQL、Qdrant、Redis 和上传卷：

```bash
docker compose down -v
```

第二条命令不可恢复，执行前先按运行手册完成备份。
