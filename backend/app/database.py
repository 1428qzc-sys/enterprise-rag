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
    engine_kwargs = {"echo": False, "pool_pre_ping": True}
    if settings.is_sqlite:
        # SQLite 需允许跨线程共享连接，并确保目录存在
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        db_path = url.replace("sqlite:///", "").replace("sqlite://", "")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    else:
        engine_kwargs.update(
            {
                "pool_size": settings.db_pool_size,
                "max_overflow": settings.db_max_overflow,
                "pool_timeout": settings.db_pool_timeout_seconds,
            }
        )
    return create_engine(url, **engine_kwargs)


engine = _make_engine()


def init_db() -> None:
    """初始化运行目录与引导身份；Docker/生产架构必须先由 Alembic 建立。"""
    # 导入以注册所有表模型
    from . import models  # noqa: F401
    from .security import ensure_bootstrap_identity

    os.makedirs(settings.upload_dir, exist_ok=True)
    if settings.database_auto_create:
        SQLModel.metadata.create_all(engine)
        _run_compat_migrations()
    else:
        tables = set(inspect(engine).get_table_names())
        required = {"alembic_version", "tenant", "app_user", "knowledge_base"}
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(
                "数据库架构尚未迁移；请先执行 alembic upgrade head。缺少："
                + ", ".join(missing)
            )
    with Session(engine) as session:
        ensure_bootstrap_identity(session)
    if settings.database_auto_create:
        _backfill_legacy_tenant_ids()


def _run_compat_migrations() -> None:
    """仅供原生 SQLite 演示库向后兼容；Docker/生产使用 Alembic。"""

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
