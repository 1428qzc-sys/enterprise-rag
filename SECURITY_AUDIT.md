# Enterprise RAG 安全审计快照

审计版本：`1.0.0-rc.1`
审计日期：2026-07-20
范围：FastAPI、Vue、PostgreSQL、Qdrant、Redis、文件/URL 入库、RAG 检索与 Docker 配置。

本文只记录可由当前代码和测试复现的控制，不是第三方认证或公网渗透测试报告。

## 风险与证据

| 风险 | 当前控制 | 自动化证据 |
| --- | --- | --- |
| 未登录或权限绕过 | JWT、主体重载、服务端权限依赖 | `test_api.py`、`test_tenant_rbac.py` |
| 跨租户对象访问 | 所有资源按 tenant 过滤，外租户对象返回 404 | 完整读/写/删/版本/任务矩阵 |
| superuser 跨租户 | superuser 只获得本租户全部权限 | `test_superuser_cannot_cross_tenant` 类回归 |
| 限流多副本不一致 | Redis Lua 原子计数；生产强制 fail-closed | `test_rate_limit.py` |
| SSRF/metadata | DNS/IP/每次重定向复核、大小与超时限制 | `test_security_hardening.py` |
| 伪造文件扩展名 | PDF/Office/Text 内容结构检查 | 文档生命周期文件安全参数化测试 |
| 数据/向量错位 | 版本 staging、激活补偿、删除恢复、reconcile | `test_document_lifecycle.py`、`test_vector_store_contract.py` |
| 向量空间混用 | collection 维度校验、新 revision 构建后切换 | `test_reindexing.py` |
| 假引用 | 来源回源过滤、答案引用范围校验、无证据短路 | `test_rag.py`、固定评估、真实 PDF 页码测试 |
| Prompt injection | 可疑 Chunk 标记并从生成上下文隔离 | 固定评估 injection 用例 |
| 日志泄密 | 字段白名单、Token/secret/email 脱敏、不记正文 | `test_observability.py` |
| 演示配置误上生产 | production 启动门禁拒绝 Mock 和单进程后端 | `test_observability.py` |

## 仍需部署方承担

- 真实域名、TLS/WAF 和公网攻击面测试。
- Secret manager、密钥轮换和集中 SIEM。
- 恶意文件病毒扫描或 CDR。
- 真实模型供应商的数据使用、越狱与内容安全评估。
- 针对组织数据分类的保留、删除、加密与合规流程。

## 已知限制

浏览器令牌位于 local storage，没有 refresh/revocation 列表；上传没有病毒扫描；提示注入检测属于启发式防御。上述边界已在 [SECURITY.md](SECURITY.md) 明示，不作为“已消除风险”宣传。

正式依赖与 secret scan 结果以本次验收输出为准，未运行前不在本文预写“零漏洞”结论。
