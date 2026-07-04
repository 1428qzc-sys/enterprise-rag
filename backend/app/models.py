"""数据库模型（SQLModel）。

实体关系：
    KnowledgeBase 1..* Document 1..* Chunk
    KnowledgeBase 1..* Conversation 1..* Message

设计要点：
- 每个知识库快照其 embedding 模型与维度：向量空间与模型强绑定，
  换模型必须重嵌入，避免维度/语义错配。
- Chunk 的正文同时落库：既用于 BM25 稀疏检索的重建，也用于引用溯源展示，
  无需回查向量库即可还原命中片段。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import JSON, Text
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


class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_base"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(index=True)
    description: str = Field(default="")
    # 该库使用的向量空间快照
    embedding_provider: str = Field(default="")
    embedding_model: str = Field(default="")
    embedding_dim: int = Field(default=0)
    vector_backend: str = Field(default="")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Document(SQLModel, table=True):
    __tablename__ = "document"

    id: str = Field(default_factory=_uuid, primary_key=True)
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
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Chunk(SQLModel, table=True):
    __tablename__ = "chunk"

    id: str = Field(default_factory=_uuid, primary_key=True)
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    document_id: str = Field(index=True, foreign_key="document.id")
    chunk_index: int = Field(default=0)
    content: str = Field(sa_type=Text)
    char_count: int = Field(default=0)
    page: Optional[int] = Field(default=None)
    meta: Dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    # 与向量库中 point id 保持一致（此处直接复用 chunk.id）
    vector_id: str = Field(default="")
    created_at: datetime = Field(default_factory=_now)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversation"

    id: str = Field(default_factory=_uuid, primary_key=True)
    kb_id: str = Field(index=True, foreign_key="knowledge_base.id")
    title: str = Field(default="新对话")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Message(SQLModel, table=True):
    __tablename__ = "message"

    id: str = Field(default_factory=_uuid, primary_key=True)
    conversation_id: str = Field(index=True, foreign_key="conversation.id")
    role: str = Field(default="user")  # user | assistant
    content: str = Field(sa_type=Text)
    # assistant 消息的引用来源快照（List[SourceChunk]）
    sources: List[Dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(default_factory=_now)
