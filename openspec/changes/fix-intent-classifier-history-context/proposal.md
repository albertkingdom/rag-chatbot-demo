## Problem

同一主題的問題，只因為換了問法（例如把肯定句改寫成疑問句、或拿掉操作動詞），`IntentClassifier.classify()` 就可能給出不一致的分類結果：使用者問「門檻值設定該如何填寫？」被判定為 `relevant=true`，但緊接著問「你確定嗎」（會先被 `rewrite_query` 改寫成「您確定顯著性門檻、實質性門檻和排除門檻的建議值分別為3、5、0.5嗎？」）卻被判定為 `relevant=false`，導致使用者在同一個對話串中被擋下、看到離題提示訊息，即使他只是在追問同一個系統操作主題。

## Root Cause

`chat_stream`（`src/rag_pipeline.py`）呼叫 `rewrite_query()` 把追問改寫成獨立問句後，只把這個改寫後的單一問句字串傳給 `IntentClassifier.classify()`。`classify()`（`src/intent_classifier.py`）的 prompt 完全不包含對話歷史，分類器只能看著這一句話本身判斷是否與 CarbonM 系統相關；當改寫後的問句偏向敘述數值細節（例如「顯著性門檻、實質性門檻和排除門檻的建議值分別為3、5、0.5」）而不含「填寫」「系統」等操作性詞彙時，LLM 容易把它誤判為通用知識問題，即使同一輪對話的上一句話已經確立了「這是在問 CarbonM 系統設定」的脈絡。

## Proposed Solution

修改 `IntentClassifier.classify()` 的簽章，新增一個 `history` 參數（沿用 `chat_stream` 已經在用的 `history: list` 格式），並在 prompt 中附上最近幾輪對話（沿用 `rag_pipeline.py` 現有的 `_parse_history_turns`/`format_history` 邏輯所使用的格式），讓分類器判斷時看得到「這是延續同一個系統操作對話的追問」，而不是只看孤立的單一問句。`chat_stream` 呼叫 `intent_classifier.classify(rewritten_query)` 時改為傳入 `intent_classifier.classify(rewritten_query, history)`。`is_relevant()` 的簽章同步新增 `history` 參數並轉傳給 `classify()`。

## Non-Goals

- 不改動 `rewrite_query()` 的改寫邏輯本身；問題出在 `classify()` 看不到歷史脈絡，不是改寫的問句品質不好。
- 不調整 `SYSTEM_TOPICS` 的主題清單或範例內容；這次只解決「有沒有把歷史脈絡傳給分類器」的問題，不重新調校分類器對哪些主題算相關的判斷標準。
- 不改變 `confidence_threshold`（目前為 0.7）或分類信心分數的計算方式。
- 不保證加入歷史脈絡後 100% 消除所有分類不一致的情況；LLM 分類器本質上不是決定性的，這次修正的目標是讓分類器在有明顯延續同一主題的追問時，能看到足夠脈絡做出合理判斷，而不是每次都要人工微調範例。

## Capabilities

### New Capabilities

- `intent-classification`: 定義 `IntentClassifier.classify()` 如何結合改寫後的問句與對話歷史脈絡，判斷使用者問題是否與 CarbonM 系統相關。

## Impact

- Affected specs: `intent-classification` (new)
- Affected code:
  - Modified: src/intent_classifier.py
  - Modified: src/rag_pipeline.py
  - Modified: tests/test_rag_pipeline_history.py
