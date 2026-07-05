## 1. 量測耗時與共用 helper

- [x] 1.1 在 `src/rag_pipeline.py` 的 `chat_stream` 函式一開始（`session_id`/`history_key` 計算之後、`try` 區塊之前）記錄 `start_time = time.monotonic()`，實作「chat_stream measures total response time」需求；新增 `_append_timing_line(text: str, elapsed_seconds: float) -> str` helper，回傳 `f"{text}\n\n回應時間：{elapsed_seconds:.1f} 秒"`；以 `tests/test_rag_pipeline_history.py` 新增單元測試直接呼叫 `_append_timing_line("答案", 3.2)` 驗證回傳 `"答案\n\n回應時間：3.2 秒"`

## 2. 六條回應路徑都附加回應時間

- [x] 2.1 修改輸入端 prompt-injection guardrail 分支（`chat_stream` 開頭偵測 `detect_prompt_injection(message)` 為真的分支），在 `yield get_guardrail_message()` 的逐字迴圈前，用 `time.monotonic() - start_time` 算出耗時並呼叫 `_append_timing_line` 組出最終文字再逐字 yield，實作「Every response path appends a response-time line」需求中的 guardrail 情境；以 `tests/test_rag_pipeline_history.py` 新增測試驗證這個分支最終 yield 字串以 "\n\n回應時間：" 開頭的行結尾且格式符合 `X.X 秒`
- [x] 2.2 修改離題（off-topic）分支，在 `yield off_topic_message` 的逐字迴圈前比照 2.1 附加回應時間，實作「Every response path appends a response-time line」的離題情境；以 `tests/test_rag_pipeline_history.py` 新增測試驗證離題訊息的最終 yield 字串包含格式正確的回應時間行
- [x] 2.3 修改 cache 候選被 guardrail 擋下的分支（`cached_response` 命中但 PII/injection 偵測為真），比照 2.1 附加回應時間，實作「Every response path appends a response-time line」的 cache-candidate guardrail 情境；以 `tests/test_rag_pipeline_history.py` 新增測試驗證此分支輸出含回應時間行
- [x] 2.4 修改 cache 命中成功分支，在既有的 `_append_source_block(cached_answer, cached_sources)` 結果後面再串接 `_append_timing_line`，實作「RAG answer with sources gets timing after the source block」與「Cache hit」情境（時間顯示在來源區塊之後）；以 `tests/test_rag_pipeline_history.py` 更新既有的 cache-hit 測試，驗證最終字串同時含「參考資料：」區塊與其後的回應時間行
- [x] 2.5 修改生成後 guardrail 擋下的分支（`full_response` 產生後 PII/injection 偵測為真），比照 2.1 附加回應時間，實作「Every response path appends a response-time line」的生成後 guardrail 情境；以 `tests/test_rag_pipeline_history.py` 新增測試驗證此分支輸出含回應時間行
- [x] 2.6 修改 RAG 生成成功分支，在既有的 `_append_source_block(full_response, sources)` 結果後面再串接 `_append_timing_line`，實作「RAG answer with sources gets timing after the source block」與「RAG answer without sources gets timing directly after the answer」情境；以 `tests/test_rag_pipeline_history.py` 更新既有的 RAG 成功測試，驗證有來源與無來源兩種情況下回應時間行的位置都正確
