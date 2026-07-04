"""按知识库维护的 BM25 稀疏索引（jieba 分词）。

混合检索的稀疏一路：向量擅长语义、BM25 擅长关键词/术语/型号精确匹配，二者互补。
索引按 kb_id 缓存于内存，文档增删/重嵌入后由调用方 ``invalidate`` 显式失效，
缓存未命中时从关系库懒重建。
"""

from __future__ import annotations

import re
import threading
from typing import Dict, List, Optional, Tuple

import jieba
from rank_bm25 import BM25Okapi
from sqlmodel import Session, select

from ..models import Chunk

# 关闭 jieba 初始化日志
jieba.setLogLevel(20)

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_lock = threading.RLock()


def tokenize(text: str) -> List[str]:
    """中英混合分词：jieba 切中文，正则补全英文/数字 token，统一小写。"""
    text = text.lower()
    tokens = [t.strip() for t in jieba.lcut(text) if t.strip()]
    tokens = [t for t in tokens if not t.isspace()]
    # 补充英文/数字连续片段，增强术语/型号命中
    tokens += _WORD_RE.findall(text)
    return [t for t in tokens if len(t) > 0]


class _KBIndex:
    def __init__(self, chunk_ids: List[str], bm25: Optional[BM25Okapi]):
        self.chunk_ids = chunk_ids
        self.bm25 = bm25


_CACHE: Dict[str, _KBIndex] = {}


def _build(session: Session, kb_id: str) -> _KBIndex:
    rows = session.exec(
        select(Chunk.id, Chunk.content).where(Chunk.kb_id == kb_id)
    ).all()
    chunk_ids = [r[0] for r in rows]
    corpus = [tokenize(r[1]) for r in rows]
    bm25 = BM25Okapi(corpus) if corpus else None
    return _KBIndex(chunk_ids=chunk_ids, bm25=bm25)


def _get_index(session: Session, kb_id: str) -> _KBIndex:
    with _lock:
        idx = _CACHE.get(kb_id)
        if idx is None:
            idx = _build(session, kb_id)
            _CACHE[kb_id] = idx
        return idx


def invalidate(kb_id: str) -> None:
    with _lock:
        _CACHE.pop(kb_id, None)


def search(session: Session, kb_id: str, query: str, top_k: int) -> List[Tuple[str, float]]:
    """返回 [(chunk_id, bm25_score)]，按分数降序。"""
    idx = _get_index(session, kb_id)
    if idx.bm25 is None or not idx.chunk_ids:
        return []
    scores = idx.bm25.get_scores(tokenize(query))
    ranked = sorted(zip(idx.chunk_ids, scores), key=lambda x: x[1], reverse=True)
    return [(cid, float(s)) for cid, s in ranked[:top_k] if s > 0]
