"""测试夹具与离线环境配置。

在导入应用前设置环境变量，将系统切到「离线三态」：
- Embedding = fake（确定性词袋向量，无需联网）
- LLM = echo（回显，无需联网）
- 向量库 = memory（进程内）
- 数据库 = 临时 SQLite 文件
如此可在无任何外部服务、无 API key 的情况下跑通完整链路。
"""

import os
import pathlib
import tempfile

_TMP = tempfile.mkdtemp(prefix="erag_test_")
_DB = pathlib.Path(_TMP, "test.db").as_posix()
_UPLOADS = pathlib.Path(_TMP, "uploads").as_posix()

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_DB}",
        "VECTOR_BACKEND": "memory",
        "UPLOAD_DIR": _UPLOADS,
        "EMBEDDING_PROVIDER": "fake",
        "EMBEDDING_MODEL": "fake",
        "EMBEDDING_DIM": "64",
        "LLM_PROVIDER": "echo",
        "LLM_MODEL": "echo",
        "RERANK_ENABLED": "true",
        "RERANK_PROVIDER": "lexical",
        "VECTOR_MIN_SCORE": "0.05",
        "HISTORY_TURNS": "4",
        "CHUNK_SIZE": "200",
        "CHUNK_OVERLAP": "40",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session(client):
    """复用 TestClient 启动后的 SQLite 库，供 RAG/检索单元测试直接写库。"""
    from sqlmodel import Session

    from app.database import engine

    with Session(engine) as session:
        yield session


@pytest.fixture()
def seeded_kb(db_session):
    """创建带 chunk 与向量的知识库，供 retrieve / RAG 单元测试复用。"""
    from app.core.embeddings import make_embeddings
    from app.core.vector_store import VectorPoint, collection_name, get_vector_store
    from app.models import Chunk, Document, DocStatus, KnowledgeBase, Tenant
    from sqlmodel import select

    tenant = db_session.exec(select(Tenant).where(Tenant.slug == "demo")).first()
    kb = KnowledgeBase(
        tenant_id=tenant.id,
        name="pytest-seed-kb",
        description="seed",
        embedding_provider="fake",
        embedding_model="fake",
        embedding_dim=64,
        vector_backend="memory",
    )
    db_session.add(kb)
    db_session.commit()
    db_session.refresh(kb)

    doc = Document(
        tenant_id=tenant.id,
        kb_id=kb.id,
        name="seed.txt",
        source_type="file",
        status=DocStatus.DONE,
        chunk_count=2,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    chunks = [
        Chunk(
            tenant_id=tenant.id,
            kb_id=kb.id,
            document_id=doc.id,
            chunk_index=0,
            content="公司为正式员工提供每年 15 天带薪年假，可在自然年度内灵活安排。",
            char_count=30,
            page=1,
            meta={"document_name": doc.name},
        ),
        Chunk(
            tenant_id=tenant.id,
            kb_id=kb.id,
            document_id=doc.id,
            chunk_index=1,
            content="差旅报销需在 OA 提交发票与出差申请，由主管审批后财务打款。",
            char_count=30,
            page=2,
            meta={"document_name": doc.name},
        ),
    ]
    for c in chunks:
        c.vector_id = c.id
        db_session.add(c)
    db_session.commit()

    embedder = make_embeddings(kb.embedding_provider, kb.embedding_model, kb.embedding_dim)
    texts = [c.content for c in chunks]
    vectors = embedder.embed_documents(texts)
    vs = get_vector_store()
    col = collection_name(kb.id)
    vs.ensure_collection(col, kb.embedding_dim)
    vs.upsert(
        col,
        [
            VectorPoint(
                id=c.id,
                vector=vec,
                payload={"chunk_id": c.id, "document_id": doc.id, "chunk_index": c.chunk_index},
            )
            for c, vec in zip(chunks, vectors)
        ],
    )
    return kb, doc, chunks
