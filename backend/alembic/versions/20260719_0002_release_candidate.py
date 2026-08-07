"""release candidate: versioned ingestion, tenant uniqueness and reindex jobs

Revision ID: 20260719_0002
Revises: 20260706_0001
Create Date: 2026-07-19
"""

from __future__ import annotations

from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "20260719_0002"
down_revision = "20260706_0001"
branch_labels = None
depends_on = None

_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _drop_global_user_email_unique() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE app_user DROP CONSTRAINT IF EXISTS app_user_email_key")
    else:
        with op.batch_alter_table(
            "app_user", recreate="always", naming_convention=_NAMING
        ) as batch:
            batch.drop_constraint("uq_app_user_email", type_="unique")


def upgrade() -> None:
    _drop_global_user_email_unique()
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("app_user", recreate="always") as batch:
            batch.create_unique_constraint(
                "uq_app_user_tenant_email", ["tenant_id", "email"]
            )
        with op.batch_alter_table("app_role", recreate="always") as batch:
            batch.create_unique_constraint(
                "uq_app_role_tenant_name", ["tenant_id", "name"]
            )
    else:
        op.create_unique_constraint(
            "uq_app_user_tenant_email", "app_user", ["tenant_id", "email"]
        )
        op.create_unique_constraint(
            "uq_app_role_tenant_name", "app_role", ["tenant_id", "name"]
        )

    op.add_column(
        "knowledge_base",
        sa.Column("vector_collection", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "knowledge_base",
        sa.Column("vector_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "knowledge_base",
        sa.Column("reindex_status", sa.String(), nullable=False, server_default="idle"),
    )
    op.add_column(
        "knowledge_base",
        sa.Column("reindex_progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "knowledge_base",
        sa.Column("reindex_error", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "knowledge_base",
        sa.Column(
            "consistency_status", sa.String(), nullable=False, server_default="consistent"
        ),
    )
    op.create_index("ix_knowledge_base_vector_collection", "knowledge_base", ["vector_collection"])
    op.create_index("ix_knowledge_base_reindex_status", "knowledge_base", ["reindex_status"])
    op.create_index(
        "ix_knowledge_base_consistency_status", "knowledge_base", ["consistency_status"]
    )
    op.execute(
        "UPDATE knowledge_base SET vector_collection = 'kb_' || id "
        "WHERE vector_collection = ''"
    )

    for name, column in (
        ("active_version_id", sa.Column("active_version_id", sa.String(), nullable=False, server_default="")),
        ("version", sa.Column("version", sa.Integer(), nullable=False, server_default="0")),
        ("latest_version", sa.Column("latest_version", sa.Integer(), nullable=False, server_default="0")),
        ("content_hash", sa.Column("content_hash", sa.String(), nullable=False, server_default="")),
        ("progress", sa.Column("progress", sa.Integer(), nullable=False, server_default="0")),
        ("retry_count", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0")),
        (
            "cancel_requested",
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        (
            "consistency_status",
            sa.Column(
                "consistency_status", sa.String(), nullable=False, server_default="pending"
            ),
        ),
        ("cleanup_error", sa.Column("cleanup_error", sa.Text(), nullable=False, server_default="")),
    ):
        del name
        op.add_column("document", column)
    op.create_index("ix_document_active_version_id", "document", ["active_version_id"])
    op.create_index("ix_document_content_hash", "document", ["content_hash"])
    op.create_index("ix_document_cancel_requested", "document", ["cancel_requested"])
    op.create_index("ix_document_consistency_status", "document", ["consistency_status"])

    op.create_table(
        "document_version",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id"), nullable=False),
        sa.Column("document_id", sa.String(), sa.ForeignKey("document.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("embedding_provider", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
    )
    for column in (
        "tenant_id",
        "kb_id",
        "document_id",
        "created_by_user_id",
        "version_number",
        "content_hash",
        "status",
        "is_active",
    ):
        op.create_index(f"ix_document_version_{column}", "document_version", [column])

    op.create_table(
        "ingestion_job",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id"), nullable=False),
        sa.Column("document_id", sa.String(), sa.ForeignKey("document.id"), nullable=False),
        sa.Column("version_id", sa.String(), sa.ForeignKey("document_version.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    for column in (
        "tenant_id",
        "kb_id",
        "document_id",
        "version_id",
        "kind",
        "status",
        "stage",
        "cancel_requested",
        "idempotency_key",
    ):
        op.create_index(f"ix_ingestion_job_{column}", "ingestion_job", [column])

    op.create_table(
        "knowledge_base_reindex_job",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("target_provider", sa.String(), nullable=False),
        sa.Column("target_model", sa.String(), nullable=False),
        sa.Column("target_dim", sa.Integer(), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("target_collection", sa.String(), nullable=False),
        sa.Column("previous_collection", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    for column in ("tenant_id", "kb_id", "status", "stage", "cancel_requested"):
        op.create_index(
            f"ix_knowledge_base_reindex_job_{column}",
            "knowledge_base_reindex_job",
            [column],
        )

    op.add_column("chunk", sa.Column("version_id", sa.String(), nullable=True))
    op.add_column(
        "chunk",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "chunk",
        sa.Column("injection_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    bind = op.get_bind()
    documents = bind.execute(
        sa.text(
            "SELECT d.id, d.tenant_id, d.kb_id, d.created_by_user_id, d.name, "
            "d.source_type, d.source, d.mime, d.size_bytes, d.status, d.error, "
            "d.chunk_count, d.stored_path, d.created_at, d.updated_at, "
            "k.embedding_provider, k.embedding_model, k.embedding_dim "
            "FROM document d JOIN knowledge_base k ON k.id = d.kb_id"
        )
    ).mappings()
    for document in documents:
        version_id = uuid4().hex
        active = document["status"] == "done"
        bind.execute(
            sa.text(
                "INSERT INTO document_version ("
                "id, tenant_id, kb_id, document_id, created_by_user_id, version_number, "
                "name, source_type, source, mime, size_bytes, stored_path, content_hash, "
                "status, error, progress, chunk_count, embedding_provider, embedding_model, "
                "embedding_dim, is_active, created_at, updated_at"
                ") VALUES ("
                ":id, :tenant_id, :kb_id, :document_id, :created_by_user_id, 1, :name, "
                ":source_type, :source, :mime, :size_bytes, :stored_path, '', :status, :error, "
                ":progress, :chunk_count, :embedding_provider, :embedding_model, :embedding_dim, "
                ":is_active, :created_at, :updated_at)"
            ),
            {
                **dict(document),
                "id": version_id,
                "document_id": document["id"],
                "progress": 100 if active else 0,
                "is_active": active,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE document SET active_version_id = :active_version_id, version = :version, "
                "latest_version = 1, progress = :progress, consistency_status = :consistency "
                "WHERE id = :document_id"
            ),
            {
                "active_version_id": version_id if active else "",
                "version": 1 if active else 0,
                "progress": 100 if active else 0,
                "consistency": "consistent" if active else "pending",
                "document_id": document["id"],
            },
        )
        bind.execute(
            sa.text(
                "UPDATE chunk SET version_id = :version_id, is_active = :active "
                "WHERE document_id = :document_id"
            ),
            {
                "version_id": version_id,
                "active": active,
                "document_id": document["id"],
            },
        )

    with op.batch_alter_table("chunk", recreate="auto") as batch:
        batch.alter_column("version_id", existing_type=sa.String(), nullable=False)
        batch.create_foreign_key(
            "fk_chunk_version_id_document_version",
            "document_version",
            ["version_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_chunk_document_version_index",
            ["document_id", "version_id", "chunk_index"],
        )
    op.create_index("ix_chunk_version_id", "chunk", ["version_id"])
    op.create_index("ix_chunk_is_active", "chunk", ["is_active"])
    op.create_index("ix_chunk_injection_risk", "chunk", ["injection_risk"])


def downgrade() -> None:
    op.drop_index("ix_chunk_injection_risk", table_name="chunk")
    op.drop_index("ix_chunk_is_active", table_name="chunk")
    op.drop_index("ix_chunk_version_id", table_name="chunk")
    with op.batch_alter_table("chunk", recreate="auto") as batch:
        batch.drop_constraint("uq_chunk_document_version_index", type_="unique")
        batch.drop_constraint("fk_chunk_version_id_document_version", type_="foreignkey")
        batch.drop_column("injection_risk")
        batch.drop_column("is_active")
        batch.drop_column("version_id")

    for column in ("cancel_requested", "stage", "status", "kb_id", "tenant_id"):
        op.drop_index(f"ix_knowledge_base_reindex_job_{column}", table_name="knowledge_base_reindex_job")
    op.drop_table("knowledge_base_reindex_job")

    for column in (
        "idempotency_key",
        "cancel_requested",
        "stage",
        "status",
        "kind",
        "version_id",
        "document_id",
        "kb_id",
        "tenant_id",
    ):
        op.drop_index(f"ix_ingestion_job_{column}", table_name="ingestion_job")
    op.drop_table("ingestion_job")

    for column in (
        "is_active",
        "status",
        "content_hash",
        "version_number",
        "created_by_user_id",
        "document_id",
        "kb_id",
        "tenant_id",
    ):
        op.drop_index(f"ix_document_version_{column}", table_name="document_version")
    op.drop_table("document_version")

    op.drop_index("ix_document_consistency_status", table_name="document")
    op.drop_index("ix_document_cancel_requested", table_name="document")
    op.drop_index("ix_document_content_hash", table_name="document")
    op.drop_index("ix_document_active_version_id", table_name="document")
    for column in (
        "cleanup_error",
        "consistency_status",
        "cancel_requested",
        "retry_count",
        "progress",
        "content_hash",
        "latest_version",
        "version",
        "active_version_id",
    ):
        op.drop_column("document", column)

    op.drop_index("ix_knowledge_base_consistency_status", table_name="knowledge_base")
    op.drop_index("ix_knowledge_base_reindex_status", table_name="knowledge_base")
    op.drop_index("ix_knowledge_base_vector_collection", table_name="knowledge_base")
    for column in (
        "consistency_status",
        "reindex_error",
        "reindex_progress",
        "reindex_status",
        "vector_revision",
        "vector_collection",
    ):
        op.drop_column("knowledge_base", column)

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("app_role", recreate="always") as batch:
            batch.drop_constraint("uq_app_role_tenant_name", type_="unique")
        with op.batch_alter_table("app_user", recreate="always") as batch:
            batch.drop_constraint("uq_app_user_tenant_email", type_="unique")
            batch.create_unique_constraint("app_user_email_key", ["email"])
    else:
        op.drop_constraint("uq_app_role_tenant_name", "app_role", type_="unique")
        op.drop_constraint("uq_app_user_tenant_email", "app_user", type_="unique")
        op.create_unique_constraint("app_user_email_key", "app_user", ["email"])
