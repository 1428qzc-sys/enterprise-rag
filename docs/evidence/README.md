# 公网 HTTPS / 监控 / 备份演练证据占位

本目录用于归档 **DEPLOYMENT.md §8.5–§8.7** 与 **RUNBOOK.md 备份恢复演练** 的附件。

## Phase-1 / Phase-2 分界

| 阶段 | 条件 | 已完成项 | 待办 |
| --- | --- | --- | --- |
| **Phase-1** | 无公网 staging 域名 | Hub `:18000` health + `smoke_auth_flow` + checklist #5–#10 | — |
| **Phase-2** | 提供公网域名 + 443 | — | §8.1–§8.4 DNS/TLS/HTTPS smoke；SSL Labs 截图 · **⬜ PENDING** 见 [`phase-2-pending-20260706.md`](./phase-2-pending-20260706.md) |

Phase-1 归档（2026-07-06）：

- [`health-hub-local-20260706.json`](./health-hub-local-20260706.json)
- [`login-smoke-hub-local-20260706.txt`](./login-smoke-hub-local-20260706.txt)
- [`acceptance-checklist-hub-local-20260706.md`](./acceptance-checklist-hub-local-20260706.md)

Phase-2 触发：在项目根执行 `.\scripts\collect-https-evidence.ps1 -Domain <your-domain>`。

## 使用方式

1. 在目标环境（staging / production）执行 DEPLOYMENT §8.1–§8.4 命令。
2. 将输出或截图保存为本目录下的文件（见下表命名规范）。
3. 在变更单 / 发布记录中链接文件名；**勿**在 PR 中提交含真实 token 的 JSON。
4. 本仓库默认**不**提交真实生产截图；可只提交 `.example` 模板供团队复制。

## 文件命名规范

| 模式 | 用途 |
| --- | --- |
| `ssl-labs-<domain>-<YYYYMMDD>.png` | SSL Labs 评级截图 |
| `browser-https-<domain>-<YYYYMMDD>.png` | 浏览器 HTTPS 锁标 |
| `health-<env>-<YYYYMMDD>.json` | `/api/health` curl 输出 |
| `login-smoke-<env>-<YYYYMMDD>.txt` | 登录 200 摘要（**无 token 正文**） |
| `grafana-<panel>-<YYYYMMDD>.png` | Grafana 可用性 / 延迟面板 |
| `backup-drill-<YYYY>Q<n>.md` | 季度备份恢复演练记录 |

## 模板文件（可提交）

| 文件 | 说明 |
| --- | --- |
| [`health-local.example.json`](./health-local.example.json) | 本地 Docker smoke 健康检查样例 |
| [`health-hub-local-20260706.json`](./health-hub-local-20260706.json) | **Hub :18000 实测** health JSON（2026-07-06） |
| [`login-smoke-hub-local-20260706.txt`](./login-smoke-hub-local-20260706.txt) | **Hub 本地** smoke_auth_flow 摘要（无 token） |
| [`acceptance-checklist-hub-local-20260706.md`](./acceptance-checklist-hub-local-20260706.md) | Hub 本地 #5–#10 已勾选；#1–#4 待公网 |
| [`backup-drill.example.md`](./backup-drill.example.md) | 备份恢复演练 checklist 空白模板 |
| [`acceptance-checklist.example.md`](./acceptance-checklist.example.md) | 与 DEPLOYMENT §8.7 对应的可勾选清单 |

## 公网 HTTPS 采集脚本

在有公网域名时执行：

```powershell
cd enterprise-rag
.\scripts\collect-https-evidence.ps1 -Domain rag-staging.example.com
```

输出写入本目录 `dns-*` / `tls-*` / `health-*` / `login-smoke-*` 文件。

## 禁止提交

- 真实 JWT / API Key / 管理员密码
- 未脱敏客户文档或 PII
- 内网-only 域名若政策不允许公开

## 相关文档

- [DEPLOYMENT.md §8](../../DEPLOYMENT.md#8-公网-https-部署证据验收模板)
- [RUNBOOK.md § 备份恢复](../../RUNBOOK.md)
- [PERFORMANCE_REPORT.md](../../PERFORMANCE_REPORT.md)
