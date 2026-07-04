"""分块逻辑测试。"""

from app.services.chunking import chunk_sections, chunk_text
from app.services.parsing import Section


def test_chunk_text_splits_long_text():
    text = "。".join([f"这是第{i}个句子内容用于测试分块效果" for i in range(200)])
    chunks = chunk_text(text, chunk_size=200, chunk_overlap=40)
    assert len(chunks) > 1
    # 每块不应大幅超过 chunk_size（允许分隔符带来的少量溢出）
    assert all(len(c) <= 200 + 40 for c in chunks)
    assert all(c.strip() for c in chunks)


def test_chunk_text_empty():
    assert chunk_text("   ") == []


def test_chunk_sections_keeps_page_and_title():
    sections = [
        Section(text="A" * 500, page=1),
        Section(text="B" * 500, page=2, title="第二章"),
    ]
    chunks = chunk_sections(sections, chunk_size=200, chunk_overlap=20)
    pages = {c.page for c in chunks}
    assert pages == {1, 2}
    assert any(c.meta.get("title") == "第二章" for c in chunks)
