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
import json as _json
import os
import re as _re
from pathlib import Path as _Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.query_router import (
    RouterResult,
    _rule_route,
    route_query,
)
import src.query_router as query_router_module


HISTORY = [
    {"role": "user", "content": "排放係數從哪裡來？"},
    {"role": "assistant", "content": "排放係數可以在「係數庫」查看，並標示來源與適用年度。"},
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


class TestRuleRoutingSystemOperation:
    @pytest.mark.parametrize(
        "query",
        [
            "忘記密碼怎麼辦？",
            "登入失敗太多次會怎樣？",
            "多廠區要怎麼切換盤查邊界？",
            "如何審核數據條目？",
            "報告和清冊的格式是什麼？",
            "管理者可以看到所有廠區嗎？",
            "外購電力的排放代表什麼？",
            "冷媒逸散要怎麼計算？",
            "儀表板可以看到哪些資訊？",
        ],
    )
    def test_system_operation_questions_route_to_rag(self, query):
        assert _rule_route(query) == "rag"

    @pytest.mark.parametrize(
        "query",
        [
            "什麼是機器學習？",
            "推薦一本好看的小說",
            "明天要不要帶傘？",
            "幫我想一個生日派對的主題",
            "遊戲角色怎麼練等",
        ],
    )
    def test_general_questions_do_not_route_to_rag(self, query):
        assert _rule_route(query) != "rag"


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
            return_value=MagicMock(content="CarbonM 係數庫的排放係數適用哪一個年度？")
        )
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("那適用年度呢？碳排放係數要用哪一年的？", HISTORY)

        llm.ainvoke.assert_awaited_once()
        assert result.route == "rag"
        assert result.rewritten_query == "CarbonM 係數庫的排放係數適用哪一個年度？"
        prompt = llm.ainvoke.call_args[0][0]
        assert "排放係數從哪裡來？" in prompt
        assert "排放係數可以在「係數庫」查看，並標示來源與適用年度。" in prompt

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

        result = await route_query("碳排放係數的適用年度是哪一年？", HISTORY)

        llm.ainvoke.assert_awaited_once()
        llm.with_structured_output.assert_not_called()
        assert result.route == "rag"
        assert result.rewritten_query == "rewritten standalone query"

    @pytest.mark.asyncio
    async def test_rewrite_error_falls_back_to_original_message(self, monkeypatch):
        # "碳盤查建議值" contains 碳 → rule route = rag; rewrite error → original
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("碳盤查建議值是多少？", HISTORY)

        assert result.route == "rag"
        assert result.rewritten_query == "碳盤查建議值是多少？"

    @pytest.mark.asyncio
    async def test_llm_route_rewrites_in_single_call(self, monkeypatch):
        """LLM route (uncertain stage) does routing + rewriting in one call."""
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(
            return_value=RouterResult(
                route="rag",
                rewritten_query="CarbonM 的排放係數來源是什麼？",
                reasoning="追問排放係數的來源，改寫為獨立問題",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured_llm)
        # plain ainvoke should not be used in the LLM-route path
        llm.ainvoke = AsyncMock(side_effect=AssertionError("should not call plain ainvoke"))
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        result = await route_query("那適用年度呢？", HISTORY)

        # "那適用年度呢？" alone doesn't hit either rule stage -> uncertain -> LLM
        structured_llm.ainvoke.assert_awaited_once()
        llm.ainvoke.assert_not_called()
        assert result.route == "rag"
        assert result.rewritten_query == "CarbonM 的排放係數來源是什麼？"
        prompt = structured_llm.ainvoke.call_args[0][0]
        assert "排放係數從哪裡來？" in prompt


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
        result = await route_query("那適用年度呢？", HISTORY)

        assert isinstance(result, RouterResult)
        assert result.route in ("rag", "direct")
        assert result.rewritten_query != ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# Spec: query-routing-coverage
# ---------------------------------------------------------------------------


_ROOT = _Path(__file__).resolve().parents[1]

GENERAL_QUESTIONS = [
    "什麼是機器學習？", "推薦一本好看的小說", "明天要不要帶傘？", "幫我想一個生日派對的主題",
    "遊戲角色怎麼練等", "台灣的首都是哪裡？", "怎麼學好英文？", "如何修改 Gmail 的個人資料？",
    "Excel 報表怎麼做樞紐分析？", "iPhone 如何開啟兩步驟驗證？", "數據分析要學什麼？",
    "相關係數是什麼？", "如何查證網路謠言？", "哲學的研究範疇有哪些？", "幫我寫 Python 計算相關係數",
    "Excel 如何匯入數據？", "倉庫庫存盤查要注意什麼？", "GDP 的基準年是什麼意思？", "如何煮咖啡？",
    "原始數據要怎麼清理？", "Excel 如何批次匯入資料？", "Google 帳戶沒有收到啟用信怎麼辦？",
    "哲學的研究範疇一般如何劃分？", "兩步驟驗證和雙因素驗證有什麼差別？", "工廠廠區的消防規定有哪些？",
    "論文的佐證文件要怎麼準備？", "如何在 Excel 製作數據填報表？", "Google 表單可以設定填報截止時間嗎？",
    "Google 表單的填報人員怎麼設定？",
]


def _sample_manual_questions():
    from src.build_vector_store import extract_from_xlsx

    return [e["question"] for e in extract_from_xlsx(str(_ROOT / "examples" / "carbonm_sample_manual.xlsx"))]


def _frontend_suggestions():
    source = (_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    block = _re.search(r"const suggestions = \[(.*?)\];", source).group(1)
    return _re.findall(r'"([^"]+)"', block)


class TestRoutingCoverage:
    def test_rule_stage_covers_the_sample_manual(self):
        routes = [_rule_route(q) for q in _sample_manual_questions()]
        assert routes.count("rag") >= 42
        assert "direct" not in routes

    def test_eval_questions_are_never_direct(self):
        cases = _json.loads((_ROOT / "tests" / "rag_eval_data.json").read_text(encoding="utf-8"))
        assert [c["question"] for c in cases if _rule_route(c["question"]) == "direct"] == []

    def test_frontend_suggestions_are_never_direct(self):
        suggestions = _frontend_suggestions()
        assert suggestions
        assert [q for q in suggestions if _rule_route(q) == "direct"] == []

    @pytest.mark.parametrize("query", GENERAL_QUESTIONS)
    def test_general_questions_do_not_route_to_rag(self, query):
        assert _rule_route(query) != "rag"


class TestLlmRoutingPrompt:
    @pytest.mark.asyncio
    async def test_prompt_lists_features_and_routing_rules(self, monkeypatch):
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(
            return_value=RouterResult(route="rag", rewritten_query="q", reasoning="r")
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured_llm)
        monkeypatch.setattr(query_router_module, "get_llm", lambda: llm)

        await route_query("如何修改個人資料？", history=None)

        prompt = structured_llm.ainvoke.call_args[0][0]
        for feature in ["個人資料", "兩步驟驗證", "角色", "廠區", "排放係數", "數據填報", "報表", "第三方查證"]:
            assert feature in prompt, feature
        assert "沒有指明其他產品" in prompt
        assert "資安概念" in prompt
