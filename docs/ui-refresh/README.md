# Enterprise RAG UI 刷新截图

Playwright 脚本：`frontend/scripts/ui-screenshots.mjs`

## 运行

```bash
cd enterprise-rag/frontend
# 终端 1：Hub 后端或本地 backend 在 18000
# 终端 2：
$env:VITE_PROXY_TARGET="http://127.0.0.1:18000"
npx vite --host 127.0.0.1 --port 5175
# 终端 3：
node scripts/ui-screenshots.mjs
```

## 产出

| 文件 | 说明 |
| --- | --- |
| `01-login-light-after.png` | 登录页（浅色 + 错误态样式） |
| `02-dashboard-light-after.png` | 工作台统计卡 + 知识库网格 |
| `03-dashboard-dark-after.png` | 暗色主题一致性 |
| `04-documents-dark-after.png` | 文档列表 loading/表格 |
| `05-chat-empty-after.png` | 问答空状态 + 操作提示 |
| `06-chat-citations-after.png` | 流式问答 + 引用溯源面板（面试速览归档） |

> **面试速览**：`06-chat-citations-after.png` 同步复制到 `_optimization-screenshots/enterprise-rag/`，供 [INTERVIEW-GUIDE.md](../INTERVIEW-GUIDE.md) 引用。
