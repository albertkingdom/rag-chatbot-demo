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

# Low-confidence retrieval handling (see openspec change
# handle-low-confidence-retrieval).
#
# LOW_CONFIDENCE_PREFIX is prepended to the answer by chat_service, so the
# label never depends on the model complying. It must not trip the input /
# output guardrail patterns (guarded by tests/test_rag_prompt.py).
LOW_CONFIDENCE_PREFIX = (
    "知識庫中查無與此問題直接相關的資料，"
    "以下為一般性參考說明，實際操作請以官方操作手冊或系統畫面為準。"
)

# LOW_CONFIDENCE_NOTE is appended to the system message (via the
# `confidence_note` prompt variable) only for low-confidence retrievals.
# It starts with a newline on purpose: the template appends the variable
# directly after the last sentence, so an empty note renders byte-for-byte
# like the prompt did before this variable existed.
LOW_CONFIDENCE_NOTE = (
    "\n檢索到的資料與問題的相關性低。"
    "只有在資料中明確寫出能回答問題的內容時才可引用。"
    "若問題是關於 CarbonM 產品本身（功能、介面、版本、價格、是否支援某項目等）而資料中沒有寫，"
    "請明確說明無法確認，不要推測，也不要斷言有或沒有；"
    "只有一般常識性的問題才可以用一般知識簡短回答。"
    "不得杜撰 CarbonM 系統的具體操作步驟、畫面或選單名稱；"
    "不需要重複說明知識庫查無資料，系統會自行加註。"
)

# `confidence_note` defaults to "" through partial(): callers that do not pass
# it (the direct-answer path, tests/evaluate_rag.py) keep working unchanged,
# and a value passed at invoke time overrides the default.
_QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a helpful assistant with expertise in carbon management (CarbonM).\n"
     "Today is {today}.\n"
     "Always reply in Traditional Chinese (繁體中文) unless the user explicitly uses another language.\n"
     "You cannot browse the internet, execute code, or access external systems.\n"
     "Do not provide medical, legal, or financial advice.\n"
     "Use the provided context to answer the user's question. "
     "If the context is not relevant or empty, answer based on your general knowledge.\n"
     "Keep the answer concise and helpful.{confidence_note}"),
    ("human",
     "{history}\n\n"
     "Context:\n{context}\n\n"
     "Question:\n{question}"),
]).partial(confidence_note="")


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
