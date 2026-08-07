"""RRF 融合与可验证的可选重排器。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple

from ..config import settings

_LATIN_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CHINESE_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def reciprocal_rank_fusion(rankings: List[List[str]], k: int = 60) -> Dict[str, float]:
    """将多路排名融合为稳定的倒数秩分数。"""
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def lexical_tokens(text: str) -> set[str]:
    """提取英文词、中文单字和相邻双字，供零依赖重排使用。"""
    normalized = text.lower()
    tokens = set(_LATIN_TOKEN_RE.findall(normalized))
    for run in _CHINESE_RUN_RE.findall(normalized):
        tokens.update(run)
        tokens.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return {token for token in tokens if token}


def lexical_similarity(query: str, document: str) -> float:
    """返回稳定的词项覆盖相似度，供重排与生成证据门槛共用。"""
    query_tokens = lexical_tokens(query)
    document_tokens = lexical_tokens(document)
    overlap = len(query_tokens & document_tokens)
    denominator = math.sqrt(
        max(1, len(query_tokens)) * max(1, len(document_tokens))
    )
    return overlap / denominator


@dataclass
class RerankOutcome:
    ids: List[str]
    scores: Dict[str, float] = field(default_factory=dict)
    applied: bool = False
    provider: str = "none"
    degraded_reason: str = ""


class Reranker(Protocol):
    def rerank_with_scores(
        self, query: str, docs: List[Tuple[str, str]], top_n: int
    ) -> RerankOutcome: ...


class LexicalReranker:
    """确定性词项覆盖率重排器，适合 zero-key 回归与轻量部署。"""

    provider = "lexical"

    def rerank_with_scores(
        self, query: str, docs: List[Tuple[str, str]], top_n: int
    ) -> RerankOutcome:
        if not docs:
            return RerankOutcome(ids=[], applied=True, provider=self.provider)
        scores: Dict[str, float] = {}
        for doc_id, text in docs:
            scores[doc_id] = lexical_similarity(query, text)
        original_rank = {doc_id: index for index, (doc_id, _) in enumerate(docs)}
        ordered = sorted(
            (doc_id for doc_id, _ in docs),
            key=lambda doc_id: (-scores[doc_id], original_rank[doc_id]),
        )
        return RerankOutcome(
            ids=ordered[:top_n],
            scores=scores,
            applied=True,
            provider=self.provider,
        )

    def rerank(self, query: str, docs: List[Tuple[str, str]], top_n: int) -> List[str]:
        return self.rerank_with_scores(query, docs, top_n).ids


class CrossEncoderReranker:
    """惰性加载交叉编码器，并显式报告模型不可用的降级。"""

    provider = "cross_encoder"

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._available: Optional[bool] = None
        self._load_error = ""

    def _ensure(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._model = None
            self._available = False
            self._load_error = type(exc).__name__
        return self._available

    def rerank_with_scores(
        self, query: str, docs: List[Tuple[str, str]], top_n: int
    ) -> RerankOutcome:
        original = [doc_id for doc_id, _ in docs]
        if not docs:
            return RerankOutcome(ids=[], applied=True, provider=self.provider)
        if not self._ensure():
            return RerankOutcome(
                ids=original[:top_n],
                applied=False,
                provider=self.provider,
                degraded_reason=f"model_unavailable:{self._load_error or 'unknown'}",
            )
        pairs = [(query, text) for _, text in docs]
        try:
            raw_scores = self._model.predict(pairs)
            scores = {doc_id: float(raw_scores[index]) for index, doc_id in enumerate(original)}
        except Exception as exc:  # noqa: BLE001
            return RerankOutcome(
                ids=original[:top_n],
                applied=False,
                provider=self.provider,
                degraded_reason=f"prediction_failed:{type(exc).__name__}",
            )
        order = sorted(range(len(docs)), key=lambda index: raw_scores[index], reverse=True)
        return RerankOutcome(
            ids=[docs[index][0] for index in order[:top_n]],
            scores=scores,
            applied=True,
            provider=self.provider,
        )

    def rerank(self, query: str, docs: List[Tuple[str, str]], top_n: int) -> List[str]:
        return self.rerank_with_scores(query, docs, top_n).ids


_RERANKER: Optional[Reranker] = None
_RERANKER_KEY = ""


def get_reranker() -> Optional[Reranker]:
    """按当前配置返回重排器；配置变化时重建实例。"""
    global _RERANKER, _RERANKER_KEY
    if not settings.rerank_enabled or settings.rerank_provider == "none":
        return None
    key = f"{settings.rerank_provider}:{settings.rerank_model}"
    if _RERANKER is None or _RERANKER_KEY != key:
        if settings.rerank_provider == "lexical":
            _RERANKER = LexicalReranker()
        elif settings.rerank_provider == "cross_encoder":
            _RERANKER = CrossEncoderReranker(settings.rerank_model)
        else:  # pragma: no cover - Settings 的 Literal 已阻止此分支
            return None
        _RERANKER_KEY = key
    return _RERANKER
