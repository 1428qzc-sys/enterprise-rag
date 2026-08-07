# Enterprise RAG 验收证据索引

原 2026-07-06 “十维 8.9 分”自评已撤销：其中包含已过期测试数、旧端口、进程内限流和未完成项，不能作为 `1.0.0-rc.1` 的发布证据。

当前发布候选只接受可复现证据：

| 验收域 | 权威入口 |
| --- | --- |
| 功能与 Docker 长链路 | `backend/scripts/release_smoke.py` |
| 租户/RBAC/一致性/引用 | `backend/tests/` |
| 31 题固定质量评估 | `backend/scripts/fixed_eval.jsonl`、`backend/scripts/evaluate.py` |
| 性能 | `performance/k6-smoke.js`、`PERFORMANCE_REPORT.md` |
| PostgreSQL/Qdrant/uploads 恢复 | `backend/scripts/backup_restore_drill.py`、`RUNBOOK.md` |
| 前端质量 | `npm run lint`、`npm test`、`npm run type-check`、`npm run build` |
| 浏览器与截图 | 最终验收记录和 `docs/screenshots/` |
| 开箱启动 | `.env.example`、`docker-compose.yml`、CI release smoke |
| 安全 | `SECURITY.md`、`SECURITY_AUDIT.md`、依赖/secret scan 输出 |

不再使用主观 1–10 评分，也不以历史 CI run、占位 HTTPS、宣传截图或未运行的 checklist 证明当前版本完成。最终结论以本次完整测试、真实浏览器、无缓存 Docker、恢复演练、审计和 Git 状态为准。
