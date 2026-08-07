"""Embedding 提供方抽象。

支持三种 provider：
- ``openai``：任意 OpenAI 兼容 /embeddings 端点
- ``ollama``：本地 Ollama（/api/embed）
- ``fake``：确定性词袋哈希向量，无需外部服务，供测试与离线演示

每个知识库会快照自己的 provider / model / dim，换库不串味。
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from typing import List, Optional

import httpx

from ..config import settings

_LATIN_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CHINESE_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def _hash_tokens(text: str) -> List[str]:
    normalized = text.lower()
    tokens = _LATIN_TOKEN_RE.findall(normalized)
    for run in _CHINESE_RUN_RE.findall(normalized):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        if len(run) >= 3:
            tokens.extend(run[index : index + 3] for index in range(len(run) - 2))
    return tokens


class BaseEmbeddings(ABC):
    def __init__(self, model: str, dim: int):
        self.model = model
        self.dim = dim

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        ...

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


class OpenAIEmbeddings(BaseEmbeddings):
    """OpenAI 兼容 /embeddings。"""

    def __init__(self, model: str, dim: int, base_url: str, api_key: str, batch_size: int = 32):
        super().__init__(model, dim)
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._batch = batch_size

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), self._batch):
            batch = [t if t.strip() else " " for t in texts[i : i + self._batch]]
            resp = self._client.embeddings.create(model=self.model, input=batch)
            out.extend([d.embedding for d in resp.data])
        return out


class OllamaEmbeddings(BaseEmbeddings):
    """本地 Ollama /api/embed。"""

    def __init__(self, model: str, dim: int, base_url: str):
        super().__init__(model, dim)
        self._base = base_url.rstrip("/")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        payload = {"model": self.model, "input": texts}
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(f"{self._base}/api/embed", json=payload)
            resp.raise_for_status()
            data = resp.json()
        embeddings = data.get("embeddings")
        if embeddings is None and "embedding" in data:  # 兼容旧接口
            embeddings = [data["embedding"]]
        return embeddings


class FakeEmbeddings(BaseEmbeddings):
    """确定性词袋哈希向量：同词共享维度，余弦相似度可反映文本重合度。"""

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _hash_tokens(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def make_embeddings(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    dim: Optional[int] = None,
) -> BaseEmbeddings:
    """按配置构造 Embedding 提供方。参数缺省时取全局配置。"""
    provider = provider or settings.embedding_provider
    model = model or settings.embedding_model
    dim = dim or settings.embedding_dim

    if provider == "openai":
        return OpenAIEmbeddings(
            model=model,
            dim=dim,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            batch_size=settings.embedding_batch_size,
        )
    if provider == "ollama":
        return OllamaEmbeddings(model=model, dim=dim, base_url=settings.ollama_base_url)
    if provider == "fake":
        return FakeEmbeddings(model=model, dim=dim)
    raise ValueError(f"未知 embedding provider: {provider}")
