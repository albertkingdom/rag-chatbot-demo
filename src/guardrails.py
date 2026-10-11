import re
from typing import List, Tuple


PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    # Specific phone shapes only: the previous generic digit-group pattern
    # also matched dates (2026-10-11) and plain figures (123456 tCO2e).
    # Digit lookarounds instead of \b, because CJK characters count as word
    # characters and "電話0912345678" has no word boundary before the digits.
    "phone": re.compile(
        r"(?<![\d+])(?:"
        r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}"                      # TW mobile
        r"|\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{4}"             # TW landline
        r"|\+\d{1,3}[-\s]?\(?\d{1,4}\)?(?:[-\s]?\d{2,4}){2,3}"  # international
        r")(?!\d)"
    ),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    "taiwan_id": re.compile(r"\b[A-Z][12]\d{8}\b", re.IGNORECASE),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore (all|previous|the above) instructions",
        r"disregard (all|previous|the above) instructions",
        r"system prompt",
        r"developer message",
        r"reveal (the )?prompt",
        r"print (the )?prompt",
        r"api key",
        r"secret",
        r"password",
        r"token",
        r"bypass",
        r"忽略(以上|之前)?(指令|規則)",
        r"系統提示",
        r"開發者(訊息|指令)",
        r"洩漏(提示|指令|金鑰)",
        r"顯示(系統|提示|指令)",
        r"繞過",
    ]
]


def detect_pii(text: str) -> Tuple[bool, str]:
    if not text:
        return False, ""
    for name, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            return True, name
    return False, ""


def detect_prompt_injection(text: str) -> Tuple[bool, str]:
    if not text:
        return False, ""
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return True, pattern.pattern
    return False, ""


def check_context_similarity(
    response: str,
    contexts: List[str],
    reranker,
    min_similarity: float = 0.3
) -> Tuple[bool, float]:
    if not response or not contexts:
        return False, 0.0

    pairs = [(response, ctx) for ctx in contexts]
    scores = reranker.score(pairs)
    best_similarity = float(max(scores))

    return best_similarity >= min_similarity, best_similarity


def get_guardrail_message() -> str:
    return (
        "抱歉，我無法提供此回覆。"
        "請改以 CarbonM 系統相關問題詢問，或提供更多細節。"
    )
