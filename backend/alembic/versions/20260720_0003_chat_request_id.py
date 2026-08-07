"""add idempotent chat request id

Revision ID: 20260720_0003
Revises: 20260719_0002
"""

from alembic import op
import sqlalchemy as sa

revision = "20260720_0003"
down_revision = "20260719_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("message") as batch_op:
        batch_op.add_column(
            sa.Column("request_id", sa.String(length=64), nullable=False, server_default="")
        )
        batch_op.create_index("ix_message_request_id", ["request_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("message") as batch_op:
        batch_op.drop_index("ix_message_request_id")
        batch_op.drop_column("request_id")
