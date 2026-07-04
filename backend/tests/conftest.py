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
        "HISTORY_TURNS": "4",
        "CHUNK_SIZE": "200",
        "CHUNK_OVERLAP": "40",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
