"""解析与 BM25 分词单元测试（纯函数，无需 TestClient）。"""

import os

import pytest

from app.services.bm25_index import tokenize
from app.services.parsing import SUPPORTED_EXTS, detect_type


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("report.PDF", "pdf"),
        ("notes.docx", "docx"),
        ("data.xlsx", "xlsx"),
        ("page.html", "html"),
        ("readme.md", "markdown"),
        ("readme.markdown", "markdown"),
        ("plain.txt", "text"),
        ("data.csv", "text"),
    ],
)
def test_detect_type(filename, expected):
    assert detect_type(filename) == expected


def test_supported_exts_covers_common_formats():
    for ext in (".pdf", ".docx", ".xlsx", ".md", ".txt", ".html"):
        assert ext in SUPPORTED_EXTS


def test_tokenize_mixed_chinese_english():
    tokens = tokenize("员工 annual leave 15天 GPT-4o")
    assert "员工" in tokens
    assert "annual" in tokens
    assert "leave" in tokens
    assert "15" in tokens
    assert "gpt" in tokens or "4o" in tokens


def test_tokenize_empty_string():
    assert tokenize("   ") == []


def test_tokenize_preserves_numbers():
    tokens = tokenize("订单123号 HT-2024")
    assert "123" in tokens
    assert "2024" in tokens
