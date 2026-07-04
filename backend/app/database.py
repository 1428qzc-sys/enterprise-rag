"""数据库引擎与会话管理。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

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

    os.makedirs(settings.upload_dir, exist_ok=True)
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：请求级数据库会话。"""
    with Session(engine) as session:
        yield session
