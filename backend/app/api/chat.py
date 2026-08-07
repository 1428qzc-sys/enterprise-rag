"""问答与对话：RAG 问答（SSE 流式 / 非流式）、检索预览、会话与消息管理。"""

from __future__ import annotations

import json
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from ..audit import record_audit
from ..database import get_session
from ..models import Conversation, KnowledgeBase, Message
from ..schemas import (
    ChatRequest,
    ChatResponse,
    ConversationRead,
    MessageRead,
    RetrieveRequest,
    RetrieveResponse,
    SourceChunk,
)
from ..security import Principal
from ..services.rag import run_rag, run_rag_stream
from ..services.retrieval import retrieve_with_diagnostics
from .deps import can_access_kb, require_permission

router = APIRouter(tags=["chat"])


def _require_kb(kb_id: str, session: Session, principal: Principal) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


def _require_conversation(
    conversation_id: str,
    session: Session,
    principal: Principal,
    kb_id: str | None = None,
) -> Conversation:
    conv = session.get(Conversation, conversation_id)
    if (
        conv is None
        or conv.tenant_id != principal.tenant_id
        or (kb_id is not None and conv.kb_id != kb_id)
    ):
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    principal: Principal = Depends(require_permission("chat:use")),
    session: Session = Depends(get_session),
):
    kb = _require_kb(body.kb_id, session, principal)
    request_id = body.request_id or uuid4().hex
    if body.conversation_id:
        _require_conversation(body.conversation_id, session, principal, kb.id)

    if body.stream:
        record_audit(
            "chat.ask",
            "accepted",
            request=request,
            principal=principal,
            resource_type="knowledge_base",
            resource_id=kb.id,
            detail={"stream": True, "top_k": body.top_k, "request_id": request_id},
        )

        async def event_gen():
            event_id = 0
            async for ev in run_rag_stream(
                session,
                kb,
                body.question,
                body.conversation_id,
                body.top_k,
                principal.tenant_id,
                principal.user_id,
                request_id,
            ):
                if await request.is_disconnected():
                    break
                event_id += 1
                yield {
                    "id": str(event_id),
                    "event": ev["event"],
                    "data": json.dumps(ev["data"], ensure_ascii=False),
                }

        return EventSourceResponse(event_gen(), ping=15000)

    result = await run_rag(
        session,
        kb,
        body.question,
        body.conversation_id,
        body.top_k,
        principal.tenant_id,
        principal.user_id,
        request_id,
    )
    record_audit(
        "chat.ask",
        "success",
        request=request,
        principal=principal,
        resource_type="knowledge_base",
        resource_id=kb.id,
        detail={"stream": False, "top_k": body.top_k, "request_id": request_id},
    )
    return ChatResponse(**result)


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve_preview(
    body: RetrieveRequest,
    request: Request,
    principal: Principal = Depends(require_permission("chat:use")),
    session: Session = Depends(get_session),
) -> RetrieveResponse:
    kb = _require_kb(body.kb_id, session, principal)
    retrieval_result = retrieve_with_diagnostics(session, kb, body.query, body.top_k)
    results = [
        SourceChunk(
            index=i + 1,
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_name=c.document_name,
            chunk_index=c.chunk_index,
            page=c.page,
            score=c.score,
            score_type=c.score_type,
            vector_score=c.vector_score,
            bm25_score=c.bm25_score,
            rrf_score=c.rrf_score,
            rerank_score=c.rerank_score,
            injection_risk=c.injection_risk,
            content=c.content,
        )
        for i, c in enumerate(retrieval_result.chunks)
    ]
    record_audit(
        "chat.retrieve",
        "success",
        request=request,
        principal=principal,
        resource_type="knowledge_base",
        resource_id=kb.id,
        detail={
            "top_k": body.top_k,
            "result_count": len(results),
            "degraded": retrieval_result.diagnostics["degraded"],
        },
    )
    return RetrieveResponse(
        query=body.query,
        results=results,
        diagnostics=retrieval_result.diagnostics,
    )


@router.get("/knowledge-bases/{kb_id}/conversations", response_model=List[ConversationRead])
def list_conversations(
    kb_id: str,
    principal: Principal = Depends(require_permission("chat:use")),
    session: Session = Depends(get_session),
) -> List[ConversationRead]:
    _require_kb(kb_id, session, principal)
    convs = session.exec(
        select(Conversation)
        .where(Conversation.kb_id == kb_id, Conversation.tenant_id == principal.tenant_id)
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [ConversationRead.model_validate(c) for c in convs]


@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageRead])
def list_messages(
    conversation_id: str,
    principal: Principal = Depends(require_permission("chat:use")),
    session: Session = Depends(get_session),
) -> List[MessageRead]:
    conv = _require_conversation(conversation_id, session, principal)
    _require_kb(conv.kb_id, session, principal)
    msgs = session.exec(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    ).all()
    return [MessageRead.model_validate(m) for m in msgs]


def _conversation_markdown(conversation: Conversation, messages: List[Message]) -> str:
    title = conversation.title.replace("\n", " ").strip() or "对话"
    lines = [f"# {title}", ""]
    for message in messages:
        heading = "用户" if message.role == "user" else "助手"
        lines.extend([f"## {heading}", "", message.content.strip(), ""])
        if message.role != "assistant" or not message.sources:
            continue
        lines.extend(["### 引用", ""])
        for source in message.sources:
            index = source.get("index", "?")
            document_name = str(source.get("document_name", "未知文档"))
            page = source.get("page")
            location = f"，第 {page} 页" if page else ""
            content = " ".join(str(source.get("content", "")).split())
            lines.append(f"{index}. **{document_name}**{location}")
            if content:
                lines.append(f"   > {content}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@router.get("/conversations/{conversation_id}/export.md")
def export_conversation_markdown(
    conversation_id: str,
    request: Request,
    principal: Principal = Depends(require_permission("chat:use")),
    session: Session = Depends(get_session),
) -> PlainTextResponse:
    conversation = _require_conversation(conversation_id, session, principal)
    _require_kb(conversation.kb_id, session, principal)
    messages = list(
        session.exec(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        ).all()
    )
    record_audit(
        "conversation.export_markdown",
        "success",
        request=request,
        principal=principal,
        resource_type="conversation",
        resource_id=conversation.id,
        detail={"message_count": len(messages)},
    )
    return PlainTextResponse(
        _conversation_markdown(conversation, messages),
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="conversation-{conversation.id}.md"'
        },
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    request: Request,
    principal: Principal = Depends(require_permission("conversation:delete")),
    session: Session = Depends(get_session),
) -> None:
    conv = _require_conversation(conversation_id, session, principal)
    _require_kb(conv.kb_id, session, principal)
    session.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
    session.delete(conv)
    session.commit()
    record_audit(
        "conversation.delete",
        "success",
        request=request,
        principal=principal,
        resource_type="conversation",
        resource_id=conversation_id,
    )
