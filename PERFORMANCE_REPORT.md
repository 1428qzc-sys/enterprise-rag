# Enterprise RAG 性能报告

报告日期：2026-07-20
版本：`1.0.0-rc.1`
状态：本地 Docker zero-key 发布候选基线通过

## 结论

固定夹具压测达到仓库发布预算：普通读 p95 `136.25ms`，混合检索 p95 `225.16ms`，本地写 p95 `106.37ms`，HTTP 错误率和业务错误均为 `0`。结果来自实际 PostgreSQL、Qdrant、Redis、BM25、RRF 和 lexical reranker；Embedding/LLM 使用明确标识的 deterministic fake/echo，不代表外部模型延迟或质量。

原始 k6 机器报告：`artifacts/k6-summary.json`。

## 固定环境与预算

- 主机：Windows 11 Pro 64-bit，版本 10.0.26200。
- Docker Desktop：Engine 29.6.1，Compose 5.3.0，16 vCPU，约 16.4GB 内存。
- k6：2.1.0。
- 后端：Python 3.11.15，单 Uvicorn 进程。
- 依赖：PostgreSQL 16、Qdrant 1.18.2、Redis 7.4.9。
- 配置：`.env.example`、fake embedding 256 维、echo LLM、lexical reranker、Redis fail-closed 限流。
- 固定文档：`backend/scripts/eval_fixture/hr_policy.md`；setup 真实建库、上传、等待一致并预建该 KB 的 BM25 缓存，teardown 删除夹具。
- 预算：普通读与混合检索 p95 `<300ms`；本地创建/删除写 p95 `<800ms`；HTTP 失败率 `0`；业务检查失败数 `0`。

外部 OpenAI/Ollama/CrossEncoder 的网络、排队、模型首 token 和生成时延不混入本报告。

## 优化前基线与根因

在同一 `.env.example` Docker 路径、空 PostgreSQL/Qdrant/Redis 卷下，首次混合检索 HTTP 耗时为 `6206.811ms`。紧随其后的主机端口连接出现 502，而容器零重启、后端和 Nginx 均没有收到该连接。

根因是 Jieba 词典在首个 BM25 请求中冷加载，约 5-6 秒的 CPU/磁盘开销发生在服务已被标记可用之后。修复为在应用 startup 中完成 Jieba 初始化，并在该步骤、数据库初始化和真实依赖检查完成后才允许 readiness 通过。

修复后空卷启动日志记录预热 `5149.286ms`；发布长链路中的首个 retrieve 为 `259.183ms`，同一夹具第二次 retrieve 为 `95.021ms`。冷启动成本没有被隐藏，而是移到了启动门槛。

## 固定 k6 场景

脚本：`performance/k6-smoke.js`。

- Read：3 个 VU，20 秒；每轮依次请求知识库列表、知识库详情、文档列表和混合检索，再等待 0.5 秒。
- Write：1 个 VU，15 秒；每轮创建唯一临时知识库并立即删除，再等待 0.5 秒。
- 检索断言不仅检查 200，还要求首条结果包含固定事实“18 天”。
- 全程使用默认每租户 600 请求/分钟限流，没有为压测提高配额。
- setup、teardown、读、检索和写使用独立 tag，避免把入库等待或清理时间混入目标分位数。

执行命令：

```powershell
docker run --rm `
  -e BASE_URL=http://host.docker.internal:19021 `
  -e TENANT_SLUG=demo `
  -e SUMMARY_PATH=/workspace/artifacts/k6-summary.json `
  -v D:\project-hub\enterprise-rag:/workspace `
  grafana/k6:2.1.0 run `
  /workspace/performance/k6-smoke.js
```

## 实测结果

| 指标 | 样本结果 | 门槛 | 结论 |
|---|---:|---:|---|
| 普通读 p95 | 136.25ms | <300ms | PASS |
| 混合检索 p95 | 225.16ms | <300ms | PASS |
| 本地写 p95 | 106.37ms | <800ms | PASS |
| HTTP 失败 | 0 / 345 | 0 | PASS |
| 业务错误 | 0 | 0 | PASS |
| 检查 | 416 / 416 | 100% | PASS |
| 完成迭代 | 96，0 interrupted | 无中断 | PASS |

分布明细：

| 类别 | avg | median | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|
| 普通读 | 73.90ms | 65.96ms | 117.65ms | 136.25ms | 152.01ms |
| 混合检索 | 138.17ms | 139.24ms | 209.98ms | 225.16ms | 248.64ms |
| 本地写 | 57.00ms | 48.93ms | 83.73ms | 106.37ms | 139.77ms |

总体 HTTP p95 为 `184.17ms`，其中包含 setup、teardown 与写请求，因此不用于“普通读 `<300ms`”判断。后续新增同步写逻辑时必须复跑本脚本。

## 可复现性与边界

- k6 阈值写在脚本中，任何一项超标会非零退出。
- 夹具由脚本创建并清理；本次 teardown 204，通过后没有残留性能知识库。
- 这是单机 Docker Desktop 的小规模发布门槛，不是生产容量或 100 VU 声明。
- 公网、TLS 终止、外部模型与跨主机数据库需在目标部署环境单独测量。
