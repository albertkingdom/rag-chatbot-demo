"""Unit tests for guardrails PII detection (phone pattern false positives)."""
import pytest

from src.guardrails import detect_pii


@pytest.mark.parametrize(
    "text",
    [
        "請撥 0912-345-678",
        "電話0912345678",
        "02-2345-6789",
        "(02)2345-6789",
        "+886 912 345 678",
        "+1 415 555 0100",
    ],
)
def test_phone_numbers_are_detected(text):
    assert detect_pii(text) == (True, "phone")


@pytest.mark.parametrize(
    "text",
    [
        "今天是 2026-10-11 星期日",
        "時間 2026-10-11 14:30",
        "2026/10/11",
        "排放量 123456 tCO2e",
        "2025 年排放 12345.67 噸",
        "類別 4.1 與 4.2，門檻 0.95",
        "共 1000 筆，耗時 25 秒",
    ],
)
def test_dates_and_figures_are_not_pii(text):
    assert detect_pii(text) == (False, "")
