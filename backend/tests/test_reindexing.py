"""知识库模型/维度重建、回滚与重试。"""

from __future__ import annotations

from uuid import uuid4

from sqlmodel import Session

from app.core.vector_store import get_vector_store
from app.database import engine
from app.models import DocumentVersion, KnowledgeBase
from app.services import reindexing
from tests.test_api import _auth_headers, _create_kb
from tests.test_document_lifecycle import _document, _upload


def _kb(client, kb_id: str) -> dict:
    response = client.get(f"/api/knowledge-bases/{kb_id}", headers=_auth_headers(client))
    assert response.status_code == 200, response.text
    return response.json()


def _jobs(client, kb_id: str) -> list[dict]:
    response = client.get(
        f"/api/knowledge-bases/{kb_id}/reindex-jobs", headers=_auth_headers(client)
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_reindex_switches_dimension_only_after_new_collection_is_complete(client):
    kb_id = _create_kb(client, f"reindex-{uuid4().hex[:8]}")
    created = _upload(client, kb_id, "维度重建术语 DIMENSION_SAFE，年假 15 天。")
    document_id = created.json()["id"]
    document = _document(client, kb_id, document_id)
    before = _kb(client, kb_id)
    old_collection = before["vector_collection"]
    old_revision = before["vector_revision"]
    store = get_vector_store()
    assert store.collection_dimension(old_collection) == 64

    response = client.post(
        f"/api/knowledge-bases/{kb_id}/reindex",
        json={
            "embedding_provider": "fake",
            "embedding_model": "fake-32",
            "embedding_dim": 32,
        },
        headers=_auth_headers(client),
    )
    assert response.status_code == 202, response.text

    after = _kb(client, kb_id)
    assert after["embedding_dim"] == 32
    assert after["embedding_model"] == "fake-32"
    assert after["vector_revision"] == old_revision + 1
    assert after["vector_collection"] != old_collection
    assert after["reindex_status"] == "done"
    assert after["consistency_status"] == "consistent"
    assert store.collection_dimension(after["vector_collection"]) == 32
    assert store.count(after["vector_collection"]) == document["chunk_count"]
    assert store.count(old_collection) == 0
    assert store.collection_dimension(old_collection) is None

    with Session(engine) as session:
        version = session.get(DocumentVersion, document["active_version_id"])
        assert version is not None
        assert version.embedding_dim == 32
        assert version.embedding_model == "fake-32"

    retrieved = client.post(
        "/api/retrieve",
        json={"kb_id": kb_id, "query": "DIMENSION_SAFE"},
        headers=_auth_headers(client),
    )
    assert retrieved.status_code == 200
    assert any("DIMENSION_SAFE" in row["content"] for row in retrieved.json()["results"])


def test_reindex_embedding_failure_keeps_old_collection_then_retry_succeeds(client, monkeypatch):
    kb_id = _create_kb(client, f"reindex-fail-{uuid4().hex[:8]}")
    created = _upload(client, kb_id, "重建回滚术语 REINDEX_ROLLBACK。")
    document = _document(client, kb_id, created.json()["id"])
    before = _kb(client, kb_id)
    old_collection = before["vector_collection"]
    store = get_vector_store()
    old_count = store.count(old_collection)
    original_factory = reindexing.make_embeddings

    class BrokenEmbeddings:
        def embed_documents(self, _texts):
            raise RuntimeError("injected embedding failure")

    monkeypatch.setattr(reindexing, "make_embeddings", lambda *_args, **_kwargs: BrokenEmbeddings())
    attempted = client.post(
        f"/api/knowledge-bases/{kb_id}/reindex",
        json={
            "embedding_provider": "fake",
            "embedding_model": "fake-24",
            "embedding_dim": 24,
        },
        headers=_auth_headers(client),
    )
    assert attempted.status_code == 202
    failed = _kb(client, kb_id)
    assert failed["vector_collection"] == old_collection
    assert failed["embedding_dim"] == 64
    assert failed["reindex_status"] == "failed"
    assert failed["consistency_status"] == "consistent"
    assert store.count(old_collection) == old_count == document["chunk_count"]
    job = _jobs(client, kb_id)[0]
    assert job["status"] == "failed"
    assert store.collection_dimension(job["target_collection"]) is None

    monkeypatch.setattr(reindexing, "make_embeddings", original_factory)
    retried = client.post(
        f"/api/knowledge-bases/{kb_id}/reindex-jobs/{job['id']}/retry",
        headers=_auth_headers(client),
    )
    assert retried.status_code == 200
    recovered = _kb(client, kb_id)
    assert recovered["embedding_dim"] == 24
    assert recovered["vector_collection"] == job["target_collection"]
    assert recovered["reindex_status"] == "done"
    assert recovered["consistency_status"] == "consistent"
    assert store.count(recovered["vector_collection"]) == document["chunk_count"]
    assert store.collection_dimension(old_collection) is None
