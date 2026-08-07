"""向量存储维度、版本计数与持久化契约。"""

from __future__ import annotations

import pickle
import threading
import time
from types import SimpleNamespace

import pytest

from app.core.vector_store import MemoryVectorStore, QdrantVectorStore, VectorPoint


def _point(point_id: str, vector: list[float], document_id: str, version_id: str) -> VectorPoint:
    return VectorPoint(
        id=point_id,
        vector=vector,
        payload={"chunk_id": point_id, "document_id": document_id, "version_id": version_id},
    )


def test_memory_store_rejects_collection_point_and_query_dimension_mismatch():
    store = MemoryVectorStore()
    store.ensure_collection("kb", 3)
    with pytest.raises(ValueError, match="collection dimension mismatch"):
        store.ensure_collection("kb", 4)
    with pytest.raises(ValueError, match="vector dimension mismatch"):
        store.upsert("kb", [_point("bad", [1.0, 0.0], "doc", "v1")])
    store.upsert("kb", [_point("ok", [1.0, 0.0, 0.0], "doc", "v1")])
    with pytest.raises(ValueError, match="query dimension mismatch"):
        store.search("kb", [1.0, 0.0], 5)


def test_memory_store_counts_and_deletes_versions_without_touching_active_version():
    store = MemoryVectorStore()
    store.ensure_collection("kb", 2)
    store.upsert(
        "kb",
        [
            _point("a", [1.0, 0.0], "doc", "v1"),
            _point("b", [0.0, 1.0], "doc", "v1"),
            _point("c", [1.0, 1.0], "doc", "v2"),
        ],
    )
    assert store.count("kb") == 3
    assert store.count_document("kb", "doc") == 3
    assert store.count_version("kb", "v1") == 2
    store.delete_version("kb", "v1")
    assert store.count_document("kb", "doc") == 1
    assert store.count_version("kb", "v2") == 1


def test_memory_store_persists_v2_and_loads_legacy_format(tmp_path):
    path = tmp_path / "vectors.pkl"
    store = MemoryVectorStore(str(path))
    store.ensure_collection("kb", 2)
    store.upsert("kb", [_point("a", [1.0, 0.0], "doc", "v1")])
    loaded = MemoryVectorStore(str(path))
    assert loaded.collection_dimension("kb") == 2
    assert loaded.count_version("kb", "v1") == 1

    legacy_path = tmp_path / "legacy.pkl"
    with legacy_path.open("wb") as output:
        pickle.dump(
            {
                "legacy": {
                    "a": {
                        "vector": [1.0, 0.0, 0.0],
                        "payload": {"document_id": "doc"},
                    }
                }
            },
            output,
        )
    legacy = MemoryVectorStore(str(legacy_path))
    assert legacy.collection_dimension("legacy") == 3
    assert legacy.count_document("legacy", "doc") == 1


def test_qdrant_ensure_collection_accepts_a_concurrent_create_with_same_dimension():
    class ConcurrentCreateClient:
        def __init__(self):
            self.exists_checks = 0

        def collection_exists(self, _name):
            self.exists_checks += 1
            return self.exists_checks > 1

        def create_collection(self, **_kwargs):
            raise RuntimeError("collection already exists")

        def get_collection(self, _name):
            vectors = SimpleNamespace(size=3)
            params = SimpleNamespace(vectors=vectors)
            return SimpleNamespace(config=SimpleNamespace(params=params))

    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = ConcurrentCreateClient()

    store.ensure_collection("kb", 3)


def test_qdrant_ensure_collection_serializes_local_first_creation():
    class SlowCreateClient:
        def __init__(self):
            self.exists = False
            self.create_calls = 0
            self.state_lock = threading.Lock()

        def collection_exists(self, _name):
            with self.state_lock:
                return self.exists

        def create_collection(self, **_kwargs):
            with self.state_lock:
                self.create_calls += 1
            time.sleep(0.05)
            with self.state_lock:
                self.exists = True

        def get_collection(self, _name):
            vectors = SimpleNamespace(size=3)
            params = SimpleNamespace(vectors=vectors)
            return SimpleNamespace(config=SimpleNamespace(params=params))

    client = SlowCreateClient()
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = client
    start = threading.Barrier(3)
    errors = []

    def ensure():
        start.wait()
        try:
            store.ensure_collection("kb", 3)
        except Exception as exc:  # pragma: no cover - assertion reports worker failures
            errors.append(exc)

    workers = [threading.Thread(target=ensure) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.wait()
    for worker in workers:
        worker.join(timeout=2)

    assert errors == []
    assert all(not worker.is_alive() for worker in workers)
    assert client.create_calls == 1


def test_qdrant_ensure_collection_retries_transient_metadata_read():
    class InitiallyUnreadableClient:
        def __init__(self):
            self.info_calls = 0

        def collection_exists(self, _name):
            return True

        def get_collection(self, _name):
            self.info_calls += 1
            if self.info_calls == 1:
                raise RuntimeError("collection is still initializing")
            vectors = SimpleNamespace(size=3)
            params = SimpleNamespace(vectors=vectors)
            return SimpleNamespace(config=SimpleNamespace(params=params))

    client = InitiallyUnreadableClient()
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = client

    store.ensure_collection("kb", 3)

    assert client.info_calls == 2


def test_qdrant_ensure_collection_does_not_hide_a_real_create_failure():
    class MissingCollectionClient:
        def collection_exists(self, _name):
            return False

        def create_collection(self, **_kwargs):
            raise RuntimeError("qdrant unavailable")

    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = MissingCollectionClient()

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        store.ensure_collection("kb", 3)
