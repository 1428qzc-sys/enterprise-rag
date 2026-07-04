"""文本分块。

基于 LangChain 的 ``RecursiveCharacterTextSplitter``，并加入中文标点分隔符，
避免在句子中间硬切。页码 / 小标题等信息随 chunk 元数据一同保留，服务于溯源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config import settings
from .parsing import Section

# 中文优先的分隔符：段落 > 换行 > 中文句末标点 > 英文句末 > 逗号 > 空格 > 字符
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", "，", " ", ""]


@dataclass
class ChunkData:
    content: str
    page: Optional[int] = None
    meta: dict = field(default_factory=dict)


def _make_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_SEPARATORS,
        keep_separator=True,
        length_function=len,
    )


def chunk_text(
    text: str,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> List[str]:
    """把纯文本切成若干片（供测试与轻量调用）。"""
    splitter = _make_splitter(chunk_size or settings.chunk_size, chunk_overlap or settings.chunk_overlap)
    return [c.strip() for c in splitter.split_text(text) if c.strip()]


def chunk_sections(
    sections: List[Section],
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> List[ChunkData]:
    """把解析出的 Section 列表切块，保留页码/标题元数据。"""
    splitter = _make_splitter(chunk_size or settings.chunk_size, chunk_overlap or settings.chunk_overlap)
    chunks: List[ChunkData] = []
    for sec in sections:
        for piece in splitter.split_text(sec.text):
            piece = piece.strip()
            if not piece:
                continue
            meta: dict = {}
            if sec.title:
                meta["title"] = sec.title
            chunks.append(ChunkData(content=piece, page=sec.page, meta=meta))
    return chunks
