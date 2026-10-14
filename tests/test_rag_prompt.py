"""Prompt rendering for the low-confidence note and the fixed answer prefix.

Hermetic: no LLM is called; the prompt is rendered only.
"""
from langchain_core.runnables import RunnableLambda

from src.guardrails import detect_pii, detect_prompt_injection
from src.rag_pipeline import _QA_PROMPT, LOW_CONFIDENCE_NOTE, LOW_CONFIDENCE_PREFIX

# The system message exactly as it rendered before `confidence_note` existed.
_SYSTEM_BEFORE = (
    "You are a helpful assistant with expertise in carbon management (CarbonM).\n"
    "Today is 2026-10-14 星期三.\n"
    "Always reply in Traditional Chinese (繁體中文) unless the user explicitly uses another language.\n"
    "You cannot browse the internet, execute code, or access external systems.\n"
    "Do not provide medical, legal, or financial advice.\n"
    "Use the provided context to answer the user's question. "
    "If the context is not relevant or empty, answer based on your general knowledge.\n"
    "Keep the answer concise and helpful."
)
_HUMAN_BEFORE = "H\n\nContext:\nC\n\nQuestion:\nQ"

_BASE_INPUT = {"today": "2026-10-14 星期三", "history": "H", "context": "C", "question": "Q"}


def _render(**extra):
    system, human = _QA_PROMPT.format_messages(**_BASE_INPUT, **extra)
    return system.content, human.content


class TestConfidenceNoteVariable:
    def test_omitted_note_renders_identically_to_the_prompt_before_the_change(self):
        system, human = _render()

        assert system == _SYSTEM_BEFORE
        assert human == _HUMAN_BEFORE

    def test_empty_note_renders_identically_to_the_prompt_before_the_change(self):
        system, human = _render(confidence_note="")

        assert system == _SYSTEM_BEFORE
        assert human == _HUMAN_BEFORE

    def test_low_confidence_note_is_appended_to_the_system_message(self):
        system, human = _render(confidence_note=LOW_CONFIDENCE_NOTE)

        assert system == _SYSTEM_BEFORE + LOW_CONFIDENCE_NOTE
        assert "不得杜撰" in system
        assert human == _HUMAN_BEFORE

    def test_note_tells_the_model_not_to_assert_unsupported_product_facts(self):
        # Found in the real-provider run: with a weaker note the model sometimes
        # asserted "CarbonM currently has no mobile app" without any evidence.
        assert "無法確認" in LOW_CONFIDENCE_NOTE
        assert "不要推測" in LOW_CONFIDENCE_NOTE
        assert "斷言有或沒有" in LOW_CONFIDENCE_NOTE

    def test_value_passed_at_invoke_time_overrides_the_default(self):
        system, _ = _render(confidence_note="\nOVERRIDE")

        assert system.endswith("Keep the answer concise and helpful.\nOVERRIDE")

    def test_the_note_is_data_so_braces_in_it_are_not_template_syntax(self):
        system, _ = _render(confidence_note="\n{not_a_variable}")

        assert system.endswith("\n{not_a_variable}")

    def test_prompt_still_requires_the_other_variables(self):
        # Behaviour unchanged for callers that forget a real variable.
        import pytest

        with pytest.raises(KeyError):
            _QA_PROMPT.format_messages(today="t", history="h", context="c")

    def test_pipeline_accepts_inputs_with_and_without_the_note(self):
        # Mirrors `_QA_PROMPT | llm | parser` in get_generation_chain with a
        # stand-in for the LLM, so no provider is needed.
        chain = _QA_PROMPT | RunnableLambda(lambda value: value.to_string())

        without_note = chain.invoke(_BASE_INPUT)
        with_note = chain.invoke({**_BASE_INPUT, "confidence_note": LOW_CONFIDENCE_NOTE})

        assert "不得杜撰" not in without_note
        assert "不得杜撰" in with_note


class TestFixedTextsPassTheGuardrails:
    def test_prefix_does_not_trigger_injection_or_pii_rules(self):
        assert detect_prompt_injection(LOW_CONFIDENCE_PREFIX) == (False, "")
        assert detect_pii(LOW_CONFIDENCE_PREFIX) == (False, "")

    def test_note_does_not_trigger_injection_or_pii_rules(self):
        assert detect_prompt_injection(LOW_CONFIDENCE_NOTE) == (False, "")
        assert detect_pii(LOW_CONFIDENCE_NOTE) == (False, "")

    def test_prefix_joined_to_an_answer_does_not_trigger_rules_across_the_seam(self):
        answer = LOW_CONFIDENCE_PREFIX + "\n\n" + "請洽管理者確認帳號權限設定。"

        assert detect_prompt_injection(answer) == (False, "")
        assert detect_pii(answer) == (False, "")

    def test_prefix_states_the_two_required_facts(self):
        assert "查無" in LOW_CONFIDENCE_PREFIX
        assert "一般性參考" in LOW_CONFIDENCE_PREFIX
