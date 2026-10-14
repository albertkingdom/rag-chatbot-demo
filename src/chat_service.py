"""Transport-independent chat application service.

The service accepts plain identifiers and emits typed events for the API.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import langsmith
from langsmith import traceable

from .cache_service import PromptCacheService
from .chat_events import (
    ChatStreamEvent,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    MetadataEvent,
    SourceItem,
    SourcesEvent,
    StatusEvent,
)
from .chat_history_service import ChatHistoryService
from .config import CACHE_ENABLED, CACHE_SIMILARITY_THRESHOLD, RETRIEVAL_MAX_RETRIES
from .conversation_db import get_conversation_db
from .guardrails import detect_pii, detect_prompt_injection, get_guardrail_message
from .query_router import route_query
from . import retrieval_grader
from .rag_pipeline import _today_str, format_history
from .rag_pipeline import LOW_CONFIDENCE_NOTE, LOW_CONFIDENCE_PREFIX
from .rag_pipeline import get_generation_chain, get_retrieval_chain
from .retrieval_grader import get_top_score, grade_documents, rewrite_for_retry
from .services import get_embeddings, get_redis_conn

logger = logging.getLogger("chat_service")

_STREAM_CHUNK_SIZE = 10
_STREAM_CHUNK_DELAY = 0.005


async def _answer_events(text: str) -> AsyncGenerator[DeltaEvent, None]:
    """Emit append-only chunks containing only the answer body."""
    for start in range(0, len(text), _STREAM_CHUNK_SIZE):
        yield DeltaEvent(text=text[start : start + _STREAM_CHUNK_SIZE])
        if start + _STREAM_CHUNK_SIZE < len(text):
            await asyncio.sleep(_STREAM_CHUNK_DELAY)


def _elapsed_ms(start_time: float) -> int:
    return max(0, round((time.monotonic() - start_time) * 1000))


async def _finish_response(
    *,
    answer: str,
    start_time: float,
    response_source: str,
    cache_hit: bool,
    sources: list[str] | None = None,
) -> AsyncGenerator[ChatStreamEvent, None]:
    async for event in _answer_events(answer):
        yield event
    if sources:
        yield SourcesEvent(items=[SourceItem(label=source) for source in sources])
    yield MetadataEvent(
        elapsed_ms=_elapsed_ms(start_time),
        response_source=response_source,
        cache_hit=cache_hit,
    )
    yield DoneEvent()


@dataclass
class _RetrievalOutcome:
    """The retrieval kept for generation, plus how it was reached."""

    bundle: dict
    # Top-1 score of every retrieval actually executed, original first.
    attempt_scores: list[float | None]
    # Index into attempt_scores: 0 is the original query, 1 the first retry...
    kept_attempt: int
    # True only when retrying ended because the rewritten query was empty or
    # repeated an earlier query.
    retry_skipped: bool
    low_confidence: bool
    threshold: float

    def as_metadata(self) -> dict:
        return {
            "retrieval": {
                "top1_score": self.attempt_scores[self.kept_attempt],
                "attempt_scores": self.attempt_scores,
                "retry_count": len(self.attempt_scores) - 1,
                "kept_attempt": self.kept_attempt,
                "low_confidence": self.low_confidence,
                "threshold": self.threshold,
                "retry_skipped": self.retry_skipped,
            }
        }


def _is_better(score: float | None, best: float | None) -> bool:
    """A retry replaces the kept result only on a strictly higher top-1 score.

    ``None`` (no documents) is the lowest possible score.
    """
    return score is not None and (best is None or score > best)


async def _retrieve_best(rewritten_query: str) -> _RetrievalOutcome:
    """Retrieve, retrying while grading fails, and keep the best attempt.

    A retry that scores worse than what is already kept is discarded, and no
    retrieval is made for a rewritten query that is empty or repeats any query
    already tried (the rewrite call itself still happens first).
    """
    bundle = await get_retrieval_chain().ainvoke(rewritten_query)
    attempt_scores = [get_top_score(bundle.get("rerank_scores", []))]
    best = bundle
    kept_attempt = 0
    passed = grade_documents(best.get("rerank_scores", []))
    tried = {rewritten_query.strip()}
    last_query, last_bundle = rewritten_query, bundle
    retry_skipped = False

    while not passed and len(attempt_scores) - 1 < RETRIEVAL_MAX_RETRIES:
        retry_query = (await rewrite_for_retry(last_query, last_bundle.get("docs", [])) or "").strip()
        if not retry_query or retry_query in tried:
            retry_skipped = True
            break
        tried.add(retry_query)
        bundle = await get_retrieval_chain().ainvoke(retry_query)
        score = get_top_score(bundle.get("rerank_scores", []))
        attempt_scores.append(score)
        if _is_better(score, attempt_scores[kept_attempt]):
            best = bundle
            kept_attempt = len(attempt_scores) - 1
            passed = grade_documents(best.get("rerank_scores", []))
        last_query, last_bundle = retry_query, bundle

    outcome = _RetrievalOutcome(
        bundle=best,
        attempt_scores=attempt_scores,
        kept_attempt=kept_attempt,
        retry_skipped=retry_skipped,
        low_confidence=not passed,
        threshold=retrieval_grader.GRADE_SCORE_THRESHOLD,
    )
    logger.info(
        "[Retrieval] attempts=%s kept=%d low_confidence=%s retry_skipped=%s",
        attempt_scores,
        kept_attempt,
        outcome.low_confidence,
        retry_skipped,
    )
    return outcome


@traceable(name="RAG_DEMO Chat Events")
async def chat_event_stream(
    message: str,
    *,
    history_key: str | None,
    session_id: str | None,
) -> AsyncGenerator[ChatStreamEvent, None]:
    """Run one chat request and emit transport-neutral events.

    `history_key` and `session_id` must be derived by the server. The browser
    never supplies either value directly.
    """
    history_service = ChatHistoryService(get_redis_conn())
    history = history_service.get_history(history_key) or []

    start_time = time.monotonic()
    full_response = ""

    try:
        injection_hit, _ = detect_prompt_injection(message)
        if injection_hit:
            guardrail_message = get_guardrail_message()
            await get_conversation_db().async_save_conversation(
                user_question=message,
                assistant_response=guardrail_message,
                session_id=session_id,
                response_source="guardrail",
                metadata={"injection_pattern": "input_injection"},
            )
            async for event in _finish_response(
                answer=guardrail_message,
                start_time=start_time,
                response_source="guardrail",
                cache_hit=False,
            ):
                yield event
            return

        router_result = await route_query(message, history)
        rewritten_query = router_result.rewritten_query
        logger.info(
            "[Router] route=%s rewritten=%s reasoning=%s",
            router_result.route,
            rewritten_query,
            router_result.reasoning,
        )

        if router_result.route == "direct":
            history_text = format_history(history)
            async for chunk in get_generation_chain().astream(
                {
                    "context": "",
                    "question": message,
                    "history": history_text,
                    "today": _today_str(),
                }
            ):
                full_response += chunk

            pii_hit, pii_type = detect_pii(full_response)
            injection_hit, injection_pattern = detect_prompt_injection(full_response)
            if pii_hit or injection_hit:
                guardrail_message = get_guardrail_message()
                await get_conversation_db().async_save_conversation(
                    user_question=message,
                    assistant_response=guardrail_message,
                    session_id=session_id,
                    response_source="guardrail",
                    cache_hit=False,
                    metadata={
                        "pii_type": pii_type if pii_hit else None,
                        "injection_pattern": injection_pattern if injection_hit else None,
                    },
                )
                async for event in _finish_response(
                    answer=guardrail_message,
                    start_time=start_time,
                    response_source="guardrail",
                    cache_hit=False,
                ):
                    yield event
                return

            history_service.append_turn(history_key, message, full_response)
            await get_conversation_db().async_save_conversation(
                user_question=message,
                assistant_response=full_response,
                session_id=session_id,
                response_source="direct",
                cache_hit=False,
            )
            async for event in _finish_response(
                answer=full_response,
                start_time=start_time,
                response_source="direct",
                cache_hit=False,
            ):
                yield event
            return

        yield StatusEvent(
            stage="retrieving", message="正在從知識庫檢索相關資訊…"
        )
        embeddings = get_embeddings()
        with langsmith.trace(name="Embed Query (OpenAI)"):
            question_embedding = await embeddings.aembed_query(rewritten_query)

        cached_response = None
        if CACHE_ENABLED:
            with langsmith.trace(name="Cache Lookup (Redis)"):
                cache_service = PromptCacheService(get_redis_conn())
                cached_response = cache_service.get_cached_response(
                    question_embedding,
                    threshold=CACHE_SIMILARITY_THRESHOLD,
                )

        if cached_response:
            cached_answer = cached_response["answer"]
            cached_sources = cached_response.get("sources", [])
            pii_hit, pii_type = detect_pii(cached_answer)
            injection_hit, injection_pattern = detect_prompt_injection(cached_answer)
            if pii_hit or injection_hit:
                guardrail_message = get_guardrail_message()
                await get_conversation_db().async_save_conversation(
                    user_question=message,
                    assistant_response=guardrail_message,
                    session_id=session_id,
                    response_source="guardrail",
                    cache_hit=False,
                    metadata={
                        "pii_type": pii_type if pii_hit else None,
                        "injection_pattern": injection_pattern if injection_hit else None,
                        "cache_candidate": True,
                        "cache_similarity": cached_response.get("similarity"),
                    },
                )
                async for event in _finish_response(
                    answer=guardrail_message,
                    start_time=start_time,
                    response_source="guardrail",
                    cache_hit=False,
                ):
                    yield event
                return

            await get_conversation_db().async_save_conversation(
                user_question=message,
                assistant_response=cached_answer,
                session_id=session_id,
                response_source="cache",
                cache_hit=True,
                cache_similarity=cached_response["similarity"],
            )
            history_service.append_turn(history_key, message, cached_answer)
            async for event in _finish_response(
                answer=cached_answer,
                start_time=start_time,
                response_source="cache",
                cache_hit=True,
                sources=cached_sources,
            ):
                yield event
            return

        yield StatusEvent(
            stage="reranking", message="正在優化檢索結果…"
        )
        outcome = await _retrieve_best(rewritten_query)
        # A low-confidence answer is not grounded in the knowledge base: its
        # sources are not presented as citations, and the retrieved passages
        # are not sent to the model either. A top-1 score this low means no FAQ
        # question resembles the user's, and the model otherwise narrates the
        # unrelated passage ("this text explains how to open the dashboard...").
        context = "" if outcome.low_confidence else outcome.bundle.get("context", "")
        sources = [] if outcome.low_confidence else outcome.bundle.get("sources", [])
        retrieval_metadata = outcome.as_metadata()

        yield StatusEvent(stage="generating", message="正在生成回答…")
        history_text = format_history(history)
        async for chunk in get_generation_chain().astream(
            {
                "context": context,
                "question": message,
                "history": history_text,
                "today": _today_str(),
                "confidence_note": LOW_CONFIDENCE_NOTE if outcome.low_confidence else "",
            }
        ):
            full_response += chunk

        if outcome.low_confidence and full_response.strip():
            # Added after generation and before the output guardrail, so the
            # streamed text, the history turn and the conversation record are
            # all the same text and the label never depends on the model. An
            # empty generation gets no notice: a header with nothing under it
            # is worse than the empty answer it would decorate.
            full_response = f"{LOW_CONFIDENCE_PREFIX}\n\n{full_response}"

        with langsmith.trace(name="Output Guardrail"):
            pii_hit, pii_type = detect_pii(full_response)
            injection_hit, injection_pattern = detect_prompt_injection(full_response)

        if pii_hit or injection_hit:
            guardrail_message = get_guardrail_message()
            await get_conversation_db().async_save_conversation(
                user_question=message,
                assistant_response=guardrail_message,
                session_id=session_id,
                response_source="guardrail",
                cache_hit=False,
                metadata={
                    "pii_type": pii_type if pii_hit else None,
                    "injection_pattern": injection_pattern if injection_hit else None,
                    **retrieval_metadata,
                },
            )
            async for event in _finish_response(
                answer=guardrail_message,
                start_time=start_time,
                response_source="guardrail",
                cache_hit=False,
            ):
                yield event
            return

        history_service.append_turn(history_key, message, full_response)
        with langsmith.trace(name="Cache Write + DB Save"):
            if CACHE_ENABLED and full_response and not outcome.low_confidence:
                PromptCacheService(get_redis_conn()).set_cached_response(
                    question_embedding,
                    message,
                    full_response,
                    sources,
                )
            await get_conversation_db().async_save_conversation(
                user_question=message,
                assistant_response=full_response,
                session_id=session_id,
                response_source="rag",
                cache_hit=False,
                metadata=retrieval_metadata,
            )

        async for event in _finish_response(
            answer=full_response,
            start_time=start_time,
            response_source="rag",
            cache_hit=False,
            sources=sources,
        ):
            yield event

    except asyncio.CancelledError:
        logger.info("Chat event stream cancelled for session=%s", session_id)
        raise
    except Exception:
        logger.exception("Chat event stream failed for session=%s", session_id)
        yield ErrorEvent()
