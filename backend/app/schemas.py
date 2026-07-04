"""API 请求 / 响应模型（Pydantic）。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ==================== 知识库 ====================
class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""


class KBUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = None


class KBRead(BaseModel):
    id: str
    name: str
    description: str
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    vector_backend: str
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
    created_at: datetime
    updated_at: datetime


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
    content: str


class RetrieveRequest(BaseModel):
    kb_id: str
    query: str
    top_k: Optional[int] = None


class RetrieveResponse(BaseModel):
    query: str
    results: List[SourceChunk]


# ==================== 对话 / 问答 ====================
class ChatRequest(BaseModel):
    kb_id: str
    question: str = Field(..., min_length=1)
    conversation_id: Optional[str] = Field(
        default=None, description="为空则自动新建对话"
    )
    top_k: Optional[int] = None
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
    role: str
    content: str
    sources: List[SourceChunk] = []
    created_at: datetime


class ChatResponse(BaseModel):
    """非流式问答返回。"""

    conversation_id: str
    answer: str
    sources: List[SourceChunk] = []
