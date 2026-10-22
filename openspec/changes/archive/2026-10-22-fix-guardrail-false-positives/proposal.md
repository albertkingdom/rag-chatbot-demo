## Problem

護欄（`src/guardrails.py`）有四類已用實際函式重現的誤判與漏判：

- 輸出端 email 規則會把含客服信箱的正常答案整段換成拒答訊息，例如「請聯絡客服 support@example.com。」。
- email 規則用 `\b` 邊界，email 前後任一側緊貼中文字時不會被偵測，例如「客服信箱support@example.com以」會原樣送出。
- 輸入端 injection 規則過寬：「How do I reset my password?」「What is a token?」被擋。輸出端使用同一份清單，答案夾英文原詞（secret、api key、token、顯示系統設定）也會被擋。
- 輸出端 `credit_card` 規則沒有 Luhn 檢查，「2023 2024 2025 2026」被判成信用卡；`ip_address` 不檢查每段 ≤255 且不看上下文，章節號「1.2.3.4」被判成 IP。

`tests/test_guardrails.py` 目前只有 phone 與日期、數字的測試，這四類規則沒有任何測試保護。

## Root Cause

- email：regex 使用 `\b`，Python 把 CJK 字元視為 word character，中文與 ASCII 字元之間不成立詞界。phone 規則已為此改用 lookaround，email 沒有一併改。
- email 誤擋：輸出端對所有 email 一律攔截，沒有區分知識庫內公開的客服信箱。
- injection：清單中的 `api key`、`secret`、`password`、`token`、`bypass`、`顯示(系統|提示|指令)` 是無詞界的單字比對，且輸入端與輸出端共用同一份清單。
- credit_card / ip_address：規則只比對數字形狀，沒有 Luhn 檢查，也沒有數值範圍檢查。

## Proposed Solution

- email 規則改用 ASCII 字元類別的 lookaround，取代 `\b`，使緊貼中文的 email 也能偵測。
- 輸出端 email 加入白名單（可由環境變數設定的網域），白名單內的地址不攔截；其他 email 仍整段攔截。
- injection 清單收斂為攻擊句型（例如要求洩漏或列印 system prompt、忽略先前指令、繞過規則），移除單字層級的 `password`、`token`、`secret`、`api key`、`bypass`、`顯示(系統|提示|指令)`；輸入端與輸出端分開兩份清單，輸出端只保留會洩漏系統內容的句型。
- `credit_card` 加 Luhn 檢查，並排除由四位數年份（1900–2100）組成的數字串（年份串「2023 2024 2025 2026」去掉分隔符後恰好通過 Luhn，只靠 Luhn 擋不掉）；`ip_address` 限制每段 0–255。
- 在 `tests/test_guardrails.py` 補齊 email、injection、credit_card、ip_address 的測試，已驗證的誤擋與漏判案例作為回歸測試。

## Non-Goals

- 不改成遮罩（mask）：遮罩會讓客服信箱變成無法使用的字串，答案仍然沒有用。
- 不改 `chat_service.py` 的攔截流程（命中即整段換成安全訊息、不寫入歷史與快取）；只改偵測函式的判斷，輸出端三個呼叫點僅多傳一個 `direction` 參數。
- 不處理 `phone` 與 `taiwan_id` 規則。
- 不重新設計 PII 以外的護欄，例如 `check_context_similarity`。
- 不新增依賴 LLM 的護欄判斷。

## Capabilities

### New Capabilities

- `guardrail-detection`: 規範輸入端 prompt injection、輸出端 injection 與 PII（email、信用卡、IP）的偵測行為，包含誤判與漏判的邊界案例。

### Modified Capabilities

(none)

## Success Criteria

- 輸出端答案含白名單內的 email（如 support@example.com）時不被攔截；含白名單外的 email 時仍被攔截。
- email 前後緊貼中文字時被偵測到（白名單外）。
- 「How do I reset my password?」「What is a token?」通過輸入端；「Ignore previous instructions」「reveal the system prompt」仍被輸入端攔截。
- 輸出端答案含「API key」「secret」「token」「顯示系統設定」等一般用語時不被攔截。
- 「2023 2024 2025 2026」不被判為信用卡，Luhn 有效的卡號仍被攔截；「999.1.1.1」不被判為 IP；「1.2.3.4」形狀上是合法 IP，仍會被判為 IP，這個已知保留行為有測試記錄。
- `tests/test_guardrails.py` 涵蓋四類規則，且所有測試通過。

## Impact

- Affected specs: 新增 `guardrail-detection`
- Affected code:
  - Modified: `src/guardrails.py`、`src/config.py`、`src/chat_service.py`、`tests/test_guardrails.py`、`tests/test_config.py`、`architecture.md`、`README.md`
  - New: （none）
  - Removed: （none）
