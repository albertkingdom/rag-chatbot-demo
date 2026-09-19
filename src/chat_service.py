"""Transport-independent chat application service.

The service accepts plain identifiers instead of a Gradio/FastAPI request and
emits typed events that can be serialized by any UI adapter.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

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
from .rag_pipeline import _STREAM_CHUNK_DELAY, _STREAM_CHUNK_SIZE, _today_str, format_history
from .rag_pipeline import get_generation_chain, get_retrieval_chain
from .retrieval_grader import grade_documents, rewrite_for_retry
from .services import get_embeddings, get_redis_conn

logger = logging.getLogger("chat_service")


async def _answer_events(text: str) -> AsyncGenerator[DeltaEvent, None]:
    """Emit append-only chunks rather than Gradio's accumulated strings."""
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


@traceable(name="RAG_DEMO Chat Events")
async def chat_event_stream(
    message: str,
    *,
    history_key: str | None,
    session_id: str | None,
    fallback_history: list[dict] | None = None,
) -> AsyncGenerator[ChatStreamEvent, None]:
    """Run one chat request and emit transport-neutral events.

    `history_key` and `session_id` must be derived by the server. The browser
    never supplies either value directly.
    """
    history_service = ChatHistoryService(get_redis_conn())
    history = history_service.get_history(history_key)
    if not history:
        history = fallback_history or []

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
        context_bundle = await get_retrieval_chain().ainvoke(rewritten_query)

        retry_count = 0
        grade_passed = grade_documents(context_bundle.get("rerank_scores", []))
        while not grade_passed and retry_count < RETRIEVAL_MAX_RETRIES:
            retry_count += 1
            retry_query = await rewrite_for_retry(
                rewritten_query, context_bundle.get("docs", [])
            )
            context_bundle = await get_retrieval_chain().ainvoke(retry_query)
            grade_passed = grade_documents(context_bundle.get("rerank_scores", []))

        context = context_bundle.get("context", "")
        sources = context_bundle.get("sources", [])

        yield StatusEvent(stage="generating", message="正在生成回答…")
        history_text = format_history(history)
        async for chunk in get_generation_chain().astream(
            {
                "context": context,
                "question": message,
                "history": history_text,
                "today": _today_str(),
            }
        ):
            full_response += chunk

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
            if CACHE_ENABLED and full_response:
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
