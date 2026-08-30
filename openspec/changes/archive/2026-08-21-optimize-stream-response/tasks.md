## 1. 新增共用 streaming 函式

- [x] 1.1 在 `src/rag_pipeline.py` 新增模組常數 `_STREAM_CHUNK_SIZE = 10` 和 `_STREAM_CHUNK_DELAY = 0.005`，以及 `_stream_text(text: str)` async generator。此函式以 chunk size 為步進 yield 累積文字，最後一次 yield 包含完整文字。滿足 spec「Text responses are streamed in fixed-size chunks」。驗證：`tests/test_rag_stream.py` 中新增單元測試，確認 30 字元回應 yield 3 次、25 字元回應 yield 3 次（最後一次為完整文字）

## 2. 替換所有逐字 yield 迴圈

- [x] 2.1 將 `chat_stream` 中所有 7 處 `for i in range(1, len(...) + 1): yield ...[:i]; await asyncio.sleep(0.005)` 迴圈替換為 `async for chunk in _stream_text(...): yield chunk`。涵蓋 RAG 回應、direct 回應、cached 回應、guardrail 回應等所有路徑。滿足 spec「All yield sites use the shared streaming function」。驗證：執行 `grep -n "for i in range(1, len(" src/rag_pipeline.py` 確認回傳為空（無殘留舊迴圈）

## 3. 整合驗證

- [x] 3.1 所有既有測試通過且 Stream Response 的 yield 次數大幅減少。驗證：執行 `pytest tests/test_rag_stream.py tests/test_app_pipeline.py` 全部通過
