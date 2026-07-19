"""RAG 编排层单元测试：上下文组装、会话管理、问题改写、检索降级。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models import Conversation, KnowledgeBase, Message
from app.services.rag import (
    build_context,
    build_messages,
    condense_question,
    ensure_conversation,
    load_history,
)
from app.services.retrieval import RetrievedChunk, retrieve


def _sample_chunks():
    return [
        RetrievedChunk(
            chunk_id="c1",
            document_id="d1",
            document_name="手册.pdf",
            chunk_index=0,
            page=3,
            content="年假 15 天。",
            score=0.42,
        ),
        RetrievedChunk(
            chunk_id="c2",
            document_id="d1",
            document_name="手册.pdf",
            chunk_index=1,
            page=None,
            content="报销流程说明。",
            score=0.31,
        ),
    ]


def test_build_context_numbering_and_page_label():
    context, sources = build_context(_sample_chunks())
    assert "[1]" in context and "[2]" in context
    assert "《手册.pdf》 第3页" in context
    assert "《手册.pdf》" in context
    assert sources[1].page is None
    assert len(sources) == 2
    assert sources[0].index == 1 and sources[0].page == 3
    assert sources[1].document_name == "手册.pdf"


def test_build_context_truncates_when_over_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_context_chars", 80)
    long_chunks = [
        RetrievedChunk(
            chunk_id=f"c{i}",
            document_id="d1",
            document_name="doc.txt",
            chunk_index=i,
            page=1,
            content="X" * 60,
            score=1.0,
        )
        for i in range(5)
    ]
    _, sources = build_context(long_chunks)
    assert len(sources) < len(long_chunks)


def test_build_context_empty_chunks():
    context, sources = build_context([])
    assert context == ""
    assert sources == []


def test_build_messages_with_context_and_history():
    history = [{"role": "assistant", "content": "上次回答"}]
    msgs = build_messages("追问", "[1] 来源：doc\n内容", history)
    assert msgs[-1]["role"] == "user"
    assert "追问" in msgs[-1]["content"]
    assert "[1]" in msgs[-1]["content"]


def test_build_messages_includes_history_and_empty_context():
    history = [{"role": "user", "content": "上次问了年假"}]
    msgs = build_messages("有多少天？", "", history)
    assert msgs[0]["role"] == "system"
    assert msgs[1] == history[0]
    assert "未检索到相关资料" in msgs[-1]["content"]
    assert "有多少天？" in msgs[-1]["content"]


def test_load_history_respects_turn_limit(db_session, seeded_kb):
    kb, _, _ = seeded_kb
    conv = Conversation(kb_id=kb.id, tenant_id=kb.tenant_id, title="t")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    for i in range(6):
        db_session.add(Message(conversation_id=conv.id, role="user", content=f"q{i}"))
    db_session.commit()

    loaded = load_history(db_session, conv.id, turns=3)
    assert len(loaded) == 3
    assert loaded[-1]["content"] == "q5"


def test_ensure_conversation_creates_and_reuses(db_session, seeded_kb):
    kb, _, _ = seeded_kb
    conv1 = ensure_conversation(db_session, kb.id, None, "年假有多少天？", kb.tenant_id)
    assert conv1.title.startswith("年假")
    conv2 = ensure_conversation(db_session, kb.id, conv1.id, "追问", kb.tenant_id)
    assert conv2.id == conv1.id


def test_ensure_conversation_ignores_mismatched_kb(db_session, seeded_kb):
    kb, _, _ = seeded_kb
    other = Conversation(kb_id="other-kb", tenant_id=kb.tenant_id, title="x")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    conv = ensure_conversation(db_session, kb.id, other.id, "新问题", kb.tenant_id)
    assert conv.id != other.id
    assert conv.kb_id == kb.id
    db_session.delete(other)
    db_session.commit()


@pytest.mark.asyncio
async def test_condense_question_skips_for_echo_provider():
    result = await condense_question([{"role": "user", "content": "年假？"}], "它有多少天？")
    assert result == "它有多少天？"


@pytest.mark.asyncio
async def test_condense_question_rewrites_with_llm(monkeypatch):
    monkeypatch.setattr("app.services.rag.settings.llm_provider", "openai")
    with patch("app.services.rag.make_llm") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.acomplete = AsyncMock(return_value="员工每年享有多少天年假？")
        mock_factory.return_value = mock_llm
        result = await condense_question(
            [{"role": "user", "content": "公司年假政策是什么？"}],
            "它有多少天？",
        )
    assert result == "员工每年享有多少天年假？"


@pytest.mark.asyncio
async def test_condense_question_fallback_on_llm_error(monkeypatch):
    monkeypatch.setattr("app.services.rag.settings.llm_provider", "openai")
    with patch("app.services.rag.make_llm") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.acomplete = AsyncMock(side_effect=RuntimeError("llm unavailable"))
        mock_factory.return_value = mock_llm
        question = "它有多少天？"
        result = await condense_question([{"role": "user", "content": "年假？"}], question)
    assert result == question


def test_retrieve_hybrid_returns_ranked_chunks(db_session, seeded_kb):
    kb, doc, chunks = seeded_kb
    results = retrieve(db_session, kb, "年假有多少天")
    assert len(results) >= 1
    assert any("年假" in r.content for r in results)
    assert results[0].document_name == doc.name
    assert all(r.chunk_id in {c.id for c in chunks} for r in results)


def test_retrieve_returns_empty_without_chunks(db_session, seeded_kb):
    kb, _, _ = seeded_kb
    empty_kb = KnowledgeBase(
        tenant_id=kb.tenant_id,
        name="empty-kb",
        embedding_provider="fake",
        embedding_model="fake",
        embedding_dim=64,
        vector_backend="memory",
    )
    db_session.add(empty_kb)
    db_session.commit()
    db_session.refresh(empty_kb)
    assert retrieve(db_session, empty_kb, "年假") == []


def test_retrieve_degrades_to_bm25_when_vector_fails(db_session, seeded_kb, monkeypatch):
    kb, _, chunks = seeded_kb

    def _boom(*_args, **_kwargs):
        raise RuntimeError("vector backend down")

    monkeypatch.setattr("app.services.retrieval.make_embeddings", _boom)
    monkeypatch.setattr(
        "app.services.retrieval.bm25_index.search",
        lambda _session, _kb_id, _query, _top_k: [(chunks[0].id, 1.5)],
    )
    results = retrieve(db_session, kb, "带薪年假")
    assert len(results) == 1
    assert results[0].chunk_id == chunks[0].id
    assert "年假" in results[0].content


def test_chat_multi_turn_keeps_conversation(client):
    from tests.test_api import _auth_headers, _create_kb, _upload_text, _wait_done

    kb_id = _create_kb(client, "多轮库")
    headers = _auth_headers(client)
    doc_id = _upload_text(client, kb_id)
    _wait_done(client, kb_id, doc_id)

    r1 = client.post(
        "/api/chat",
        json={"kb_id": kb_id, "question": "年假有多少天？", "stream": False},
        headers=headers,
    )
    assert r1.status_code == 200
    conv_id = r1.json()["conversation_id"]

    r2 = client.post(
        "/api/chat",
        json={
            "kb_id": kb_id,
            "question": "报销流程呢？",
            "conversation_id": conv_id,
            "stream": False,
        },
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["conversation_id"] == conv_id

    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    assert len(msgs) >= 4
