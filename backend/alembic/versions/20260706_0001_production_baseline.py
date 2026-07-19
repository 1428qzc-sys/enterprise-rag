"""production baseline schema

Revision ID: 20260706_0001
Revises:
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260706_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tenant_name", "tenant", ["name"])
    op.create_index("ix_tenant_slug", "tenant", ["slug"])
    op.create_index("ix_tenant_is_active", "tenant", ["is_active"])

    op.create_table(
        "app_user",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_app_user_tenant_id", "app_user", ["tenant_id"])
    op.create_index("ix_app_user_email", "app_user", ["email"])
    op.create_index("ix_app_user_is_active", "app_user", ["is_active"])
    op.create_index("ix_app_user_is_superuser", "app_user", ["is_superuser"])

    op.create_table(
        "app_role",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_app_role_tenant_id", "app_role", ["tenant_id"])
    op.create_index("ix_app_role_name", "app_role", ["name"])

    op.create_table(
        "app_permission",
        sa.Column("code", sa.String(), primary_key=True),
        sa.Column("description", sa.String(), nullable=False),
    )

    op.create_table(
        "app_user_role",
        sa.Column("user_id", sa.String(), sa.ForeignKey("app_user.id"), primary_key=True),
        sa.Column("role_id", sa.String(), sa.ForeignKey("app_role.id"), primary_key=True),
    )

    op.create_table(
        "app_role_permission",
        sa.Column("role_id", sa.String(), sa.ForeignKey("app_role.id"), primary_key=True),
        sa.Column("permission_code", sa.String(), sa.ForeignKey("app_permission.code"), primary_key=True),
    )

    op.create_table(
        "knowledge_base",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("embedding_provider", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("vector_backend", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_knowledge_base_tenant_id", "knowledge_base", ["tenant_id"])
    op.create_index("ix_knowledge_base_created_by_user_id", "knowledge_base", ["created_by_user_id"])
    op.create_index("ix_knowledge_base_name", "knowledge_base", ["name"])

    op.create_table(
        "document",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_document_tenant_id", "document", ["tenant_id"])
    op.create_index("ix_document_created_by_user_id", "document", ["created_by_user_id"])
    op.create_index("ix_document_kb_id", "document", ["kb_id"])
    op.create_index("ix_document_status", "document", ["status"])

    op.create_table(
        "chunk",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id"), nullable=False),
        sa.Column("document_id", sa.String(), sa.ForeignKey("document.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("vector_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chunk_tenant_id", "chunk", ["tenant_id"])
    op.create_index("ix_chunk_kb_id", "chunk", ["kb_id"])
    op.create_index("ix_chunk_document_id", "chunk", ["document_id"])

    op.create_table(
        "conversation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversation_tenant_id", "conversation", ["tenant_id"])
    op.create_index("ix_conversation_created_by_user_id", "conversation", ["created_by_user_id"])
    op.create_index("ix_conversation_kb_id", "conversation", ["kb_id"])

    op.create_table(
        "message",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("conversation.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_message_conversation_id", "message", ["conversation_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_resource_type", "audit_log", ["resource_type"])
    op.create_index("ix_audit_log_resource_id", "audit_log", ["resource_id"])
    op.create_index("ix_audit_log_outcome", "audit_log", ["outcome"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("message")
    op.drop_table("conversation")
    op.drop_table("chunk")
    op.drop_table("document")
    op.drop_table("knowledge_base")
    op.drop_table("app_role_permission")
    op.drop_table("app_user_role")
    op.drop_table("app_permission")
    op.drop_table("app_role")
    op.drop_table("app_user")
    op.drop_table("tenant")
