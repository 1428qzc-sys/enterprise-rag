# 发布证据说明

2026-07-06 的 Hub `18000/18001` health、占位 checklist 和自评材料已删除：它们不对应 `1.0.0-rc.1`，也不能证明公网 HTTPS、当前 Docker 链路或备份恢复。

当前验收证据由可执行工具生成：

- `backend/scripts/release_smoke.py` → Docker 核心长链路 JSON。
- `backend/scripts/evaluate.py` → 固定 31 题质量报告。
- `performance/k6-smoke.js` → k6 summary。
- `backend/scripts/backup_restore_drill.py` → 三类数据恢复报告。
- `frontend/e2e/enterprise-rag.e2e.ts` → Chromium 长链路、控制台/API 失败断言和三视口截图。
- `frontend/scripts/lighthouse.mjs` → Performance、Accessibility、Best Practices 固定阈值与 JSON 报告。
- `scripts/verify_clean_start.py` → 独立 Compose 项目、无缓存构建、空卷迁移、发布 smoke、固定评估、正式浏览器、三视口、Lighthouse 和资源清理的一次性门禁。

当前版本最终证据为 `artifacts/clean-start-report.json`（2026-08-07）：`status=passed`，无缓存构建 742.109 秒，空卷启动/迁移/发布 smoke 核心链路 57.766 秒，31 题八项指标均为 1.0000，Chromium E2E 与 Lighthouse 97/100/100 通过；验收项目的容器、卷、网络均无残留。固定评估明细、性能、备份恢复和 Lighthouse 原始机器报告分别位于 `artifacts/fixed-eval-report.json`、`artifacts/k6-summary.json`、`artifacts/backup-restore-report.json` 和 `artifacts/lighthouse.json`。

## 完成证据矩阵

| 验收域 | 实现与自动化证据 | 运行证据 |
| --- | --- | --- |
| 登录、租户、角色、权限与审计 | [租户/RBAC 回归](../../backend/tests/test_tenant_rbac.py)、[安全加固回归](../../backend/tests/test_security_hardening.py) | 最终 Chromium E2E 的登录、租户管理三视图与跨租户门禁 |
| 知识库、可靠入库、版本与数据/向量一致性 | [文档生命周期回归](../../backend/tests/test_document_lifecycle.py)、[重建回归](../../backend/tests/test_reindexing.py)、[Qdrant 契约回归](../../backend/tests/test_vector_store_contract.py) | [最终干净启动报告](../../artifacts/clean-start-report.json)中的 v1/v2、旧向量隔离、对账与删除链路 |
| 混合检索、重排、SSE、引用与安全回答 | [检索回归](../../backend/tests/test_retrieval.py)、[RAG 回归](../../backend/tests/test_rag.py)、[31 题评估回归](../../backend/tests/test_fixed_evaluation.py) | [固定评估明细](../../artifacts/fixed-eval-report.json)：Hit@5、MRR、引用、无答案、提示注入、租户隔离和多轮指标均为 1.0000 |
| 管理页面、状态、响应式与交互 | [聊天竞态组件回归](../../frontend/src/views/Chat.spec.ts)、[正式 Playwright 长链路](../../frontend/e2e/enterprise-rag.e2e.ts) | [桌面](../screenshots/chat-1440x900.png)、[平板](../screenshots/chat-768x1024.png)、[移动端](../screenshots/chat-375x812.png)截图及 [Lighthouse 报告](../../artifacts/lighthouse.json) |
| 限流、可观测性、性能与恢复 | [限流回归](../../backend/tests/test_rate_limit.py)、[可观测性回归](../../backend/tests/test_observability.py)、[恢复演练脚本](../../backend/scripts/backup_restore_drill.py) | [性能报告](../../PERFORMANCE_REPORT.md)、[k6 机器报告](../../artifacts/k6-summary.json)、[备份恢复报告](../../artifacts/backup-restore-report.json) |
| 迁移、部署、开箱启动与 CI | [迁移回归](../../backend/tests/test_migrations.py)、[一键验收脚本](../../scripts/verify_clean_start.py)、[CI](../../.github/workflows/ci.yml) | 无缓存构建 742.109 秒，核心链路 57.766 秒，迁移 `20260720_0003 (head)`，验收项目资源零残留 |

最终回归结果：后端 115 项通过、覆盖率 84.01%；前端 ESLint、3 文件/11 项 Vitest、类型检查和生产构建通过；npm 全依赖/生产依赖与 Python 顶层/完整锁审计均未发现已知漏洞。

最终缺陷门禁：零已知 P0/P1，核心流程零已知可复现 P2。历史发现的租户越权、引用伪造、Qdrant 首次建集合竞态和聊天历史覆盖竞态均有回归测试，并由当前依赖锁的空卷端到端报告复证。

可提交的最终摘要与精选截图位于本目录和 `docs/screenshots/`；原始临时 trace、token、数据库、缓存和未脱敏文档不得进入仓库。公网 TLS/WAF/真实 provider 只能在目标环境实采，仓库不保留占位“通过”文件。
