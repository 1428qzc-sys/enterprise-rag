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
_warmed_up = False


def warmup() -> None:
    """在 readiness 之前加载 Jieba 词典，避免首个检索承担数秒冷启动。"""

    global _warmed_up
    with _lock:
        if _warmed_up:
            return
        jieba.initialize()
        # 同时走一遍中英混合路径，提前编译/填充相关内部缓存。
        tokenize("企业知识库 Enterprise RAG 检索预热 2026")
        _warmed_up = True


def tokenize(text: str) -> List[str]:
    """中英混合分词：jieba 切中文，正则补全英文/数字 token，统一小写。"""
    text = text.lower()
    tokens = [t.strip() for t in jieba.lcut(text) if t.strip()]
    tokens = [t for t in tokens if not t.isspace()]
    # 补充英文/数字连续片段，增强术语/型号命中
    tokens += _WORD_RE.findall(text)
    return [t for t in tokens if len(t) > 0]


class _KBIndex:
    def __init__(
        self,
        chunk_ids: List[str],
        bm25: Optional[BM25Okapi],
        corpus: List[List[str]],
    ):
        self.chunk_ids = chunk_ids
        self.bm25 = bm25
        self.corpus = corpus


_CACHE: Dict[str, _KBIndex] = {}


def _build(session: Session, kb_id: str) -> _KBIndex:
    rows = session.exec(
        select(Chunk.id, Chunk.content).where(
            Chunk.kb_id == kb_id,
            Chunk.is_active.is_(True),
        )
    ).all()
    chunk_ids = [r[0] for r in rows]
    corpus = [tokenize(r[1]) for r in rows]
    bm25 = BM25Okapi(corpus) if corpus else None
    return _KBIndex(chunk_ids=chunk_ids, bm25=bm25, corpus=corpus)


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
    query_tokens = tokenize(query)
    scores = idx.bm25.get_scores(query_tokens)
    query_set = set(query_tokens)
    ranked: List[Tuple[str, float]] = []
    for chunk_id, raw_score, tokens in zip(idx.chunk_ids, scores, idx.corpus):
        overlap = len(query_set & set(tokens))
        if overlap == 0:
            continue
        # BM25Okapi 在极小语料中会因 IDF=0 返回 0；词项覆盖率只在该退化场景回退。
        effective_score = float(raw_score)
        if effective_score <= 0:
            effective_score = overlap / max(1, len(query_set))
        ranked.append((chunk_id, effective_score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:top_k]
