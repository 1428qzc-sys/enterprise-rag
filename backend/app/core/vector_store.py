"""向量库抽象。

- ``qdrant``：外部持久化向量库，每个知识库一个 collection（物理隔离，删库即删 collection）
- ``memory``：进程内 numpy 余弦，pickle 落盘持久化，零外部依赖，适合本地体验与测试

统一以 ``chunk_id`` 作为 payload 主键，正文仍以关系库为准，向量库只存向量与轻量元数据。
"""

from __future__ import annotations

import os
import pickle
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..config import settings


@dataclass
class VectorPoint:
    id: str
    vector: List[float]
    payload: Dict


@dataclass
class ScoredPoint:
    id: str
    score: float
    payload: Dict = field(default_factory=dict)


class BaseVectorStore:
    def ensure_collection(self, name: str, dim: int) -> None: ...
    def upsert(self, name: str, points: List[VectorPoint]) -> None: ...
    def search(self, name: str, query_vector: List[float], top_k: int) -> List[ScoredPoint]: ...
    def delete_document(self, name: str, document_id: str) -> None: ...
    def delete_version(self, name: str, version_id: str) -> None: ...
    def delete_collection(self, name: str) -> None: ...
    def count(self, name: str) -> int: ...
    def count_document(self, name: str, document_id: str) -> int: ...
    def count_version(self, name: str, version_id: str) -> int: ...
    def collection_dimension(self, name: str) -> Optional[int]: ...
    def health(self) -> bool: ...


# ==================== 内存后端 ====================
class MemoryVectorStore(BaseVectorStore):
    def __init__(self, persist_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._store: Dict[str, Dict[str, dict]] = {}  # collection -> {id -> {vector, payload}}
        self._dims: Dict[str, int] = {}
        self._persist_path = persist_path
        self._load()

    def _load(self) -> None:
        if self._persist_path and os.path.exists(self._persist_path):
            try:
                with open(self._persist_path, "rb") as f:
                    loaded = pickle.load(f)
                if isinstance(loaded, dict) and loaded.get("format") == 2:
                    self._store = loaded.get("store", {})
                    self._dims = {str(k): int(v) for k, v in loaded.get("dims", {}).items()}
                elif isinstance(loaded, dict):
                    self._store = loaded
                    for name, points in self._store.items():
                        first = next(iter(points.values()), None)
                        if first is not None:
                            self._dims[name] = len(first.get("vector", []))
            except Exception:
                self._store = {}
                self._dims = {}

    def _save(self) -> None:
        if not self._persist_path:
            return
        Path(self._persist_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = self._persist_path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump({"format": 2, "dims": self._dims, "store": self._store}, f)
        os.replace(tmp, self._persist_path)

    def ensure_collection(self, name: str, dim: int) -> None:
        with self._lock:
            existing = self._dims.get(name)
            if existing is not None and existing != dim:
                raise ValueError(
                    f"vector collection dimension mismatch: collection={existing}, requested={dim}"
                )
            self._store.setdefault(name, {})
            self._dims[name] = dim
            self._save()

    def upsert(self, name: str, points: List[VectorPoint]) -> None:
        with self._lock:
            dim = self._dims.get(name)
            if dim is None:
                if not points:
                    return
                dim = len(points[0].vector)
                self._dims[name] = dim
            col = self._store.setdefault(name, {})
            for p in points:
                if len(p.vector) != dim:
                    raise ValueError(
                        f"vector dimension mismatch: collection={dim}, point={len(p.vector)}"
                    )
                col[p.id] = {"vector": np.asarray(p.vector, dtype=np.float32), "payload": p.payload}
            self._save()

    def search(self, name: str, query_vector: List[float], top_k: int) -> List[ScoredPoint]:
        with self._lock:
            col = self._store.get(name, {})
            if not col:
                return []
            q = np.asarray(query_vector, dtype=np.float32)
            dim = self._dims.get(name)
            if dim is not None and len(q) != dim:
                raise ValueError(
                    f"query dimension mismatch: collection={dim}, query={len(q)}"
                )
            qn = np.linalg.norm(q) or 1.0
            scored: List[ScoredPoint] = []
            for pid, item in col.items():
                v = item["vector"]
                vn = np.linalg.norm(v) or 1.0
                score = float(np.dot(q, v) / (qn * vn))
                scored.append(ScoredPoint(id=pid, score=score, payload=item["payload"]))
            scored.sort(key=lambda s: s.score, reverse=True)
            return scored[:top_k]

    def delete_document(self, name: str, document_id: str) -> None:
        with self._lock:
            col = self._store.get(name, {})
            drop = [pid for pid, it in col.items() if it["payload"].get("document_id") == document_id]
            for pid in drop:
                del col[pid]
            self._save()

    def delete_version(self, name: str, version_id: str) -> None:
        with self._lock:
            col = self._store.get(name, {})
            drop = [pid for pid, it in col.items() if it["payload"].get("version_id") == version_id]
            for pid in drop:
                del col[pid]
            self._save()

    def delete_collection(self, name: str) -> None:
        with self._lock:
            self._store.pop(name, None)
            self._dims.pop(name, None)
            self._save()

    def count(self, name: str) -> int:
        with self._lock:
            return len(self._store.get(name, {}))

    def count_document(self, name: str, document_id: str) -> int:
        with self._lock:
            return sum(
                1
                for item in self._store.get(name, {}).values()
                if item["payload"].get("document_id") == document_id
            )

    def count_version(self, name: str, version_id: str) -> int:
        with self._lock:
            return sum(
                1
                for item in self._store.get(name, {}).values()
                if item["payload"].get("version_id") == version_id
            )

    def collection_dimension(self, name: str) -> Optional[int]:
        with self._lock:
            return self._dims.get(name)

    def health(self) -> bool:
        return True


# ==================== Qdrant 后端 ====================
def _to_point_id(raw: str) -> str:
    """把 chunk.id（uuid hex）转成 Qdrant 认可的规范 UUID 字符串。"""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw)))


class QdrantVectorStore(BaseVectorStore):
    # Qdrant may briefly expose a collection through ``collection_exists`` before
    # ``get_collection`` is readable.  Serialize first creation in this process;
    # the readiness retry below also covers a competing creation in another process.
    _collection_init_lock = threading.RLock()

    def __init__(self, url: str, api_key: str = ""):
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url, api_key=api_key or None, timeout=60)

    def _wait_for_collection_dimension(self, name: str, attempts: int = 8) -> Optional[int]:
        last_error: Optional[Exception] = None
        delay = 0.05
        for attempt in range(attempts):
            try:
                dimension = self.collection_dimension(name)
                if dimension is not None:
                    return dimension
            except Exception as exc:
                last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay)
                delay = min(delay * 2, 0.4)
        if last_error is not None:
            raise last_error
        return None

    def ensure_collection(self, name: str, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        with self._collection_init_lock:
            creation_error: Optional[Exception] = None
            if not self._client.collection_exists(name):
                try:
                    self._client.create_collection(
                        collection_name=name,
                        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                    )
                except Exception as exc:
                    creation_error = exc
                    # Another process may win the check-then-create race.  A
                    # genuinely missing collection must still surface the error.
                    if not self._client.collection_exists(name):
                        raise
            existing = self._wait_for_collection_dimension(name)
            if existing is None:
                if creation_error is not None:
                    raise creation_error
                raise RuntimeError(f"vector collection did not become ready: {name}")
            if existing != dim:
                raise ValueError(
                    f"vector collection dimension mismatch: collection={existing}, requested={dim}"
                )

    def upsert(self, name: str, points: List[VectorPoint]) -> None:
        from qdrant_client.models import PointStruct

        dim = self.collection_dimension(name)
        if dim is None:
            raise ValueError(f"vector collection does not exist: {name}")
        for point in points:
            if len(point.vector) != dim:
                raise ValueError(
                    f"vector dimension mismatch: collection={dim}, point={len(point.vector)}"
                )

        payloads = [
            PointStruct(id=_to_point_id(p.id), vector=p.vector, payload=p.payload)
            for p in points
        ]
        self._client.upsert(collection_name=name, points=payloads)

    def search(self, name: str, query_vector: List[float], top_k: int) -> List[ScoredPoint]:
        dim = self.collection_dimension(name)
        if dim is not None and len(query_vector) != dim:
            raise ValueError(
                f"query dimension mismatch: collection={dim}, query={len(query_vector)}"
            )
        hits = self._client.query_points(
            collection_name=name, query=query_vector, limit=top_k, with_payload=True
        ).points
        return [ScoredPoint(id=str(h.id), score=float(h.score), payload=h.payload or {}) for h in hits]

    def delete_document(self, name: str, document_id: str) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        if not self._client.collection_exists(name):
            return
        self._client.delete(
            collection_name=name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
                )
            ),
        )

    def delete_version(self, name: str, version_id: str) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        if not self._client.collection_exists(name):
            return
        self._client.delete(
            collection_name=name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="version_id", match=MatchValue(value=version_id))]
                )
            ),
        )

    def delete_collection(self, name: str) -> None:
        if self._client.collection_exists(name):
            self._client.delete_collection(name)

    def count(self, name: str) -> int:
        if not self._client.collection_exists(name):
            return 0
        return self._client.count(name).count

    def _count_filtered(self, name: str, field: str, value: str) -> int:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if not self._client.collection_exists(name):
            return 0
        return int(
            self._client.count(
                collection_name=name,
                count_filter=Filter(
                    must=[FieldCondition(key=field, match=MatchValue(value=value))]
                ),
                exact=True,
            ).count
        )

    def count_document(self, name: str, document_id: str) -> int:
        return self._count_filtered(name, "document_id", document_id)

    def count_version(self, name: str, version_id: str) -> int:
        return self._count_filtered(name, "version_id", version_id)

    def collection_dimension(self, name: str) -> Optional[int]:
        if not self._client.collection_exists(name):
            return None
        vectors = self._client.get_collection(name).config.params.vectors
        if hasattr(vectors, "size"):
            return int(vectors.size)
        if isinstance(vectors, dict) and vectors:
            first = next(iter(vectors.values()))
            if hasattr(first, "size"):
                return int(first.size)
            if isinstance(first, dict) and "size" in first:
                return int(first["size"])
        return None

    def health(self) -> bool:
        self._client.get_collections()
        return True


# ==================== 工厂（单例） ====================
_INSTANCE: Optional[BaseVectorStore] = None


def get_vector_store() -> BaseVectorStore:
    global _INSTANCE
    if _INSTANCE is None:
        if settings.vector_backend == "qdrant":
            _INSTANCE = QdrantVectorStore(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        else:
            persist = os.path.join(os.path.dirname(settings.upload_dir.rstrip("/")), "memory_vectors.pkl")
            _INSTANCE = MemoryVectorStore(persist_path=persist)
    return _INSTANCE


def collection_name(kb_id: str, revision: int = 1) -> str:
    suffix = "" if revision <= 1 else f"_v{revision}"
    return f"{settings.qdrant_collection_prefix}{kb_id}{suffix}"


def collection_for_kb(kb) -> str:
    return kb.vector_collection or collection_name(kb.id, getattr(kb, "vector_revision", 1))
