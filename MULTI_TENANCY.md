# Enterprise RAG 多租户与权限模型

## 数据模型

核心表：

- `tenant`：租户。
- `app_user`：用户，必须归属一个 `tenant_id`。
- `app_role`：租户内角色。
- `app_permission`：权限码。
- `app_user_role`：用户角色关系。
- `app_role_permission`：角色权限关系。

业务表：

- `knowledge_base.tenant_id`
- `document.tenant_id`
- `chunk.tenant_id`
- `conversation.tenant_id`
- `audit_log.tenant_id`

所有业务数据必须绑定租户。旧演示数据会在启动时回填到 bootstrap 默认租户。

## 权限码

| 权限 | 说明 |
| --- | --- |
| `kb:read` | 读取本租户知识库 |
| `kb:write` | 创建与更新本租户知识库 |
| `kb:delete` | 删除本租户知识库 |
| `doc:read` | 读取本租户文档 |
| `doc:write` | 上传、抓取与重嵌入本租户文档 |
| `doc:delete` | 删除本租户文档 |
| `chat:use` | 使用本租户检索与问答 |
| `conversation:delete` | 删除本租户会话 |
| `admin:manage` | 管理本租户用户与角色 |

## 后端强制校验

后端依赖 `Authorization: Bearer <token>`：

1. 校验 HS256 JWT 签名与过期时间。
2. 读取用户、租户、权限。
3. 每个业务 API 先检查权限码。
4. 知识库、文档、会话接口必须通过 `tenant_id` 判断对象是否属于当前租户。
5. 不属于当前租户的对象统一返回 404，避免泄漏 ID 是否存在。

## 已覆盖的越权测试

`backend/tests/test_api.py` 已覆盖：

- 未登录访问业务 API 返回 401。
- 默认管理员可登录并读取权限。
- 第二租户用户不能读取第一租户知识库。
- 第二租户用户不能对第一租户知识库执行检索。
- 第二租户知识库列表不包含第一租户对象。

`backend/tests/test_security_hardening.py` 已覆盖：

- SSRF 私网/localhost/metadata URL 拒绝。
- URL 抓取拒绝事件写入租户审计日志。
- 安全响应头返回。

## 当前边界

- 当前版本提供租户内用户创建、用户列表和审计日志查询；角色编辑 UI 尚未实现。
- 超级管理员可跨租户查看业务数据，用于平台运维；普通租户用户不能跨租户。
- 已引入 Alembic 基线迁移；当前兼容迁移函数仅用于不删除旧本地 Docker volume 的平滑兜底。
