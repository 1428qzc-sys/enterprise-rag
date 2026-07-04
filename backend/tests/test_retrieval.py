"""检索融合与分词测试。"""

from app.core.reranker import reciprocal_rank_fusion
from app.services.bm25_index import tokenize


def test_rrf_prefers_docs_ranked_high_in_both():
    vector_rank = ["a", "b", "c", "d"]
    bm25_rank = ["b", "a", "e", "f"]
    fused = reciprocal_rank_fusion([vector_rank, bm25_rank], k=60)
    order = sorted(fused, key=lambda d: fused[d], reverse=True)
    # a、b 在两路都靠前，应排在只出现在单路的文档之前
    assert set(order[:2]) == {"a", "b"}
    assert fused["a"] > fused["c"]
    assert fused["b"] > fused["e"]


def test_rrf_empty():
    assert reciprocal_rank_fusion([[], []]) == {}


def test_tokenize_mixed_language():
    tokens = tokenize("企业知识库 RAG 系统 GPT4")
    assert "企业" in tokens or "知识库" in tokens
    # 英文/数字应被切出
    assert any(t == "rag" for t in tokens)
    assert any("gpt" in t for t in tokens)
