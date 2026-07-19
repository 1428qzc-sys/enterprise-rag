from app.core.reranker import CrossEncoderReranker, reciprocal_rank_fusion
from app.services import bm25_index
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


def test_rrf_single_ranking():
    fused = reciprocal_rank_fusion([["x", "y", "z"]], k=60)
    assert fused["x"] > fused["y"] > fused["z"]


def test_bm25_search_empty_kb(db_session):
    assert bm25_index.search(db_session, "nonexistent-kb-id", "年假", top_k=5) == []


def test_bm25_invalidate_clears_cache(db_session, seeded_kb):
    kb, _, _ = seeded_kb
    bm25_index.search(db_session, kb.id, "query", top_k=1)
    assert kb.id in bm25_index._CACHE
    bm25_index.invalidate(kb.id)
    assert kb.id not in bm25_index._CACHE

def test_cross_encoder_reranker_degrades_without_model():
    reranker = CrossEncoderReranker("nonexistent-model-path")
    docs = [("a", "年假政策"), ("b", "报销流程")]
    assert reranker.rerank("年假", docs, top_n=2) == ["a", "b"]
