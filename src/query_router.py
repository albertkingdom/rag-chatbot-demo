"""
Hybrid Query Router for the Agentic RAG pipeline.

Decides whether a user query should go through RAG retrieval ("rag") or be
answered directly by the LLM ("direct"). Replaces the old intent_classifier,
which only ever filtered off-topic questions — this router additionally
absorbs query rewriting (turning a follow-up question into a standalone
one), replacing rag_pipeline.rewrite_query().

Two-stage hybrid routing:
    Stage 1 (zero latency): keyword/pattern rules decide the obvious cases.
    Stage 2 (LLM fallback): only "uncertain" queries reach the LLM, which
        returns route + rewritten_query + reasoning in a single structured
        output call.
"""
import logging
import re
from typing import Literal, Optional

from langsmith import traceable
from pydantic import BaseModel, Field

from .services import get_llm

logger = logging.getLogger("query_router")


class RouterResult(BaseModel):
    route: Literal["rag", "direct"] = Field(description="Chosen route: 'rag' or 'direct'")
    rewritten_query: str = Field(description="Rewritten standalone query, or original if no rewrite needed")
    reasoning: str = Field(description="Brief routing rationale, for logging/debug")


# ---------------------------------------------------------------------------
# Stage 1: rule-based routing constants
# ---------------------------------------------------------------------------

# Carbon-management domain keywords. Matched case-insensitively as substrings
# of the (lower-cased) query. Keep this list the single source of truth for
# "this query needs the knowledge base".
RAG_KEYWORDS = [
    # Chinese
    "碳", "esg", "溫室氣體", "碳足跡", "碳排", "碳盤查", "碳中和", "淨零",
    "排放係數", "排放源", "盤查邊界", "活動數據", "固定源", "移動源",
    "生質能源", "汽電共生", "環境部登錄", "碳權", "碳交易",
    # English / mixed
    "carbon", "emission", "ghg", "carbonm",
    "scope 1", "scope 2", "scope 3", "scope1", "scope2", "scope3",
]

# Common general-purpose query patterns that clearly do NOT need retrieval.
# Regex, matched case-insensitively with re.search against the raw query.
DIRECT_PATTERNS = [
    r"^\s*(你好|嗨|哈囉|hi|hello|hey)\b",           # greetings
    r"(謝謝|感謝|thank you|thanks)",                  # thanks
    r"\d+\s*[\+\-\*×x/]\s*\d+",                       # simple math, e.g. "1+1"
    r"(翻譯|translate)",                              # translation requests
    r"(寫.{0,6}(程式|code)|write.{0,10}code|debug|演算法|function\s*\(|python|javascript)",  # coding
    r"(講個笑話|說個笑話|tell me a joke|joke)",        # jokes
]

_RAG_KEYWORDS_LOWER = [kw.lower() for kw in RAG_KEYWORDS]
_DIRECT_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in DIRECT_PATTERNS]


def _rule_route(message: str) -> str:
    """Fast, zero-latency rule-based routing.

    Returns "rag", "direct", or "uncertain" (fall through to the LLM stage).
    Domain keywords are checked first so a query that happens to also match
    a general pattern (e.g. mentions both carbon and code) still routes to
    "rag".
    """
    if not message:
        return "uncertain"

    lowered = message.lower()

    if any(kw in lowered for kw in _RAG_KEYWORDS_LOWER):
        return "rag"

    if any(pattern.search(message) for pattern in _DIRECT_PATTERNS_COMPILED):
        return "direct"

    return "uncertain"


# ---------------------------------------------------------------------------
# History helpers (duplicated locally, not imported from rag_pipeline, to
# avoid a circular import: rag_pipeline will import route_query from this
# module).
# ---------------------------------------------------------------------------


def _parse_history_turns(history: list, max_turns: int = 3) -> list:
    if not history:
        return []
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


def _format_history_block(history: list, max_turns: int = 3) -> str:
    recent = _parse_history_turns(history, max_turns)
    if not recent:
        return ""
    lines = [f"User: {u}\nAssistant: {a}" for u, a in recent]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 1 follow-up: rewrite-only LLM call (route already decided by rules)
# ---------------------------------------------------------------------------


@traceable(name="Query Rewriting")
async def _rewrite_only(message: str, history: list) -> str:
    """Rewrites a follow-up question into a standalone query, without
    touching routing. Used when the rule stage already decided the route but
    there is conversation history to fold in.
    """
    history_block = _format_history_block(history)
    if not history_block:
        return message

    prompt = f"""Given the conversation history and the follow-up question, rewrite the question into a standalone question that can be understood without the conversation context.
If the question is already complete and self-contained, return it as-is.

Conversation history:
{history_block}

Follow-up question: {message}

Rewritten standalone question:"""

    try:
        llm = get_llm()
        response = await llm.ainvoke(prompt)
        rewritten = response.content.strip()
        logger.info("[Query Router] Rewrite-only. Original: %s | Rewritten: %s", message, rewritten)
        return rewritten or message
    except Exception as e:
        logger.warning("[Query Router] Rewrite-only ERROR: %s", e)
        return message


# ---------------------------------------------------------------------------
# Stage 2: LLM routing + rewriting (single call, only for "uncertain")
# ---------------------------------------------------------------------------


async def _llm_route(message: str, history: list) -> RouterResult:
    history_block = _format_history_block(history)
    history_section = f"\nConversation history:\n{history_block}\n" if history_block else ""

    prompt = f"""You route a user query for a carbon-management assistant (CarbonM) that can also chat generally.
Decide "rag" if the query needs the carbon-management knowledge base (碳盤查、排放、ESG、CarbonM 系統操作等), otherwise "direct".
If there is conversation history and the query is a follow-up, rewrite it into a standalone query; otherwise return it unchanged.
{history_section}
Query: {message}"""

    try:
        structured_llm = get_llm().with_structured_output(RouterResult)
        result: RouterResult = await structured_llm.ainvoke(prompt)
        logger.info("[Query Router] LLM route=%s rewritten=%s", result.route, result.rewritten_query)
        return result
    except Exception as e:
        logger.warning("[Query Router] LLM routing ERROR: %s", e)
        return RouterResult(
            route="direct",
            rewritten_query=message,
            reasoning=f"LLM 路由發生錯誤，回退為 direct：{e}",
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


@traceable(name="Query Routing")
async def route_query(message: str, history: Optional[list] = None) -> RouterResult:
    """Hybrid router: rule-based fast path first, LLM fallback for the rest.

    Regardless of which stage decides the route, the returned rewritten_query
    is a standalone version of `message` when there is conversation history
    to fold in (or `message` unchanged otherwise).
    """
    stage = _rule_route(message)

    if stage == "rag":
        rewritten = await _rewrite_only(message, history)
        return RouterResult(route="rag", rewritten_query=rewritten, reasoning="規則路由：命中碳管理關鍵字")

    if stage == "direct":
        rewritten = await _rewrite_only(message, history)
        return RouterResult(route="direct", rewritten_query=rewritten, reasoning="規則路由：命中一般問答模式")

    return await _llm_route(message, history)
