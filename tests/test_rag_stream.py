"""Tests for _stream_text chunked streaming."""
import pytest

from src.rag_pipeline import _stream_text, _STREAM_CHUNK_SIZE


@pytest.mark.asyncio
async def test_30_char_response_yields_3_times():
    text = "A" * 30
    chunks = [c async for c in _stream_text(text)]
    assert len(chunks) == 3
    assert chunks[0] == text[:10]
    assert chunks[1] == text[:20]
    assert chunks[2] == text


@pytest.mark.asyncio
async def test_25_char_response_yields_3_times():
    text = "B" * 25
    chunks = [c async for c in _stream_text(text)]
    assert len(chunks) == 3
    assert chunks[0] == text[:10]
    assert chunks[1] == text[:20]
    assert chunks[2] == text


@pytest.mark.asyncio
async def test_final_yield_is_always_complete_text():
    for length in [1, 5, 10, 11, 19, 20, 21, 100]:
        text = "X" * length
        chunks = [c async for c in _stream_text(text)]
        assert chunks[-1] == text, f"Failed for length {length}"


@pytest.mark.asyncio
async def test_exact_multiple_no_duplicate_final():
    text = "Z" * 20
    chunks = [c async for c in _stream_text(text)]
    assert len(chunks) == 2
    assert chunks[-1] == text


@pytest.mark.asyncio
async def test_short_text_yields_once():
    text = "Hi"
    chunks = [c async for c in _stream_text(text)]
    assert len(chunks) == 1
    assert chunks[0] == text


@pytest.mark.asyncio
async def test_empty_text_yields_once():
    chunks = [c async for c in _stream_text("")]
    assert len(chunks) == 1
    assert chunks[0] == ""
