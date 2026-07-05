## Why

使用者反應碳排放對話助手有時候回應偏慢（先前 `carbon-rerank-perf` 已經處理過 reranker 效能問題），但目前使用者介面上完全看不到「這次回答花了多久」，無法自行判斷是正常延遲還是異常。加入回應時間顯示，讓使用者（和之後除錯的人）可以直接從對話內容看出每次回應的耗時。

## What Changes

- `chat_stream`（`src/rag_pipeline.py`）在函式一開始記錄起始時間，並在每一種會回傳最終文字的路徑（輸入端 prompt injection guardrail、離題訊息、cache 候選被 guardrail 擋下、cache 命中成功、生成後 guardrail 擋下、RAG 生成成功）都在最終文字尾端加上一行「回應時間：X.X 秒」（四捨五入到小數點後 1 位）。
- 有「參考資料：」來源區塊的情況下，回應時間顯示在來源區塊之後；沒有來源區塊的情況下，直接接在答案文字之後。兩者之間都以空行分隔，維持與現有來源區塊相同的排版風格。
- 新增 `_append_timing_line(text, elapsed_seconds)` helper，供所有回傳路徑共用。

## Non-Goals

- 不追蹤或顯示各階段（embedding、rerank、生成）個別耗時的細項拆解；只顯示從 `chat_stream` 開始到該次最終文字產生為止的總耗時。
- 不把回應時間寫入 MongoDB 對話紀錄或 LangSmith trace 的新欄位；純粹是串流文字裡新增的一行顯示內容，不影響既有的 `async_save_conversation` 呼叫參數。
- 不對逐字打字動畫（`asyncio.sleep(0.005)` 等）的時間做特殊處理或補償；顯示的時間是「開始處理到最終文字組好」的耗時，不包含該次最終文字本身逐字顯示所花的動畫時間。
- 不新增可設定關閉這個顯示的開關；所有回應路徑一律顯示。

## Capabilities

### New Capabilities

- `response-timing`: 定義 `chat_stream` 如何量測耗時、以及在哪些回應路徑、用什麼格式把「回應時間：X.X 秒」這行文字附加到最終串流輸出。

## Impact

- Affected specs: `response-timing` (new)
- Affected code:
  - Modified: src/rag_pipeline.py
  - Modified: tests/test_rag_pipeline_history.py
