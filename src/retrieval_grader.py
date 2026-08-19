"""Retrieval quality grading + retry rewrite.

Evaluates whether the reranker's top-1 score is good enough to trust the
retrieved documents, and — when it isn't — rewrites the user's query so a
second retrieval pass can try a different angle. This module is intentionally
narrow: it does NOT own the retry loop (that lives in rag_pipeline.py); it
only answers "is this good enough?" and "give me a better query".
"""

import logging

from langsmith import traceable

from .config import GRADE_SCORE_THRESHOLD
from .services import get_llm

logger = logging.getLogger("retrieval_grader")


@traceable(name="Retrieval Grade")
def grade_documents(rerank_scores: list[dict]) -> bool:
    """Grades retrieval quality using the reranker's top-1 score.

    Args:
        rerank_scores: the ``rerank_scores`` list produced by
            :func:`src.rerank_stage.rerank_candidates`, e.g.
            ``[{"rank": 1, "score": "0.9500", "content": "..."}, ...]``.

    Returns:
        ``True`` when the top-1 score is at or above
        ``GRADE_SCORE_THRESHOLD`` (quality sufficient, no retry needed),
        ``False`` otherwise (including when ``rerank_scores`` is empty).
    """
    if not rerank_scores:
        return False

    top = min(rerank_scores, key=lambda entry: entry["rank"])
    top_score = float(top["score"])

    passed = top_score >= GRADE_SCORE_THRESHOLD
    logger.info(
        "[Retrieval Grade] top-1 score=%.4f threshold=%.4f passed=%s",
        top_score,
        GRADE_SCORE_THRESHOLD,
        passed,
    )
    return passed


@traceable(name="Retry Rewrite")
async def rewrite_for_retry(original_query: str, low_quality_docs: list) -> str:
    """Rewrites ``original_query`` after a low-quality retrieval attempt.

    Args:
        original_query: the query that produced insufficient results.
        low_quality_docs: the ``Document`` objects returned by the failed
            retrieval attempt (used to tell the LLM what didn't work so it
            can try different keywords/angles).

    Returns:
        The rewritten query string. On any LLM error, falls back to
        returning ``original_query`` unchanged.
    """
    retrieved_summary = "\n".join(
        f"- {doc.page_content}" for doc in low_quality_docs
    ) or "（無檢索結果）"

    prompt = f"""你是一個檢索查詢優化助手。以下查詢的檢索結果品質不佳，請將查詢改寫，
使用不同的關鍵字或角度，以提高檢索相關文件的機會。

原始查詢：{original_query}

目前檢索到但不相關或品質不佳的內容：
{retrieved_summary}

請直接輸出改寫後的查詢，不要包含任何說明文字："""

    try:
        llm = get_llm()
        response = await llm.ainvoke(prompt)
        rewritten = response.content.strip()
        logger.info("[Retry Rewrite] Original: %s", original_query)
        logger.info("[Retry Rewrite] Rewritten: %s", rewritten)
        return rewritten
    except Exception as e:
        logger.warning("[Retry Rewrite] ERROR: %s", e)
        return original_query
