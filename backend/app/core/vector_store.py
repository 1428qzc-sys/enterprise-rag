"""向量库抽象。

- ``qdrant``：生产级向量库，每个知识库一个 collection（物理隔离，删库即删 collection）
- ``memory``：进程内 numpy 余弦，pickle 落盘持久化，零外部依赖，适合本地体验与测试

统一以 ``chunk_id`` 作为 payload 主键，正文仍以关系库为准，向量库只存向量与轻量元数据。
"""

from __future__ import annotations

import os
import pickle
import threading
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
    def delete_collection(self, name: str) -> None: ...
    def count(self, name: str) -> int: ...


# ==================== 内存后端 ====================
class MemoryVectorStore(BaseVectorStore):
    def __init__(self, persist_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._store: Dict[str, Dict[str, dict]] = {}  # collection -> {id -> {vector, payload}}
        self._persist_path = persist_path
        self._load()

    def _load(self) -> None:
        if self._persist_path and os.path.exists(self._persist_path):
            try:
                with open(self._persist_path, "rb") as f:
                    self._store = pickle.load(f)
            except Exception:
                self._store = {}

    def _save(self) -> None:
        if not self._persist_path:
            return
        Path(self._persist_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = self._persist_path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(self._store, f)
        os.replace(tmp, self._persist_path)

    def ensure_collection(self, name: str, dim: int) -> None:
        with self._lock:
            self._store.setdefault(name, {})

    def upsert(self, name: str, points: List[VectorPoint]) -> None:
        with self._lock:
            col = self._store.setdefault(name, {})
            for p in points:
                col[p.id] = {"vector": np.asarray(p.vector, dtype=np.float32), "payload": p.payload}
            self._save()

    def search(self, name: str, query_vector: List[float], top_k: int) -> List[ScoredPoint]:
        with self._lock:
            col = self._store.get(name, {})
            if not col:
                return []
            q = np.asarray(query_vector, dtype=np.float32)
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

    def delete_collection(self, name: str) -> None:
        with self._lock:
            self._store.pop(name, None)
            self._save()

    def count(self, name: str) -> int:
        with self._lock:
            return len(self._store.get(name, {}))


# ==================== Qdrant 后端 ====================
def _to_point_id(raw: str) -> str:
    """把 chunk.id（uuid hex）转成 Qdrant 认可的规范 UUID 字符串。"""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw)))


class QdrantVectorStore(BaseVectorStore):
    def __init__(self, url: str, api_key: str = ""):
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url, api_key=api_key or None, timeout=60)

    def ensure_collection(self, name: str, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        if not self._client.collection_exists(name):
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def upsert(self, name: str, points: List[VectorPoint]) -> None:
        from qdrant_client.models import PointStruct

        payloads = [
            PointStruct(id=_to_point_id(p.id), vector=p.vector, payload=p.payload)
            for p in points
        ]
        self._client.upsert(collection_name=name, points=payloads)

    def search(self, name: str, query_vector: List[float], top_k: int) -> List[ScoredPoint]:
        hits = self._client.query_points(
            collection_name=name, query=query_vector, limit=top_k, with_payload=True
        ).points
        return [ScoredPoint(id=str(h.id), score=float(h.score), payload=h.payload or {}) for h in hits]

    def delete_document(self, name: str, document_id: str) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        self._client.delete(
            collection_name=name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
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


def collection_name(kb_id: str) -> str:
    return f"{settings.qdrant_collection_prefix}{kb_id}"
