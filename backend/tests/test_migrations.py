"""Alembic 空库往返与旧文档 v1 回填演练。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic(database: Path, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_migration_roundtrip_and_legacy_document_backfill(tmp_path):
    database = tmp_path / "migration.db"
    _alembic(database, "upgrade", "20260706_0001")
    now = "2026-07-19 00:00:00"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO tenant (id, name, slug, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tenant-1", "Legacy Tenant", "legacy", 1, now, now),
        )
        connection.execute(
            "INSERT INTO app_user (id, tenant_id, email, display_name, password_hash, "
            "is_active, is_superuser, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("user-1", "tenant-1", "legacy@example.com", "Legacy", "hash", 1, 1, now, now),
        )
        connection.execute(
            "INSERT INTO knowledge_base (id, tenant_id, created_by_user_id, name, description, "
            "embedding_provider, embedding_model, embedding_dim, vector_backend, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("kb-1", "tenant-1", "user-1", "Legacy KB", "", "fake", "fake", 64, "memory", now, now),
        )
        connection.execute(
            "INSERT INTO document (id, tenant_id, created_by_user_id, kb_id, name, source_type, "
            "source, mime, size_bytes, status, error, chunk_count, stored_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "doc-1",
                "tenant-1",
                "user-1",
                "kb-1",
                "legacy.txt",
                "file",
                "legacy.txt",
                "text/plain",
                12,
                "done",
                "",
                1,
                "data/legacy.txt",
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO chunk (id, tenant_id, kb_id, document_id, chunk_index, content, "
            "char_count, page, meta, vector_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("chunk-1", "tenant-1", "kb-1", "doc-1", 0, "legacy content", 14, 1, "{}", "chunk-1", now),
        )
        connection.commit()

    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        document = connection.execute(
            "SELECT active_version_id, version, latest_version, consistency_status "
            "FROM document WHERE id = 'doc-1'"
        ).fetchone()
        assert document is not None
        version_id, version, latest_version, consistency = document
        assert version_id
        assert (version, latest_version, consistency) == (1, 1, "consistent")
        migrated_version = connection.execute(
            "SELECT document_id, version_number, is_active, embedding_dim "
            "FROM document_version WHERE id = ?",
            (version_id,),
        ).fetchone()
        assert migrated_version == ("doc-1", 1, 1, 64)
        migrated_chunk = connection.execute(
            "SELECT version_id, is_active FROM chunk WHERE id = 'chunk-1'"
        ).fetchone()
        assert migrated_chunk == (version_id, 1)
        collection = connection.execute(
            "SELECT vector_collection, vector_revision FROM knowledge_base WHERE id = 'kb-1'"
        ).fetchone()
        assert collection == ("kb_kb-1", 1)

    _alembic(database, "downgrade", "base")
    _alembic(database, "upgrade", "head")
