"""
Unit tests for IntentClassifier's use of conversation history.

Unlike test_intent_classifier.py (which hits the real LLM and is skipped
without an API key), these tests mock the structured LLM so they run
offline and assert on the exact prompt text built by classify().
"""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.intent_classifier import IntentClassifier, IntentResult


@pytest.fixture
def classifier(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-tests")
    instance = IntentClassifier()
    instance.structured_llm = MagicMock()
    instance.structured_llm.ainvoke = AsyncMock(
        return_value=IntentResult(relevant=True, confidence=0.9, reason="test")
    )
    return instance


HISTORY = [
    {"role": "user", "content": "門檻值設定該如何填寫？"},
    {"role": "assistant", "content": "您需要填寫顯著性門檻、實質性門檻和排除門檻。"},
]


class TestClassifyHistoryPrompt:
    @pytest.mark.asyncio
    async def test_history_block_included_when_non_empty(self, classifier):
        await classifier.classify("您確定顯著性門檻、實質性門檻和排除門檻的建議值分別為3、5、0.5嗎？", HISTORY)

        prompt = classifier.structured_llm.ainvoke.call_args[0][0]
        assert "最近對話：" in prompt
        assert "門檻值設定該如何填寫？" in prompt
        assert "您需要填寫顯著性門檻、實質性門檻和排除門檻。" in prompt

    @pytest.mark.asyncio
    async def test_no_history_block_for_empty_list(self, classifier):
        await classifier.classify("如何重設密碼？", [])

        prompt = classifier.structured_llm.ainvoke.call_args[0][0]
        assert "最近對話：" not in prompt

    @pytest.mark.asyncio
    async def test_no_history_block_when_omitted(self, classifier):
        await classifier.classify("如何重設密碼？")

        prompt = classifier.structured_llm.ainvoke.call_args[0][0]
        assert "最近對話：" not in prompt


class TestIsRelevantForwardsHistory:
    @pytest.mark.asyncio
    async def test_is_relevant_forwards_history_to_classify(self, classifier, monkeypatch):
        classify_mock = AsyncMock(return_value={"relevant": True, "confidence": 0.9, "reason": "x"})
        monkeypatch.setattr(classifier, "classify", classify_mock)

        await classifier.is_relevant("您確定嗎？", history=HISTORY)

        classify_mock.assert_called_once_with("您確定嗎？", HISTORY)
