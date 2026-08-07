"""API 请求 / 响应模型（Pydantic）。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ==================== 知识库 ====================
class TenantRead(BaseModel):
    id: str
    name: str
    slug: str


class UserRead(BaseModel):
    id: str
    tenant_id: str
    email: str
    display_name: str
    is_active: bool
    is_superuser: bool
    permissions: List[str] = Field(default_factory=list)
    role_ids: List[str] = Field(default_factory=list)
    role_names: List[str] = Field(default_factory=list)


class LoginRequest(BaseModel):
    tenant_slug: Optional[str] = Field(default=None, min_length=1, max_length=64)
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
    tenant: TenantRead


class MeResponse(BaseModel):
    user: UserRead
    tenant: TenantRead


class AdminUserCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=64)
    is_active: bool = True
    role_ids: List[str] = Field(default_factory=list)


class AdminUserUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=64)
    password: Optional[str] = Field(default=None, min_length=8, max_length=256)
    is_active: Optional[bool] = None
    role_ids: Optional[List[str]] = None


class PermissionRead(BaseModel):
    code: str
    description: str


class RoleRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    permissions: List[str] = Field(default_factory=list)
    user_count: int = 0
    is_system: bool = False
    created_at: datetime


class AdminRoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    permissions: List[str] = Field(default_factory=list)


class AdminRoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = Field(default=None, max_length=256)
    permissions: Optional[List[str]] = None


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    user_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    ip_address: str
    user_agent: str
    detail: dict
    created_at: datetime


class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""


class KBUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = None


class KBRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    vector_backend: str
    vector_collection: str
    vector_revision: int
    reindex_status: str
    reindex_progress: int
    reindex_error: str
    consistency_status: str
    document_count: int = 0
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime


# ==================== 文档 ====================
class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kb_id: str
    name: str
    source_type: str
    source: str
    mime: str
    size_bytes: int
    status: str
    error: str
    chunk_count: int
    active_version_id: str
    version: int
    latest_version: int
    content_hash: str
    progress: int
    retry_count: int
    cancel_requested: bool
    consistency_status: str
    cleanup_error: str
    created_at: datetime
    updated_at: datetime


class KBReindexRequest(BaseModel):
    embedding_provider: str = Field(..., pattern="^(openai|ollama|fake)$")
    embedding_model: str = Field(..., min_length=1, max_length=256)
    embedding_dim: int = Field(..., ge=1, le=65536)


class KBReindexJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kb_id: str
    status: str
    stage: str
    progress: int
    attempt: int
    max_attempts: int
    cancel_requested: bool
    target_provider: str
    target_model: str
    target_dim: int
    target_revision: int
    target_collection: str
    previous_collection: str
    error: str
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


class DocumentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    version_number: int
    name: str
    source_type: str
    source: str
    mime: str
    size_bytes: int
    content_hash: str
    status: str
    error: str
    progress: int
    chunk_count: int
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class IngestionJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    version_id: str
    kind: str
    status: str
    stage: str
    progress: int
    attempt: int
    max_attempts: int
    cancel_requested: bool
    error: str
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    version_id: str
    chunk_index: int
    content: str
    char_count: int
    page: Optional[int]
    meta: dict
    is_active: bool
    injection_risk: bool


class IngestUrlRequest(BaseModel):
    url: str = Field(..., description="要抓取并入库的网页 URL")


# ==================== 检索 / 引用 ====================
class SourceChunk(BaseModel):
    """一条引用来源（供溯源展示）。"""

    index: int = Field(..., description="在上下文中的引用序号，对应答案里的 [n]")
    chunk_id: str
    document_id: str
    document_name: str
    chunk_index: int
    page: Optional[int] = None
    score: float = 0.0
    score_type: str = "rrf"
    vector_score: Optional[float] = None
    bm25_score: Optional[float] = None
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None
    injection_risk: bool = False
    content: str


class RetrieveRequest(BaseModel):
    kb_id: str
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)


class RetrieveResponse(BaseModel):
    query: str
    results: List[SourceChunk]
    diagnostics: dict = Field(default_factory=dict)


# ==================== 对话 / 问答 ====================
class ChatRequest(BaseModel):
    kb_id: str
    question: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[str] = Field(
        default=None, description="为空则自动新建对话"
    )
    request_id: Optional[str] = Field(default=None, min_length=8, max_length=64)
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    stream: bool = True


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kb_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    request_id: str
    role: str
    content: str
    sources: List[SourceChunk] = Field(default_factory=list)
    created_at: datetime


class ChatResponse(BaseModel):
    """非流式问答返回。"""

    conversation_id: str
    request_id: str
    answer: str
    sources: List[SourceChunk] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)
