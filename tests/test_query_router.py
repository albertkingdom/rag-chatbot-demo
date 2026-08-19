"""
Unit tests for the hybrid query router (src/query_router.py).

Structure:
    - Rule routing tests: pure logic, no mocking needed.
    - LLM routing tests: mock get_llm(), assert uncertain queries fall
      through to the LLM and structured output is used/returned correctly.
    - Query rewriting tests: mock get_llm(), cover history/no-history and
      "rule route + history calls rewrite-only, not routing" cases.
    - Integration tests: real LLM call, skipped without OPENROUTER_API_KEY.
"""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.query_router import (
    RouterResult,
    _rule_route,
    route_query,
)
import src.query_router as query_router_module


HISTORY = [
    {"role": "user", "content": "門檻值設定該如何填寫？"},
    {"role": "assistant", "content": "您需要填寫顯著性門檻、實質性門檻和排除門檻。"},
]


# ---------------------------------------------------------------------------
# Stage 1: rule-based routing (pure logic, no mocks)
# ---------------------------------------------------------------------------


class TestRuleRoutingRag:
    @pytest.mark.parametrize(
        "query",
        [
            "碳排放是什麼？",
            "如何計算碳足跡？",
            "什麼是 ESG？",
            "溫室氣體盤查登錄表單在哪裡？",
            "如何做碳盤查？",
            "淨零排放的目標是什麼？",
            "碳中和是什麼意思？",
            "How do I calculate carbon emissions?",
            "What does GHG stand for?",
            "scope 1 and scope 2 emissions",
            "CarbonM 是什麼系統？",
        ],
    )
    def test_carbon_keywords_route_to_rag(self, query):
        assert _rule_route(query) == "rag"


class TestRuleRoutingDirect:
    @pytest.mark.parametrize(
        "query",
        [
            "你好",
            "嗨",
            "哈囉",
            "Hello",
            "1+1等於多少？",
            "3 * 4 是多少",
            "請幫我翻譯這句話成英文",
            "Please translate this sentence into Chinese",
            "幫我寫一段 python 程式計算階乘",
            "write code to reverse a string",
            "講個笑話",
        ],
    )
    def test_general_patterns_route_to_direct(self, query):
        assert _rule_route(query) == "direct"


class TestRuleRoutingUncertain:
    @pytest.mark.parametrize(
        "query",
        [
            "永續發展跟企業有什麼關係",
            "今天天氣如何？",
            "推薦台北好吃的餐廳",
            "",
        ],
    )
    def test_ambiguous_queries_are_uncertain(self, query):
        assert _rule_route(query) == "uncertain"

    def test_rag_keyword_takes_priority_over_direct_pattern(self):
        # Contains both a carbon keyword and a coding-request pattern; rag wins.
        assert _rule_route("幫我寫一段程式計算碳排放") == "rag"


# ---------------------------------------------------------------------------
# Stage 2: LLM routing (mocked)
# ---------------------------------------------------------------------------


class TestLLMRouting:
    @pytest.mark.asyncio
    async def test_uncertain_query_falls_through_to_llm(self, monkeypatch):
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(
            return_value=RouterResult(
                route="direct",
                rewritten_query="永續發展跟企業有什麼關係",
                reasoning="一般性問題，非碳管理系統操作",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured_llm)
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("永續發展跟企業有什麼關係")

        llm.with_structured_output.assert_called_once_with(RouterResult)
        structured_llm.ainvoke.assert_awaited_once()
        assert result.route == "direct"

    @pytest.mark.asyncio
    async def test_structured_output_format(self, monkeypatch):
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(
            return_value=RouterResult(
                route="rag",
                rewritten_query="企業導入 ESG 有哪些碳管理面向？",
                reasoning="涉及企業與碳管理/ESG 的關聯",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured_llm)
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("永續發展跟企業有什麼關係")

        assert isinstance(result, RouterResult)
        assert result.route in ("rag", "direct")
        assert isinstance(result.rewritten_query, str)
        assert isinstance(result.reasoning, str)

    @pytest.mark.asyncio
    async def test_rule_routed_queries_never_call_structured_output(self, monkeypatch):
        # Rule stage should short-circuit before touching the LLM router path.
        llm = MagicMock()
        llm.with_structured_output = MagicMock(side_effect=AssertionError("should not be called"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("碳排放是什麼？")

        llm.with_structured_output.assert_not_called()
        assert result.route == "rag"

    @pytest.mark.asyncio
    async def test_llm_routing_error_falls_back_to_direct(self, monkeypatch):
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured_llm)
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("永續發展跟企業有什麼關係")

        assert result.route == "direct"
        assert result.rewritten_query == "永續發展跟企業有什麼關係"


# ---------------------------------------------------------------------------
# Query rewriting (mocked)
# ---------------------------------------------------------------------------


class TestQueryRewriting:
    @pytest.mark.asyncio
    async def test_rag_rule_route_with_history_rewrites_query(self, monkeypatch):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(
            return_value=MagicMock(content="顯著性門檻、實質性門檻和排除門檻的建議值分別為3、5、0.5嗎？")
        )
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("那建議值呢？碳盤查門檻要填多少？", HISTORY)

        llm.ainvoke.assert_awaited_once()
        assert result.route == "rag"
        assert result.rewritten_query == "顯著性門檻、實質性門檻和排除門檻的建議值分別為3、5、0.5嗎？"
        prompt = llm.ainvoke.call_args[0][0]
        assert "門檻值設定該如何填寫？" in prompt
        assert "您需要填寫顯著性門檻、實質性門檻和排除門檻。" in prompt

    @pytest.mark.asyncio
    async def test_direct_rule_route_without_history_returns_original(self, monkeypatch):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=AssertionError("should not be called without history"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("你好", history=None)

        llm.ainvoke.assert_not_called()
        assert result.route == "direct"
        assert result.rewritten_query == "你好"

    @pytest.mark.asyncio
    async def test_direct_rule_route_with_empty_history_returns_original(self, monkeypatch):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=AssertionError("should not be called with empty history"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("1+1等於多少？", history=[])

        llm.ainvoke.assert_not_called()
        assert result.rewritten_query == "1+1等於多少？"

    @pytest.mark.asyncio
    async def test_rule_route_with_history_only_calls_rewrite_not_routing(self, monkeypatch):
        """Rule route hit + history should call the LLM only for rewriting
        (plain .ainvoke), never touching .with_structured_output (routing)."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="rewritten standalone query"))
        llm.with_structured_output = MagicMock(side_effect=AssertionError("should not route via LLM"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("碳盤查門檻的建議值是多少？", HISTORY)

        llm.ainvoke.assert_awaited_once()
        llm.with_structured_output.assert_not_called()
        assert result.route == "rag"
        assert result.rewritten_query == "rewritten standalone query"

    @pytest.mark.asyncio
    async def test_rewrite_error_falls_back_to_original_message(self, monkeypatch):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("那建議值呢？", HISTORY)

        assert result.route == "rag"
        assert result.rewritten_query == "那建議值呢？"

    @pytest.mark.asyncio
    async def test_llm_route_rewrites_in_single_call(self, monkeypatch):
        """LLM route (uncertain stage) does routing + rewriting in one call."""
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(
            return_value=RouterResult(
                route="rag",
                rewritten_query="碳盤查的門檻值建議設定為多少？",
                reasoning="追問碳盤查門檻設定，改寫為獨立問題",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured_llm)
        # plain ainvoke should not be used in the LLM-route path
        llm.ainvoke = AsyncMock(side_effect=AssertionError("should not call plain ainvoke"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("那建議值呢？", HISTORY)

        # "那建議值呢？" alone doesn't hit either rule stage -> uncertain -> LLM
        structured_llm.ainvoke.assert_awaited_once()
        llm.ainvoke.assert_not_called()
        assert result.route == "rag"
        assert result.rewritten_query == "碳盤查的門檻值建議設定為多少？"
        prompt = structured_llm.ainvoke.call_args[0][0]
        assert "門檻值設定該如何填寫？" in prompt


# ---------------------------------------------------------------------------
# Integration tests (real LLM, skipped without API key)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
class TestQueryRouterIntegration:
    @pytest.mark.asyncio
    async def test_real_llm_call_with_uncertain_query(self):
        result = await route_query("永續發展跟企業有什麼關係")

        assert isinstance(result, RouterResult)
        assert result.route in ("rag", "direct")
        assert isinstance(result.rewritten_query, str) and result.rewritten_query
        assert isinstance(result.reasoning, str)

    @pytest.mark.asyncio
    async def test_real_llm_call_rewrites_followup_with_history(self):
        result = await route_query("那建議值呢？", HISTORY)

        assert isinstance(result, RouterResult)
        assert result.route in ("rag", "direct")
        assert result.rewritten_query != ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
