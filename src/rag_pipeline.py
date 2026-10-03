"""RAG domain helpers: prompt, context/history formatting, and
retrieval/generation chain assembly.

Business logic lives here; singletons are obtained from src.services. This
module has no module-level side effects beyond defining the QA prompt.
"""

import asyncio
from datetime import datetime, timezone, timedelta

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langsmith import traceable

from .config import BM25_TOP_N, RRF_K, VECTOR_TOP_N
from .rerank_stage import rerank_candidates
from .services import (
    get_hybrid_retriever,
    get_llm,
    get_reranker_model,
)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a helpful assistant with expertise in carbon management (CarbonM).\n"
     "Today is {today}.\n"
     "Always reply in Traditional Chinese (繁體中文) unless the user explicitly uses another language.\n"
     "You cannot browse the internet, execute code, or access external systems.\n"
     "Do not provide medical, legal, or financial advice.\n"
     "Use the provided context to answer the user's question. "
     "If the context is not relevant or empty, answer based on your general knowledge.\n"
     "Keep the answer concise and helpful."),
    ("human",
     "{history}\n\n"
     "Context:\n{context}\n\n"
     "Question:\n{question}"),
])


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


_TW_TZ = timezone(timedelta(hours=8))

_WEEKDAY_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _today_str() -> str:
    now = datetime.now(_TW_TZ)
    weekday = _WEEKDAY_ZH[now.weekday()]
    return f"{now.strftime('%Y-%m-%d')} {weekday}"


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

        async def _rerank(payload: dict) -> dict:
            query = payload["query"]
            candidates = payload["candidates"]
            fusion_metadata = payload["fusion_metadata"]
            ranked = await asyncio.to_thread(
                rerank_candidates, query, candidates, get_reranker_model().score
            )
            formatted = _format_docs(ranked)
            return {
                **formatted,
                "sources": _format_sources(ranked["docs"]),
                "docs": ranked["docs"],
                "rerank_scores": ranked["rerank_scores"],
                "fusion_metadata": fusion_metadata,
            }

        _retrieval_chain = (
            RunnableLambda(_retrieve, name="Hybrid Retrieve")
            | RunnableLambda(_rerank, name="Rerank (BGE)")
        )
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
