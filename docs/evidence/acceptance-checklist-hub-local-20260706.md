# Enterprise RAG · Hub 本地 smoke 验收（2026-07-06）

> **范围**：`ai-portfolio` Hub Profile · **HTTP** `localhost:18000/18001`（非公网 HTTPS）。  
> 公网 TLS 项（#1–#4）需在有域名环境执行 [`scripts/collect-https-evidence.ps1`](../../scripts/collect-https-evidence.ps1) 后另归档。

| # | 检查项 | 命令 / 操作 | 期望 | 结果 | 通过 |
| --- | --- | --- | --- | --- | --- |
| 1 | DNS 解析 | `dig +short <domain> A` | 公网 A 记录 | **N/A — 待公网域名** | ☐ |
| 2 | 443 可达 | `nc -zv <domain> 443` | succeeded | **N/A — 待公网域名** | ☐ |
| 3 | TLS issuer | `openssl s_client …` | 有效 CA | **N/A — 待公网域名** | ☐ |
| 4 | TLS 到期 | 同上 `-dates` | >30 天 | **N/A — 待公网域名** | ☐ |
| 5 | Health | `curl http://localhost:18000/api/health` | `status=ok` | 见 `health-hub-local-20260706.json` | ☑ |
| 6 | 登录 | `smoke_auth_flow.py` login | HTTP 200 + token | `login: 200`（见 login-smoke 文件） | ☑ |
| 7 | 建库 | smoke 建 KB | 201 | `kb: 201` | ☑ |
| 8 | 上传 | sample 员工手册.md | job `done` | `doc_status: done`, `chunk_count: 1` | ☑ |
| 9 | 检索 | smoke retrieve | hits ≥1 | `retrieve_count: 1` | ☑ |
| 10 | 问答 | smoke chat | sources ≥1 | `chat_sources: 1` | ☑ |
| 11 | 回滚点 | 发布前 tag | digest + backup | **待生产发布** | ☐ |
| 12 | 监控 | Grafana probe | probe 正常 | **待公网部署** | ☐ |

**环境**：Hub `enterprise-rag` · 后端 **18000** · 前端 **18001** · fake/echo 提供方  
**采集时间**：2026-07-06 · **采集人**：project-hub-1 automated smoke

**附件**：

- [`health-hub-local-20260706.json`](./health-hub-local-20260706.json)
- [`login-smoke-hub-local-20260706.txt`](./login-smoke-hub-local-20260706.txt)
