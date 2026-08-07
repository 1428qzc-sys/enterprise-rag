"""文档版本、任务恢复与数据/向量一致性回归。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from reportlab.pdfgen import canvas
from sqlmodel import Session, select

from app.api import documents as document_api
from app.core.vector_store import collection_for_kb, get_vector_store
from app.database import engine
from app.models import Document, DocumentVersion, IngestionJob, KnowledgeBase
from app.services import ingestion
from tests.test_api import _auth_headers, _create_kb


def _upload(client, kb_id: str, text: str, *, document_id: str | None = None):
    path = (
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/versions/upload"
        if document_id
        else f"/api/knowledge-bases/{kb_id}/documents/upload"
    )
    return client.post(
        path,
        files={"file": ("policy.txt", text.encode("utf-8"), "text/plain")},
        headers=_auth_headers(client),
    )


def _document(client, kb_id: str, document_id: str) -> dict:
    response = client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}",
        headers=_auth_headers(client),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _versions(client, kb_id: str, document_id: str) -> list[dict]:
    response = client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/versions",
        headers=_auth_headers(client),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _jobs(client, kb_id: str, document_id: str) -> list[dict]:
    response = client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/jobs",
        headers=_auth_headers(client),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _collection(kb_id: str) -> str:
    with Session(engine) as session:
        kb = session.get(KnowledgeBase, kb_id)
        assert kb is not None
        return collection_for_kb(kb)


def _two_page_pdf() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 760, "PAGE_ONE_ALPHA_POLICY annual leave is fifteen days.")
    pdf.showPage()
    pdf.drawString(72, 760, "PAGE_TWO_BETA_SECURITY recovery keys rotate every ninety days.")
    pdf.save()
    return output.getvalue()


def test_pdf_page_metadata_reaches_chunks_retrieval_and_chat_sources(client):
    kb_id = _create_kb(client, f"pdf-pages-{uuid4().hex[:8]}")
    headers = _auth_headers(client)
    uploaded = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/upload",
        files={"file": ("two-page-policy.pdf", _two_page_pdf(), "application/pdf")},
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    document = _document(client, kb_id, document_id)
    assert document["status"] == "done", document

    chunks_response = client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/chunks",
        headers=headers,
    )
    assert chunks_response.status_code == 200, chunks_response.text
    chunks = chunks_response.json()
    first_page = next(chunk for chunk in chunks if "PAGE_ONE_ALPHA_POLICY" in chunk["content"])
    second_page = next(chunk for chunk in chunks if "PAGE_TWO_BETA_SECURITY" in chunk["content"])
    assert first_page["page"] == 1
    assert second_page["page"] == 2

    retrieved = client.post(
        "/api/retrieve",
        json={
            "kb_id": kb_id,
            "query": "PAGE_TWO_BETA_SECURITY recovery keys",
            "top_k": 3,
        },
        headers=headers,
    )
    assert retrieved.status_code == 200, retrieved.text
    retrieval_hit = next(
        item for item in retrieved.json()["results"] if "PAGE_TWO_BETA_SECURITY" in item["content"]
    )
    assert retrieval_hit["chunk_id"] == second_page["id"]
    assert retrieval_hit["document_id"] == document_id
    assert retrieval_hit["page"] == 2

    answered = client.post(
        "/api/chat",
        json={
            "kb_id": kb_id,
            "question": "What does PAGE_TWO_BETA_SECURITY say about recovery keys?",
            "top_k": 3,
            "stream": False,
        },
        headers=headers,
    )
    assert answered.status_code == 200, answered.text
    payload = answered.json()
    source = next(
        item for item in payload["sources"] if "PAGE_TWO_BETA_SECURITY" in item["content"]
    )
    assert source["chunk_id"] == second_page["id"]
    assert source["document_id"] == document_id
    assert source["document_name"] == "two-page-policy.pdf"
    assert source["page"] == 2
    assert f"[{source['index']}]" in payload["answer"]


def test_version_update_duplicate_and_reembed_keep_one_active_vector_set(client):
    kb_id = _create_kb(client, f"version-{uuid4().hex[:8]}")
    v1_text = "版本一包含唯一术语 OLDONLY，年假为 15 天。"
    created = _upload(client, kb_id, v1_text)
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    first = _document(client, kb_id, document_id)
    assert first["status"] == "done"
    assert first["version"] == 1
    assert first["consistency_status"] == "consistent"

    store = get_vector_store()
    collection = _collection(kb_id)
    versions = _versions(client, kb_id, document_id)
    v1 = versions[0]
    assert v1["is_active"] is True
    assert store.count_version(collection, v1["id"]) == first["chunk_count"]

    duplicate = _upload(client, kb_id, v1_text)
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == document_id
    assert len(_versions(client, kb_id, document_id)) == 1
    assert store.count_document(collection, document_id) == first["chunk_count"]

    v2_text = "版本二包含唯一术语 NEWONLY，年假调整为 18 天。"
    updated = _upload(client, kb_id, v2_text, document_id=document_id)
    assert updated.status_code == 201, updated.text
    second = _document(client, kb_id, document_id)
    assert second["status"] == "done"
    assert second["version"] == 2
    versions = _versions(client, kb_id, document_id)
    active = next(version for version in versions if version["is_active"])
    inactive = next(version for version in versions if not version["is_active"])
    assert active["version_number"] == 2
    assert store.count_version(collection, inactive["id"]) == 0
    assert store.count_version(collection, active["id"]) == second["chunk_count"]
    assert store.count_document(collection, document_id) == second["chunk_count"]

    active_chunks = client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/chunks",
        headers=_auth_headers(client),
    )
    assert active_chunks.status_code == 200
    assert any("NEWONLY" in chunk["content"] for chunk in active_chunks.json())
    old_chunks = client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/chunks",
        params={"version_id": inactive["id"]},
        headers=_auth_headers(client),
    )
    assert old_chunks.status_code == 200
    assert any("OLDONLY" in chunk["content"] for chunk in old_chunks.json())

    reembed = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/reembed",
        headers=_auth_headers(client),
    )
    assert reembed.status_code == 202, reembed.text
    third = _document(client, kb_id, document_id)
    assert third["version"] == 3
    versions = _versions(client, kb_id, document_id)
    active = next(version for version in versions if version["is_active"])
    assert active["version_number"] == 3
    assert store.count_document(collection, document_id) == third["chunk_count"]
    assert sum(store.count_version(collection, version["id"]) for version in versions) == third[
        "chunk_count"
    ]


def test_vector_upsert_failure_keeps_old_version_and_retry_recovers(client, monkeypatch):
    kb_id = _create_kb(client, f"rollback-{uuid4().hex[:8]}")
    created = _upload(client, kb_id, "稳定版本 SAFEOLD 年假 15 天。")
    document_id = created.json()["id"]
    before = _document(client, kb_id, document_id)
    old_version_id = before["active_version_id"]
    store = get_vector_store()
    collection = _collection(kb_id)
    old_count = store.count_document(collection, document_id)
    original_upsert = store.upsert

    def fail_new_version(name, points):
        if any(point.payload.get("version_id") != old_version_id for point in points):
            raise RuntimeError("injected vector write failure")
        return original_upsert(name, points)

    monkeypatch.setattr(store, "upsert", fail_new_version)
    attempted = _upload(client, kb_id, "更新版本 UNSAFE_NEW 年假 18 天。", document_id=document_id)
    assert attempted.status_code == 201
    failed = _document(client, kb_id, document_id)
    assert failed["status"] == "failed"
    assert failed["active_version_id"] == old_version_id
    assert failed["consistency_status"] == "consistent"
    assert store.count_document(collection, document_id) == old_count
    failed_job = _jobs(client, kb_id, document_id)[0]
    assert failed_job["status"] == "failed"

    monkeypatch.setattr(store, "upsert", original_upsert)
    retried = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/jobs/{failed_job['id']}/retry",
        headers=_auth_headers(client),
    )
    assert retried.status_code == 200, retried.text
    recovered = _document(client, kb_id, document_id)
    assert recovered["status"] == "done"
    assert recovered["version"] == 2
    assert recovered["consistency_status"] == "consistent"
    assert store.count_document(collection, document_id) == recovered["chunk_count"]


def test_cleanup_failure_is_safe_and_reconcile_retry_removes_stale_vectors(client, monkeypatch):
    kb_id = _create_kb(client, f"cleanup-{uuid4().hex[:8]}")
    created = _upload(client, kb_id, "旧版本 CLEANUP_OLD。")
    document_id = created.json()["id"]
    old_version_id = _document(client, kb_id, document_id)["active_version_id"]
    store = get_vector_store()
    collection = _collection(kb_id)
    original_delete_version = store.delete_version

    def fail_old_cleanup(name: str, version_id: str):
        if version_id == old_version_id:
            raise RuntimeError("injected cleanup failure")
        return original_delete_version(name, version_id)

    monkeypatch.setattr(store, "delete_version", fail_old_cleanup)
    updated = _upload(client, kb_id, "新版本 CLEANUP_NEW。", document_id=document_id)
    assert updated.status_code == 201
    pending = _document(client, kb_id, document_id)
    assert pending["version"] == 2
    assert pending["status"] == "failed"
    assert pending["consistency_status"] == "pending_cleanup"
    assert store.count_version(collection, old_version_id) > 0
    cleanup_job = _jobs(client, kb_id, document_id)[0]
    assert cleanup_job["stage"] == "cleanup"

    monkeypatch.setattr(store, "delete_version", original_delete_version)
    retried = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/jobs/{cleanup_job['id']}/retry",
        headers=_auth_headers(client),
    )
    assert retried.status_code == 200
    reconciled = _document(client, kb_id, document_id)
    assert reconciled["status"] == "done"
    assert reconciled["consistency_status"] == "consistent"
    assert store.count_version(collection, old_version_id) == 0
    assert store.count_document(collection, document_id) == reconciled["chunk_count"]


def test_pending_job_can_be_canceled_then_retried(client, monkeypatch):
    kb_id = _create_kb(client, f"cancel-{uuid4().hex[:8]}")
    real_processor = ingestion.process_ingestion_job
    monkeypatch.setattr(document_api, "process_ingestion_job", lambda _job_id: None)
    created = _upload(client, kb_id, "可取消版本 CANCEL_ME。")
    document_id = created.json()["id"]
    pending_job = _jobs(client, kb_id, document_id)[0]
    assert pending_job["status"] == "pending"

    canceled = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/jobs/{pending_job['id']}/cancel",
        headers=_auth_headers(client),
    )
    assert canceled.status_code == 200
    real_processor(pending_job["id"])
    assert _jobs(client, kb_id, document_id)[0]["status"] == "canceled"
    assert _document(client, kb_id, document_id)["active_version_id"] == ""

    monkeypatch.setattr(document_api, "process_ingestion_job", real_processor)
    retried = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/jobs/{pending_job['id']}/retry",
        headers=_auth_headers(client),
    )
    assert retried.status_code == 200
    assert _document(client, kb_id, document_id)["status"] == "done"


def test_vector_delete_failure_preserves_metadata_then_success_cleans_everything(client, monkeypatch):
    kb_id = _create_kb(client, f"delete-{uuid4().hex[:8]}")
    created = _upload(client, kb_id, "待删除文档 DELETE_SAFE。")
    document_id = created.json()["id"]
    document = _document(client, kb_id, document_id)
    store = get_vector_store()
    collection = _collection(kb_id)
    with Session(engine) as session:
        version = session.get(DocumentVersion, document["active_version_id"])
        assert version is not None
        stored_path = Path(version.stored_path)
    assert stored_path.exists()
    original_delete = store.delete_document

    def fail_delete(_name: str, _document_id: str):
        raise RuntimeError("injected vector delete failure")

    monkeypatch.setattr(store, "delete_document", fail_delete)
    denied = client.delete(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}",
        headers=_auth_headers(client),
    )
    assert denied.status_code == 503
    assert _document(client, kb_id, document_id)["id"] == document_id
    assert store.count_document(collection, document_id) == document["chunk_count"]

    monkeypatch.setattr(store, "delete_document", original_delete)
    deleted = client.delete(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}",
        headers=_auth_headers(client),
    )
    assert deleted.status_code == 204
    assert store.count_document(collection, document_id) == 0
    assert not stored_path.exists()
    with Session(engine) as session:
        assert session.get(Document, document_id) is None
        assert session.exec(
            select(IngestionJob).where(IngestionJob.document_id == document_id)
        ).first() is None


def test_database_activation_failure_removes_staging_vectors_and_keeps_old_version(
    client, monkeypatch
):
    kb_id = _create_kb(client, f"db-activate-{uuid4().hex[:8]}")
    created = _upload(client, kb_id, "数据库回滚旧版本 DB_OLD。")
    document_id = created.json()["id"]
    old_document = _document(client, kb_id, document_id)
    old_version_id = old_document["active_version_id"]
    store = get_vector_store()
    collection = _collection(kb_id)
    old_count = store.count_document(collection, document_id)
    real_processor = ingestion.process_ingestion_job
    monkeypatch.setattr(document_api, "process_ingestion_job", lambda _job_id: None)
    pending = _upload(client, kb_id, "数据库回滚新版本 DB_NEW。", document_id=document_id)
    assert pending.status_code == 201
    job = _jobs(client, kb_id, document_id)[0]

    original_commit = Session.commit
    injected = {"done": False}

    def fail_activation_once(db_session):
        if not injected["done"]:
            for obj in db_session.dirty:
                if (
                    isinstance(obj, Document)
                    and obj.id == document_id
                    and obj.active_version_id
                    and obj.active_version_id != old_version_id
                ):
                    injected["done"] = True
                    raise RuntimeError("injected database activation failure")
        return original_commit(db_session)

    monkeypatch.setattr(Session, "commit", fail_activation_once)
    real_processor(job["id"])
    assert injected["done"] is True
    failed = _document(client, kb_id, document_id)
    assert failed["status"] == "failed"
    assert failed["active_version_id"] == old_version_id
    assert store.count_document(collection, document_id) == old_count
    versions = _versions(client, kb_id, document_id)
    failed_version = next(version for version in versions if version["version_number"] == 2)
    assert store.count_version(collection, failed_version["id"]) == 0

    monkeypatch.setattr(Session, "commit", original_commit)
    monkeypatch.setattr(document_api, "process_ingestion_job", real_processor)
    retried = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/jobs/{job['id']}/retry",
        headers=_auth_headers(client),
    )
    assert retried.status_code == 200
    assert _document(client, kb_id, document_id)["version"] == 2


def test_database_delete_failure_restores_vectors_and_preserves_document(client, monkeypatch):
    kb_id = _create_kb(client, f"db-delete-{uuid4().hex[:8]}")
    created = _upload(client, kb_id, "数据库删除补偿 DB_DELETE_RESTORE。")
    document_id = created.json()["id"]
    document = _document(client, kb_id, document_id)
    collection = _collection(kb_id)
    store = get_vector_store()
    headers = _auth_headers(client)
    original_commit = Session.commit
    injected = {"done": False}

    def fail_delete_once(db_session):
        if not injected["done"] and any(
            isinstance(obj, Document) and obj.id == document_id for obj in db_session.deleted
        ):
            injected["done"] = True
            raise RuntimeError("injected metadata delete failure")
        return original_commit(db_session)

    monkeypatch.setattr(Session, "commit", fail_delete_once)
    response = client.delete(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}", headers=headers
    )
    assert response.status_code == 500
    assert injected["done"] is True
    assert _document(client, kb_id, document_id)["id"] == document_id
    assert store.count_document(collection, document_id) == document["chunk_count"]

    monkeypatch.setattr(Session, "commit", original_commit)
    deleted = client.delete(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}", headers=headers
    )
    assert deleted.status_code == 204
