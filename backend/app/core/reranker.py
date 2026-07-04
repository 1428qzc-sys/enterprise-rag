"""重排：RRF 融合（默认） + 可选交叉编码器重排（BGE reranker）。

- ``reciprocal_rank_fusion``：把「向量」与「BM25」两路排名做倒数秩融合，
  无需额外模型即可显著优于单路，是混合检索的默认重排策略。
- ``CrossEncoderReranker``：可选，加载 BGE-reranker 交叉编码器对候选做精排，
  需安装 requirements-optional.txt 且 RERANK_ENABLED=true。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..config import settings


def reciprocal_rank_fusion(rankings: List[List[str]], k: int = 60) -> Dict[str, float]:
    """倒数秩融合。

    Args:
        rankings: 多路检索结果，每路是按相关度降序排列的 id 列表。
        k: 平滑常数，越大则高位与低位差距越小。
    Returns:
        id -> 融合分数（越大越相关）。
    """
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


class CrossEncoderReranker:
    """惰性加载的交叉编码器重排器。加载失败时安全降级为不重排。"""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._available: Optional[bool] = None

    def _ensure(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            self._available = True
        except Exception:
            self._model = None
            self._available = False
        return self._available

    def rerank(self, query: str, docs: List[Tuple[str, str]], top_n: int) -> List[str]:
        """对候选精排。

        Args:
            docs: [(doc_id, text), ...]
        Returns:
            重排后的 doc_id 列表（截断到 top_n）。加载失败则原样返回。
        """
        if not docs:
            return []
        if not self._ensure():
            return [d[0] for d in docs][:top_n]
        pairs = [(query, text) for _, text in docs]
        scores = self._model.predict(pairs)
        order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
        return [docs[i][0] for i in order][:top_n]


_RERANKER: Optional[CrossEncoderReranker] = None


def get_reranker() -> Optional[CrossEncoderReranker]:
    """按配置返回重排器；未启用返回 None。"""
    global _RERANKER
    if not settings.rerank_enabled or settings.rerank_provider != "cross_encoder":
        return None
    if _RERANKER is None:
        _RERANKER = CrossEncoderReranker(settings.rerank_model)
    return _RERANKER
