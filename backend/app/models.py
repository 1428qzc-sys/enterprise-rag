"""数据库模型（SQLModel）。

实体关系：
    Tenant 1..* User / KnowledgeBase / Document / Chunk / Conversation / AuditLog
    User *..* Role *..* Permission
    KnowledgeBase 1..* Document 1..* Chunk
    KnowledgeBase 1..* Conversation 1..* Message

设计要点：
- 所有业务数据均写入 tenant_id：租户隔离必须由后端查询条件强制执行，
  不能只依赖前端隐藏入口。
- 每个知识库快照其 embedding 模型与维度：向量空间与模型强绑定，
  换模型必须重嵌入，避免维度/语义错配。
- Chunk 的正文同时落库：既用于 BM25 稀疏检索的重建，也用于引用溯源展示，
  无需回查向量库即可还原命中片段。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import JSON, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.utcnow()


# ---------------- 文档处理状态 ----------------
class DocStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"
    DELETING = "deleting"


class JobStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class ConsistencyStatus:
    CONSISTENT = "consistent"
    PENDING = "pending"
    PENDING_CLEANUP = "pending_cleanup"
    INCONSISTENT = "inconsistent"


class Tenant(SQLModel, table=True):
    __tablename__ = "tenant"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(index=True)
    slug: str = Field(index=True, sa_column_kwargs={"unique": True})
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class User(SQLModel, table=True):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_app_user_tenant_email"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True, foreign_key="tenant.id")
    email: str = Field(index=True)
    display_name: str = Field(default="")
    password_hash: str
    is_active: bool = Field(default=True, index=True)
    is_superuser: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Role(SQLModel, table=True):
    __tablename__ = "app_role"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_app_role_tenant_name"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True, foreign_key="tenant.id")
    name: str = Field(index=True)
    description: str = Field(default="")
    created_at: datetime = Field(default_factory=_now)


class Permission(SQLModel, table=True):
    __tablename__ = "app_permission"

    code: str = Field(primary_key=True)
    description: str = Field(default="")


class UserRole(SQLModel, table=True):
    __tablename__ = "app_user_role"

    user_id: str = Field(foreign_key="app_user.id", primary_key=True)
    role_id: str = Field(foreign_key="app_role.id", primary_key=True)


class RolePermission(SQLModel, table=True):
    __tablename__ = "app_role_permission"

    role_id: str = Field(foreign_key="app_role.id", primary_key=True)
    permission_code: str = Field(foreign_key="app_permission.code", primary_key=True)


class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_base"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(default="", index=True, foreign_key="tenant.id")
    created_by_user_id: str = Field(default="", index=True, foreign_key="app_user.id")
    name: str = Field(index=True)
    description: str = Field(default="")
    # 该库使用的向量空间快照
    embedding_provider: str = Field(default="")
    embedding_model: str = Field(default="")
    embedding_dim: int = Field(default=0)
    vector_backend: str = Field(default="")
    vector_collection: str = Field(default="", index=True)
    vector_revision: int = Field(default=1)
    reindex_status: str = Field(default="idle", index=True)
    reindex_progress: int = Field(default=0)
    reindex_error: str = Field(default="", sa_type=Text)
    consistency_status: str = Field(default=ConsistencyStatus.CONSISTENT, index=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Document(SQLModel, table=True):
    __tablename__ = "document"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(default="", index=True, foreign_key="tenant.id")
    created_by_user_id: str = Field(default="", index=True, foreign_key="app_user.id")
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    name: str
    source_type: str = Field(default="file")  # file | url
    source: str = Field(default="")           # 原始文件名或 URL
    mime: str = Field(default="")
    size_bytes: int = Field(default=0)
    status: str = Field(default=DocStatus.PENDING, index=True)
    error: str = Field(default="", sa_type=Text)
    chunk_count: int = Field(default=0)
    stored_path: str = Field(default="")      # 落盘路径（可重嵌入）
    active_version_id: str = Field(default="", index=True)
    version: int = Field(default=0)
    latest_version: int = Field(default=0)
    content_hash: str = Field(default="", index=True)
    progress: int = Field(default=0)
    retry_count: int = Field(default=0)
    cancel_requested: bool = Field(default=False, index=True)
    consistency_status: str = Field(default=ConsistencyStatus.PENDING, index=True)
    cleanup_error: str = Field(default="", sa_type=Text)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class KnowledgeBaseReindexJob(SQLModel, table=True):
    __tablename__ = "knowledge_base_reindex_job"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True, foreign_key="tenant.id")
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    status: str = Field(default=JobStatus.PENDING, index=True)
    stage: str = Field(default="queued", index=True)
    progress: int = Field(default=0)
    attempt: int = Field(default=0)
    max_attempts: int = Field(default=3)
    cancel_requested: bool = Field(default=False, index=True)
    target_provider: str
    target_model: str
    target_dim: int
    target_revision: int
    target_collection: str
    previous_collection: str
    error: str = Field(default="", sa_type=Text)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)


class DocumentVersion(SQLModel, table=True):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True, foreign_key="tenant.id")
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    document_id: str = Field(index=True, foreign_key="document.id")
    created_by_user_id: str = Field(default="", index=True, foreign_key="app_user.id")
    version_number: int = Field(index=True)
    name: str
    source_type: str = Field(default="file")
    source: str = Field(default="")
    mime: str = Field(default="")
    size_bytes: int = Field(default=0)
    stored_path: str = Field(default="")
    content_hash: str = Field(default="", index=True)
    status: str = Field(default=DocStatus.PENDING, index=True)
    error: str = Field(default="", sa_type=Text)
    progress: int = Field(default=0)
    chunk_count: int = Field(default=0)
    embedding_provider: str = Field(default="")
    embedding_model: str = Field(default="")
    embedding_dim: int = Field(default=0)
    is_active: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class IngestionJob(SQLModel, table=True):
    __tablename__ = "ingestion_job"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True, foreign_key="tenant.id")
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    document_id: str = Field(index=True, foreign_key="document.id")
    version_id: str = Field(index=True, foreign_key="document_version.id")
    kind: str = Field(default="ingest", index=True)
    status: str = Field(default=JobStatus.PENDING, index=True)
    stage: str = Field(default="queued", index=True)
    progress: int = Field(default=0)
    attempt: int = Field(default=0)
    max_attempts: int = Field(default=3)
    cancel_requested: bool = Field(default=False, index=True)
    idempotency_key: str = Field(index=True, sa_column_kwargs={"unique": True})
    error: str = Field(default="", sa_type=Text)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)


class Chunk(SQLModel, table=True):
    __tablename__ = "chunk"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_id",
            "chunk_index",
            name="uq_chunk_document_version_index",
        ),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(default="", index=True, foreign_key="tenant.id")
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    document_id: str = Field(index=True, foreign_key="document.id")
    version_id: str = Field(default="", index=True, foreign_key="document_version.id")
    chunk_index: int = Field(default=0)
    content: str = Field(sa_type=Text)
    char_count: int = Field(default=0)
    page: Optional[int] = Field(default=None)
    meta: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    # 与向量库中 point id 保持一致（此处直接复用 chunk.id）
    vector_id: str = Field(default="")
    is_active: bool = Field(default=True, index=True)
    injection_risk: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversation"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(default="", index=True, foreign_key="tenant.id")
    created_by_user_id: str = Field(default="", index=True, foreign_key="app_user.id")
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    title: str = Field(default="新对话")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Message(SQLModel, table=True):
    __tablename__ = "message"

    id: str = Field(default_factory=_uuid, primary_key=True)
    conversation_id: str = Field(index=True, foreign_key="conversation.id")
    request_id: str = Field(default="", index=True, max_length=64)
    role: str = Field(default="user")  # user | assistant
    content: str = Field(sa_type=Text)
    # assistant 消息的引用来源快照（List[SourceChunk]）
    sources: List[Dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(default_factory=_now)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(default="", index=True)
    user_id: str = Field(default="", index=True)
    action: str = Field(index=True)
    resource_type: str = Field(default="", index=True)
    resource_id: str = Field(default="", index=True)
    outcome: str = Field(default="success", index=True)
    ip_address: str = Field(default="")
    user_agent: str = Field(default="")
    detail: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=_now, index=True)
