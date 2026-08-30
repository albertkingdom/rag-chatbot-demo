"""Unit tests for src/retrieval_grader.py — retrieval quality grading and
retry-rewrite query generation.

Hermetic: get_llm() is mocked; no network calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.documents import Document

from src.retrieval_grader import grade_documents, rewrite_for_retry
from src.config import GRADE_SCORE_THRESHOLD, RETRIEVAL_MAX_RETRIES


def _make_doc(content: str, **metadata) -> Document:
    return Document(page_content=content, metadata=metadata)


# ---------------------------------------------------------------------------
# grade_documents — pure logic, no mocking needed
# ---------------------------------------------------------------------------


class TestGradeDocuments:
    def test_high_score_passes(self):
        rerank_scores = [
            {"rank": 1, "score": "0.9500", "content": "relevant text"},
            {"rank": 2, "score": "0.7000", "content": "other text"},
        ]
        assert grade_documents(rerank_scores) is True

    def test_low_score_fails(self):
        rerank_scores = [
            {"rank": 1, "score": "0.1000", "content": "not relevant"},
            {"rank": 2, "score": "0.0500", "content": "also not relevant"},
        ]
        assert grade_documents(rerank_scores) is False

    def test_boundary_score_exactly_at_threshold_passes(self):
        rerank_scores = [
            {"rank": 1, "score": f"{GRADE_SCORE_THRESHOLD:.4f}", "content": "borderline"},
        ]
        assert grade_documents(rerank_scores) is True

    def test_empty_rerank_scores_fails(self):
        assert grade_documents([]) is False

    def test_single_result_above_threshold(self):
        rerank_scores = [{"rank": 1, "score": "0.5000", "content": "only result"}]
        assert grade_documents(rerank_scores) is True

    def test_single_result_below_threshold(self):
        rerank_scores = [{"rank": 1, "score": "0.0100", "content": "only result"}]
        assert grade_documents(rerank_scores) is False

    def test_uses_rank_1_regardless_of_list_order(self):
        # rank 1 entry is not first in the list — grading must still key off
        # "rank" == 1, not list position.
        rerank_scores = [
            {"rank": 2, "score": "0.9000", "content": "second best"},
            {"rank": 1, "score": "0.1000", "content": "top rank, low score"},
        ]
        assert grade_documents(rerank_scores) is False


# ---------------------------------------------------------------------------
# rewrite_for_retry — LLM mocked
# ---------------------------------------------------------------------------


class TestRewriteForRetry:
    @pytest.mark.asyncio
    async def test_returns_rewritten_query_different_from_original(self):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content="重新改寫後的查詢")
        )
        docs = [_make_doc("不相關的內容")]

        with patch("src.retrieval_grader.get_llm", return_value=mock_llm):
            result = await rewrite_for_retry("原始查詢", docs)

        assert result == "重新改寫後的查詢"
        assert result != "原始查詢"

    @pytest.mark.asyncio
    async def test_low_quality_docs_content_included_in_prompt(self):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="改寫查詢"))
        docs = [
            _make_doc("這是第一份不相關文件的內容"),
            _make_doc("這是第二份不相關文件的內容"),
        ]

        with patch("src.retrieval_grader.get_llm", return_value=mock_llm):
            await rewrite_for_retry("原始查詢", docs)

        assert mock_llm.ainvoke.await_count == 1
        prompt_arg = mock_llm.ainvoke.await_args[0][0]
        assert "這是第一份不相關文件的內容" in prompt_arg
        assert "這是第二份不相關文件的內容" in prompt_arg
        assert "原始查詢" in prompt_arg

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_original_query(self):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM unavailable"))
        docs = [_make_doc("某些內容")]

        with patch("src.retrieval_grader.get_llm", return_value=mock_llm):
            result = await rewrite_for_retry("原始查詢", docs)

        assert result == "原始查詢"

    @pytest.mark.asyncio
    async def test_empty_docs_list(self):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="改寫後查詢"))

        with patch("src.retrieval_grader.get_llm", return_value=mock_llm):
            result = await rewrite_for_retry("原始查詢", [])

        assert result == "改寫後查詢"
        prompt_arg = mock_llm.ainvoke.await_args[0][0]
        assert "原始查詢" in prompt_arg

    @pytest.mark.asyncio
    async def test_response_content_is_stripped(self):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content="  改寫後查詢帶有空白  \n")
        )
        with patch("src.retrieval_grader.get_llm", return_value=mock_llm):
            result = await rewrite_for_retry("原始查詢", [])

        assert result == "改寫後查詢帶有空白"


# ---------------------------------------------------------------------------
# Config values
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_grade_score_threshold_default(self):
        assert GRADE_SCORE_THRESHOLD == 0.3

    def test_retrieval_max_retries_default(self):
        assert RETRIEVAL_MAX_RETRIES == 1
