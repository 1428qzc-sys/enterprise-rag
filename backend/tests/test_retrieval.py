from app.core.reranker import (
    CrossEncoderReranker,
    LexicalReranker,
    reciprocal_rank_fusion,
)
from app.core.embeddings import FakeEmbeddings
from app.services import bm25_index
from app.services.bm25_index import tokenize
from app.services.retrieval import retrieve_with_diagnostics


def test_bm25_warmup_is_idempotent(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(bm25_index, "_warmed_up", False)
    monkeypatch.setattr(bm25_index.jieba, "initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(
        bm25_index,
        "tokenize",
        lambda _text: calls.append("tokenize") or [],
    )

    bm25_index.warmup()
    bm25_index.warmup()

    assert calls == ["initialize", "tokenize"]


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
    outcome = reranker.rerank_with_scores("年假", docs, top_n=2)
    assert outcome.applied is False
    assert outcome.degraded_reason.startswith("model_unavailable:")


def test_lexical_reranker_applies_and_changes_order():
    reranker = LexicalReranker()
    docs = [("unrelated", "差旅报销流程"), ("relevant", "带薪年假十五天")]
    outcome = reranker.rerank_with_scores("年假有多少天", docs, top_n=2)
    assert outcome.applied is True
    assert outcome.provider == "lexical"
    assert outcome.ids[0] == "relevant"
    assert outcome.scores["relevant"] > outcome.scores["unrelated"]


def test_retrieval_diagnostics_report_each_stage(db_session, seeded_kb):
    kb, _, _ = seeded_kb
    result = retrieve_with_diagnostics(db_session, kb, "带薪年假多少天", top_k=2)
    assert result.chunks
    assert result.diagnostics["vector"]["status"] == "ok"
    assert result.diagnostics["bm25"]["status"] == "ok"
    assert result.diagnostics["fusion"]["method"] == "rrf"
    assert result.diagnostics["rerank"]["status"] == "applied"
    assert result.diagnostics["rerank"]["provider"] == "lexical"
    assert result.diagnostics["total_ms"] >= 0
    assert result.chunks[0].score_type == "rerank"


def test_fake_embeddings_tokenize_continuous_chinese_deterministically():
    embedder = FakeEmbeddings("fake", 256)
    query = embedder.embed_query("带薪年假申请入口")
    relevant = embedder.embed_query("员工通过门户提交带薪年假申请")
    unrelated = embedder.embed_query("量子咖啡配方编号")
    relevant_score = sum(left * right for left, right in zip(query, relevant))
    unrelated_score = sum(left * right for left, right in zip(query, unrelated))
    assert relevant_score > 0.2
    assert relevant_score > unrelated_score
