"""RAG pipeline: prompt, context formatting, query rewriting, chat stream,
and the retrieval/generation chain assembly.

Business logic lives here; singletons are obtained from src.services. This
module has no module-level side effects beyond defining the QA prompt.
"""

import asyncio
import logging
import time
from typing import AsyncGenerator

import gradio as gr
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda
from langsmith import traceable

from .access_control import SESSION_COOKIE
from .cache_service import PromptCacheService
from .chat_history_service import ChatHistoryService
from .config import (
    BM25_TOP_N,
    CACHE_ENABLED,
    CACHE_SIMILARITY_THRESHOLD,
    GRADE_SCORE_THRESHOLD,
    RETRIEVAL_MAX_RETRIES,
    RRF_K,
    VECTOR_TOP_N,
)
from .conversation_db import get_conversation_db
from .guardrails import (
    detect_pii,
    detect_prompt_injection,
    get_guardrail_message,
)
from .query_router import route_query
from .rerank_stage import rerank_candidates
from .retrieval_grader import grade_documents, rewrite_for_retry
from .services import (
    get_embeddings,
    get_hybrid_retriever,
    get_llm,
    get_redis_conn,
    get_reranker_model,
)

logger = logging.getLogger("rag_pipeline")


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_QA_PROMPT = PromptTemplate.from_template(
    """You are a helpful assistant with expertise in carbon management.
        Use the following context to answer the user's question.
        If the context is not relevant or empty, answer based on your general knowledge.
        Keep the answer concise and helpful.
        {history}

        Context:
        {context}

        Question:
        {question}

        Helpful Answer:"""
)


# ---------------------------------------------------------------------------
# Retrieval / generation chains
# ---------------------------------------------------------------------------


_retrieval_chain = None
_generation_chain = None


@traceable(name="Prepare Context")
def _format_docs(outputs):
    docs = outputs["docs"]
    contexts = [doc.metadata.get("answer", "") for doc in docs if doc.metadata.get("answer")]
    return {"context": "\n\n".join(contexts), "contexts": contexts}


def _format_sources(docs) -> list[str]:
    sources = []
    for doc in docs:
        question = doc.metadata.get("text") or doc.page_content
        answer = doc.metadata.get("answer")
        if question and answer:
            source = f"Q: {question} A: {answer}"
            if len(source) > 100:
                source = source[:100] + "..."
        else:
            source = doc.metadata.get("doc_id")
        if source and source not in sources:
            sources.append(source)
    return sources


def _append_source_block(answer: str, sources: list[str]) -> str:
    if not sources:
        return answer
    source_lines = "\n".join(f"- {source}" for source in sources)
    return f"{answer}\n\n參考資料：\n{source_lines}"


def _append_timing_line(text: str, elapsed_seconds: float) -> str:
    return f"{text}\n\n回應時間：{elapsed_seconds:.1f} 秒"


def get_retrieval_chain():
    # Deliberately NOT locked: construction only wraps providers already
    # protected by services.py locks (get_hybrid_retriever, get_reranker_model)
    # into a RunnableLambda pipe. The build is idempotent and microsecond-
    # cost; a race worst case builds an extra functionally-equivalent pipe
    # object (immediately GC'd) — no duplicate expensive I/O, no state change.
    global _retrieval_chain
    if _retrieval_chain is None:
        retriever = get_hybrid_retriever()

        async def _retrieve(query: str) -> dict:
            result = await retriever.retrieve(
                query,
                vector_top_n=VECTOR_TOP_N,
                bm25_top_n=BM25_TOP_N,
                rrf_k=RRF_K,
            )
            return {"query": query, **result}

        def _rerank(payload: dict) -> dict:
            query = payload["query"]
            candidates = payload["candidates"]
            fusion_metadata = payload["fusion_metadata"]
            ranked = rerank_candidates(query, candidates, get_reranker_model().score)
            formatted = _format_docs(ranked)
            return {
                **formatted,
                "sources": _format_sources(ranked["docs"]),
                "docs": ranked["docs"],
                "rerank_scores": ranked["rerank_scores"],
                "fusion_metadata": fusion_metadata,
            }

        _retrieval_chain = RunnableLambda(_retrieve) | RunnableLambda(_rerank)
    return _retrieval_chain


def get_generation_chain():
    # Deliberately NOT locked: construction only wraps providers already
    # protected by services.py locks (get_llm) into a prompt|llm|parser pipe.
    # Idempotent, microsecond-cost; race worst case builds an extra
    # equivalent pipe — no duplicate expensive I/O, no state change.
    global _generation_chain
    if _generation_chain is None:
        _generation_chain = _QA_PROMPT | get_llm() | StrOutputParser()
    return _generation_chain


# ---------------------------------------------------------------------------
# Conversation history helpers
# ---------------------------------------------------------------------------


def _parse_history_turns(history: list, max_turns: int = 3) -> list:
    turns = []
    i = 0
    while i < len(history) - 1:
        msg = history[i]
        next_msg = history[i + 1]
        if isinstance(msg, dict) and isinstance(next_msg, dict):
            if msg.get("role") == "user" and next_msg.get("role") == "assistant":
                turns.append((msg["content"], next_msg["content"]))
                i += 2
                continue
        i += 1
    return turns[-max_turns:] if turns else []


def format_history(history: list, max_turns: int = 3) -> str:
    if not history:
        return ""
    recent = _parse_history_turns(history, max_turns)
    if not recent:
        return ""
    lines = ["\n\n        Previous conversation:"]
    for user_msg, bot_msg in recent:
        lines.append(f"        User: {user_msg}")
        lines.append(f"        Assistant: {bot_msg}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chat stream
# ---------------------------------------------------------------------------


@traceable(name="RAG_DEMO Chat")
async def chat_stream(message: str, history: list, request: gr.Request = None) -> AsyncGenerator[str, None]:
    """Handles the entire RAG chain lifecycle for a single chat request with caching."""
    session_id = request.session_hash if request else None
    history_key = (
        (request.cookies.get(SESSION_COOKIE) or request.session_hash) if request else None
    )
    chat_history_service = ChatHistoryService(get_redis_conn())
    stored_history = chat_history_service.get_history(history_key)
    if stored_history:
        history = stored_history
    full_response = ""
    start_time = time.monotonic()
    try:
        # Step 0a: Input guardrail - block prompt injection before hitting LLM
        injection_hit, _ = detect_prompt_injection(message)
        if injection_hit:
            guardrail_message = get_guardrail_message()
            db = get_conversation_db()
            await db.async_save_conversation(
                user_question=message,
                assistant_response=guardrail_message,
                session_id=session_id,
                response_source="guardrail",
                metadata={"injection_pattern": "input_injection"},
            )
            elapsed = time.monotonic() - start_time
            guardrail_message_with_timing = _append_timing_line(guardrail_message, elapsed)
            for i in range(1, len(guardrail_message_with_timing) + 1):
                yield guardrail_message_with_timing[:i]
                await asyncio.sleep(0.005)
            return

        # Step 0b: Route - decide "rag" vs "direct", and get a standalone query
        router_result = await route_query(message, history)
        rewritten_query = router_result.rewritten_query
        logger.info(
            "[Router] route=%s rewritten=%s reasoning=%s",
            router_result.route, rewritten_query, router_result.reasoning,
        )

        # -------------------------------------------------------------
        # Direct path: no retrieval, no cache — straight to generation.
        # -------------------------------------------------------------
        if router_result.route == "direct":
            history_text = format_history(history)
            async for chunk in get_generation_chain().astream(
                {"context": "", "question": message, "history": history_text}
            ):
                full_response += chunk

            pii_hit, pii_type = detect_pii(full_response)
            injection_hit, injection_pattern = detect_prompt_injection(full_response)

            if pii_hit or injection_hit:
                guardrail_message = get_guardrail_message()
                logger.warning(
                    "[Guardrail] Blocked response: pii=%s injection=%s",
                    pii_type if pii_hit else None,
                    injection_pattern if injection_hit else None,
                )
                db = get_conversation_db()
                await db.async_save_conversation(
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
                elapsed = time.monotonic() - start_time
                guardrail_message_with_timing = _append_timing_line(guardrail_message, elapsed)
                for i in range(1, len(guardrail_message_with_timing) + 1):
                    yield guardrail_message_with_timing[:i]
                    await asyncio.sleep(0.005)
                return

            elapsed = time.monotonic() - start_time
            response_final = _append_timing_line(full_response, elapsed)
            for i in range(1, len(response_final) + 1):
                yield response_final[:i]
                await asyncio.sleep(0.005)

            chat_history_service.append_turn(history_key, message, full_response)

            db = get_conversation_db()
            await db.async_save_conversation(
                user_question=message,
                assistant_response=full_response,
                session_id=session_id,
                response_source="direct",
                cache_hit=False,
            )
            return

        # -------------------------------------------------------------
        # RAG path
        # -------------------------------------------------------------
        yield "正在從知識庫檢索相關資訊..."
        embeddings = get_embeddings()

        # Generate embedding for the rewritten query (needed for both cache and RAG)
        question_embedding = await embeddings.aembed_query(rewritten_query)

        # Try to get cached response if cache is enabled
        if CACHE_ENABLED:
            cache_service = PromptCacheService(get_redis_conn())
            cached_response = cache_service.get_cached_response(
                question_embedding,
                threshold=CACHE_SIMILARITY_THRESHOLD,
            )

            if cached_response:
                logger.info(
                    "Cache hit! Similarity: %.3f, Hit count: %s",
                    cached_response["similarity"], cached_response["hit_count"],
                )
                cached_answer = cached_response["answer"]
                cached_sources = cached_response.get("sources", [])

                pii_hit, pii_type = detect_pii(cached_answer)
                injection_hit, injection_pattern = detect_prompt_injection(cached_answer)
                if pii_hit or injection_hit:
                    guardrail_message = get_guardrail_message()
                    logger.warning(
                        "[Guardrail] Blocked cached response: pii=%s injection=%s sim=%s",
                        pii_type if pii_hit else None,
                        injection_pattern if injection_hit else None,
                        cached_response.get("similarity"),
                    )
                    db = get_conversation_db()
                    await db.async_save_conversation(
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
                    elapsed = time.monotonic() - start_time
                    guardrail_message_with_timing = _append_timing_line(guardrail_message, elapsed)
                    for i in range(1, len(guardrail_message_with_timing) + 1):
                        yield guardrail_message_with_timing[:i]
                        await asyncio.sleep(0.005)
                    return

                # Record the CACHED conversation to MongoDB
                db = get_conversation_db()
                await db.async_save_conversation(
                    user_question=message,
                    assistant_response=cached_answer,
                    session_id=session_id,
                    response_source="cache",
                    cache_hit=True,
                    cache_similarity=cached_response["similarity"],
                )

                # Stream the cached response for consistent UI experience
                cached_response_with_sources = _append_source_block(cached_answer, cached_sources)
                elapsed = time.monotonic() - start_time
                cached_response_final = _append_timing_line(cached_response_with_sources, elapsed)
                for i in range(1, len(cached_response_final) + 1):
                    yield cached_response_final[:i]
                    await asyncio.sleep(0.005)
                chat_history_service.append_turn(history_key, message, cached_answer)
                return

        # Cache miss or cache disabled - proceed with normal RAG pipeline
        yield "正在優化檢索結果 (Reranking)..."
        context_bundle = await get_retrieval_chain().ainvoke(rewritten_query)

        # Grade retrieval quality; on a low-quality grade, rewrite the query
        # and retry once (bounded by RETRIEVAL_MAX_RETRIES).
        retry_count = 0
        grade_passed = grade_documents(context_bundle.get("rerank_scores", []))
        while not grade_passed and retry_count < RETRIEVAL_MAX_RETRIES:
            retry_count += 1
            retry_query = await rewrite_for_retry(
                rewritten_query, context_bundle.get("docs", [])
            )
            logger.info(
                "[Retrieval Retry] attempt=%s query=%s", retry_count, retry_query
            )
            context_bundle = await get_retrieval_chain().ainvoke(retry_query)
            grade_passed = grade_documents(context_bundle.get("rerank_scores", []))

        context = context_bundle.get("context", "")
        contexts = context_bundle.get("contexts", [])
        sources = context_bundle.get("sources", [])

        yield "正在生成回答..."
        history_text = format_history(history)
        async for chunk in get_generation_chain().astream(
            {"context": context, "question": message, "history": history_text}
        ):
            full_response += chunk

        pii_hit, pii_type = detect_pii(full_response)
        injection_hit, injection_pattern = detect_prompt_injection(full_response)

        if pii_hit or injection_hit:
            guardrail_message = get_guardrail_message()
            logger.warning(
                "[Guardrail] Blocked response: pii=%s injection=%s",
                pii_type if pii_hit else None,
                injection_pattern if injection_hit else None,
            )
            db = get_conversation_db()
            await db.async_save_conversation(
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
            elapsed = time.monotonic() - start_time
            guardrail_message_with_timing = _append_timing_line(guardrail_message, elapsed)
            for i in range(1, len(guardrail_message_with_timing) + 1):
                yield guardrail_message_with_timing[:i]
                await asyncio.sleep(0.005)
            return

        response_with_sources = _append_source_block(full_response, sources)
        elapsed = time.monotonic() - start_time
        response_final = _append_timing_line(response_with_sources, elapsed)
        for i in range(1, len(response_final) + 1):
            yield response_final[:i]
            await asyncio.sleep(0.005)

        chat_history_service.append_turn(history_key, message, full_response)

        # Store the generated response in cache
        if CACHE_ENABLED and full_response:
            cache_service = PromptCacheService(get_redis_conn())
            cache_service.set_cached_response(
                question_embedding,
                message,
                full_response,
                sources,
            )
            logger.info("Response cached for question: %s...", message[:50])

        # Record the RAG conversation to MongoDB
        db = get_conversation_db()
        await db.async_save_conversation(
            user_question=message,
            assistant_response=full_response,
            session_id=session_id,
            response_source="rag",
            cache_hit=False,
        )

    except Exception as e:
        logger.error("An error occurred during chat stream: %s", e)
        yield f"An error occurred: {e}"
