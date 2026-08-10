"""knowledge_base/chunking.py's fixed-size chunker (#98)."""

import pytest

from rivulets.knowledge_base.chunking import chunk_text


def test_empty_text_returns_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_text_shorter_than_chunk_size_returns_one_chunk() -> None:
    assert chunk_text("hello world", chunk_size=1000, overlap=100) == ["hello world"]


def test_splits_with_overlap() -> None:
    text = "x" * 2500
    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    assert len(chunks) == 3
    assert chunks[0] == "x" * 1000
    assert chunks[1] == "x" * 1000
    assert chunks[2] == "x" * 700  # remaining 2500 - 1800


def test_rejects_overlap_not_smaller_than_chunk_size() -> None:
    with pytest.raises(ValueError, match="chunk_size must be greater than overlap"):
        chunk_text("hello", chunk_size=100, overlap=100)
