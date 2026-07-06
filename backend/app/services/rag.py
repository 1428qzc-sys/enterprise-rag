"""RAG 编排：检索 → 组装带引用的上下文 → 拼 Prompt → 流式生成 → 落库。

特性：
- 引用溯源：上下文分块编号 [1][2]…，要求模型在答案中回标来源；返回结构化 sources。
- 对话记忆：注入最近若干轮历史，支持多轮追问。
- 问题改写(condense)：多轮场景下把「它多少钱」这类指代问题改写为可独立检索的问题，
  提升跟进问题的召回（best-effort，失败自动回退原问题）。
"""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional, Tuple

from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..core.llm import make_llm
from ..models import Conversation, Message
from ..schemas import SourceChunk
from .retrieval import RetrievedChunk, retrieve

SYSTEM_PROMPT = (
    "你是企业知识库智能问答助手。请严格依据【已知信息】回答用户问题，遵守以下规则：\n"
    "1. 只使用【已知信息】中的内容作答，不要编造或依赖外部知识；\n"
    "2. 在答案中用方括号标注引用来源编号，例如：根据规定……[1][2]；\n"
    "3. 若【已知信息】不足以回答，明确说明「根据现有资料无法回答该问题」，不要臆测；\n"
    "4. 回答使用简体中文，条理清晰、准确专业。"
)

CONDENSE_PROMPT = (
    "下面是一段对话历史和用户的最新问题。请把最新问题改写成一个不依赖上下文、"
    "可以独立用于检索的完整问题。只输出改写后的问题本身，不要任何解释。"
)


def build_context(retrieved: List[RetrievedChunk]) -> Tuple[str, List[SourceChunk]]:
    """把检索结果拼成带编号的上下文文本，并生成结构化引用列表（含长度截断）。"""
    sources: List[SourceChunk] = []
    blocks: List[str] = []
    total = 0
    for i, r in enumerate(retrieved, start=1):
        loc = f"《{r.document_name}》" + (f" 第{r.page}页" if r.page else "")
        block = f"[{i}] 来源：{loc}\n{r.content}"
        if blocks and total + len(block) > settings.max_context_chars:
            break
        blocks.append(block)
        total += len(block)
        sources.append(
            SourceChunk(
                index=i,
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                document_name=r.document_name,
                chunk_index=r.chunk_index,
                page=r.page,
                score=r.score,
                content=r.content,
            )
        )
    return "\n\n".join(blocks), sources


def build_messages(question: str, context: str, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    user_content = (
        f"【已知信息】\n{context or '（未检索到相关资料）'}\n\n"
        f"【用户问题】\n{question}\n\n"
        "请基于【已知信息】作答，并在相应位置用 [编号] 标注引用来源。"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": user_content}]


def load_history(session: Session, conversation_id: str, turns: int) -> List[Dict[str, str]]:
    rows = session.exec(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    ).all()
    msgs = [{"role": m.role, "content": m.content} for m in rows]
    return msgs[-turns:] if turns > 0 else msgs


def ensure_conversation(
    session: Session,
    kb_id: str,
    conversation_id: Optional[str],
    question: str,
    tenant_id: str = "",
    user_id: str = "",
) -> Conversation:
    if conversation_id:
        conv = session.get(Conversation, conversation_id)
        if conv is not None and conv.kb_id == kb_id:
            return conv
    title = question.strip().replace("\n", " ")[:24] or "新对话"
    conv = Conversation(
        kb_id=kb_id,
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        title=title,
    )
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv


async def condense_question(history: List[Dict[str, str]], question: str) -> str:
    """多轮追问改写为独立问题；best-effort，任何异常都回退原问题。"""
    if not history or settings.llm_provider == "echo":
        return question
    try:
        messages = [
            {"role": "system", "content": CONDENSE_PROMPT},
            *history,
            {"role": "user", "content": f"最新问题：{question}"},
        ]
        rewritten = (await make_llm().acomplete(messages)).strip()
        return rewritten or question
    except Exception:
        return question


async def _prepare(
    session: Session,
    kb,
    question: str,
    conversation_id: Optional[str],
    top_k: Optional[int],
    tenant_id: str = "",
    user_id: str = "",
) -> Tuple[Conversation, List[SourceChunk], List[Dict[str, str]], str]:
    """公共准备：建会话、改写、检索、组装消息、落库 user 消息。"""
    conv = ensure_conversation(session, kb.id, conversation_id, question, tenant_id, user_id)
    history = load_history(session, conv.id, settings.history_turns)

    used_query = await condense_question(history, question)
    # 嵌入/检索为同步阻塞调用，放线程池避免卡事件循环
    retrieved: List[RetrievedChunk] = await run_in_threadpool(retrieve, session, kb, used_query, top_k)
    context, sources = build_context(retrieved)
    messages = build_messages(question, context, history)

    session.add(Message(conversation_id=conv.id, role="user", content=question))
    conv.updated_at = datetime.utcnow()
    session.add(conv)
    session.commit()
    return conv, sources, messages, used_query


def _save_answer(session: Session, conv: Conversation, answer: str, sources: List[SourceChunk]) -> Message:
    msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=answer,
        sources=[s.model_dump() for s in sources],
    )
    session.add(msg)
    conv.updated_at = datetime.utcnow()
    session.add(conv)
    session.commit()
    session.refresh(msg)
    return msg


async def run_rag_stream(
    session: Session,
    kb,
    question: str,
    conversation_id: Optional[str] = None,
    top_k: Optional[int] = None,
    tenant_id: str = "",
    user_id: str = "",
) -> AsyncIterator[Dict]:
    """流式问答，逐事件产出（供 SSE）。"""
    try:
        conv, sources, messages, used_query = await _prepare(
            session, kb, question, conversation_id, top_k, tenant_id, user_id
        )
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": {"message": f"检索准备失败：{exc}"}}
        return

    yield {"event": "meta", "data": {"conversation_id": conv.id, "used_query": used_query}}
    yield {"event": "sources", "data": [s.model_dump() for s in sources]}

    parts: List[str] = []
    try:
        async for delta in make_llm().astream(messages):
            parts.append(delta)
            yield {"event": "token", "data": {"text": delta}}
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "data": {"message": f"生成失败：{exc}"}}
        return

    answer = "".join(parts)
    msg = _save_answer(session, conv, answer, sources)
    yield {"event": "done", "data": {"message_id": msg.id, "conversation_id": conv.id}}


async def run_rag(
    session: Session,
    kb,
    question: str,
    conversation_id: Optional[str] = None,
    top_k: Optional[int] = None,
    tenant_id: str = "",
    user_id: str = "",
) -> Dict:
    """非流式问答，返回完整结果。"""
    conv, sources, messages, _used = await _prepare(
        session, kb, question, conversation_id, top_k, tenant_id, user_id
    )
    answer = await make_llm().acomplete(messages)
    _save_answer(session, conv, answer, sources)
    return {
        "conversation_id": conv.id,
        "answer": answer,
        "sources": [s.model_dump() for s in sources],
    }
