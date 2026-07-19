# Phase-2 公网 HTTPS 证据 · 待实采

> **状态**：⬜ **PENDING**（阻塞于公网 staging/production 域名）  
> **日期**：2026-07-06 · **负责人**：project-hub-1  
> **Phase-1**：✅ 已完成（Hub 本地 `:18000` / `:18001`）

---

## 为何 pending

| 条件 | 状态 |
| --- | --- |
| Phase-1 本地 Hub smoke（#5–#10） | ✅ [`acceptance-checklist-hub-local-20260706.md`](./acceptance-checklist-hub-local-20260706.md) |
| 公网域名 + 443 TLS | ❌ **未提供** — 无法实采 #1–#4、#12 |
| 采集脚本 | ✅ 就绪 [`scripts/collect-https-evidence.ps1`](../../scripts/collect-https-evidence.ps1) |

**结论**：本地证据已满足作品集 Hub Profile 验收；公网 HTTPS 属 **部署环境依赖**，不阻塞矩阵 ✓ 格，但阻塞 DIMENSION-AUDIT 部署维度 8→10。

---

## 一键实采命令（有域名后）

```powershell
cd enterprise-rag

# 1. 自动采集 DNS / TLS / health / login（脱敏）
.\scripts\collect-https-evidence.ps1 -Domain rag-staging.example.com `
  -AdminEmail admin@example.com `
  -AdminPassword '<your-staging-password>'

# 2. 完整 auth + RAG smoke（DEPLOYMENT §8.3）
python backend/scripts/smoke_auth_flow.py --base-url https://rag-staging.example.com

# 3. 手动补图（不入库含敏感信息）
#    docs/evidence/ssl-labs-rag-staging.example.com-YYYYMMDD.png
#    docs/evidence/browser-https-rag-staging.example.com-YYYYMMDD.png

# 4. 复制 checklist 并勾选 #1–#12
Copy-Item docs/evidence/acceptance-checklist.example.md `
  docs/evidence/acceptance-checklist-rag-staging-YYYYMMDD.md
```

---

## Phase-2 交付清单

| # | 产物 | 命名模式 |
| ---: | --- | --- |
| 1 | DNS 解析输出 | `dns-<domain>-<YYYYMMDD>.txt` |
| 2 | TLS verbose | `tls-<domain>-<YYYYMMDD>.txt` |
| 3 | Health JSON | `health-<domain>-<YYYYMMDD>.json` |
| 4 | Login smoke（无 token） | `login-smoke-<domain>-<YYYYMMDD>.txt` |
| 5 | SSL Labs 截图 | `ssl-labs-<domain>-<YYYYMMDD>.png` |
| 6 | 浏览器锁标截图 | `browser-https-<domain>-<YYYYMMDD>.png` |
| 7 | 验收 checklist #1–#12 全勾 | `acceptance-checklist-<env>-<YYYYMMDD>.md` |

---

## 关联

- [DEPLOYMENT.md §8](../../DEPLOYMENT.md#8-公网-https-部署证据验收模板)
- [DIMENSION-AUDIT.md §2](../DIMENSION-AUDIT.md#2-docker-与部署810)
- [evidence/README.md](./README.md)

*Phase-2 解除阻塞条件：提供 staging 域名（或用户授权使用现有公网实例）。*
