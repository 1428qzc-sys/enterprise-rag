"""数据库引擎与会话管理。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings


def _make_engine():
    url = settings.database_url
    connect_args = {}
    if settings.is_sqlite:
        # SQLite 需允许跨线程共享连接，并确保目录存在
        connect_args = {"check_same_thread": False}
        db_path = url.replace("sqlite:///", "").replace("sqlite://", "")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, echo=False, connect_args=connect_args, pool_pre_ping=True)


engine = _make_engine()


def init_db() -> None:
    """建表并确保数据目录存在。"""
    # 导入以注册所有表模型
    from . import models  # noqa: F401
    from .security import ensure_bootstrap_identity

    os.makedirs(settings.upload_dir, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _run_compat_migrations()
    with Session(engine) as session:
        ensure_bootstrap_identity(session)
    _backfill_legacy_tenant_ids()


def _run_compat_migrations() -> None:
    """为早期演示库追加生产化所需字段。

    项目尚未引入 Alembic；这里仅做向后兼容的最小列追加与默认租户回填，
    便于旧本地 Docker volume 不删除也能继续运行。正式生产迁移应迁入 Alembic。
    """

    inspector = inspect(engine)

    def has_table(table: str) -> bool:
        return table in inspector.get_table_names()

    def has_column(table: str, column: str) -> bool:
        if not has_table(table):
            return False
        return column in {c["name"] for c in inspector.get_columns(table)}

    def add_text_column(table: str, column: str) -> None:
        if has_table(table) and not has_column(table, column):
            with engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR NOT NULL DEFAULT ''")
                )

    for table in ("knowledge_base", "document", "chunk", "conversation"):
        add_text_column(table, "tenant_id")
    for table in ("knowledge_base", "document", "conversation"):
        add_text_column(table, "created_by_user_id")

    _backfill_legacy_tenant_ids()


def _backfill_legacy_tenant_ids() -> None:
    """将早期无 tenant_id 的业务数据挂到默认租户。"""

    inspector = inspect(engine)

    def has_table(table: str) -> bool:
        return table in inspector.get_table_names()

    def has_column(table: str, column: str) -> bool:
        if not has_table(table):
            return False
        return column in {c["name"] for c in inspector.get_columns(table)}

    with engine.begin() as conn:
        tenant_row = conn.execute(
            text("SELECT id FROM tenant WHERE slug = :slug"),
            {"slug": settings.bootstrap_tenant_slug},
        ).first()
        if tenant_row is not None:
            tenant_id = tenant_row[0]
            for table in ("knowledge_base",):
                if has_table(table) and has_column(table, "tenant_id"):
                    conn.execute(
                        text(
                            f"UPDATE {table} SET tenant_id = :tenant_id "
                            "WHERE tenant_id IS NULL OR tenant_id = ''"
                        ),
                        {"tenant_id": tenant_id},
                    )
            if has_table("document") and has_column("document", "tenant_id"):
                conn.execute(
                    text(
                        "UPDATE document SET tenant_id = ("
                        "SELECT tenant_id FROM knowledge_base WHERE knowledge_base.id = document.kb_id"
                        ") WHERE tenant_id IS NULL OR tenant_id = ''"
                    )
                )
            if has_table("chunk") and has_column("chunk", "tenant_id"):
                conn.execute(
                    text(
                        "UPDATE chunk SET tenant_id = ("
                        "SELECT tenant_id FROM knowledge_base WHERE knowledge_base.id = chunk.kb_id"
                        ") WHERE tenant_id IS NULL OR tenant_id = ''"
                    )
                )
            if has_table("conversation") and has_column("conversation", "tenant_id"):
                conn.execute(
                    text(
                        "UPDATE conversation SET tenant_id = ("
                        "SELECT tenant_id FROM knowledge_base WHERE knowledge_base.id = conversation.kb_id"
                        ") WHERE tenant_id IS NULL OR tenant_id = ''"
                    )
                )


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：请求级数据库会话。"""
    with Session(engine) as session:
        yield session
