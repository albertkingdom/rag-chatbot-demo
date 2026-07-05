## 1. 檢索鏈組出來源清單

- [x] 1.1 在 `src/rag_pipeline.py` 新增 `_format_sources(docs)` helper，實作「Deduplicated source list built from original QA content」需求：走訪 rerank 後的 `docs`，組出 `"Q: <text 或 page_content> A: <answer>"`（超過 100 字元截斷加 `...`），問題或答案缺值時退回 `metadata["doc_id"]`，並依首次出現順序去重；以 `tests/test_app_pipeline.py` 新增測試案例驗證含重複 QA 內容的 3 個 docs 時回傳去重後的清單、長內容會截斷、且缺問題或答案的 doc 改用 `doc_id`
- [x] 1.2 在 `_rerank`（`get_retrieval_chain` 內）呼叫 `_format_sources` 並將結果放入回傳 dict 的 `sources` 鍵，實作「Retrieval chain assembles context from answer metadata」的擴充內容（來源清單取自 rerank 後 docs 的 question 欄位，缺值時退回 doc_id）；以 `tests/test_rag_pipeline_history.py` 驗證 `get_retrieval_chain().ainvoke(query)` 回傳的 dict 同時含 `context`、`contexts`、`sources` 三個鍵且 `sources` 對應同一批 docs

## 2. 串流回答附加來源區塊

- [x] 2.1 在 `chat_stream`（`src/rag_pipeline.py`）RAG 生成分支，於 guardrail 檢查通過後、逐字 yield 之前，依「Streamed answer includes an appended source list」需求組出 `full_response + "\n\n參考資料：\n" + "- "` 開頭的清單字串（`sources` 非空時），實作「`chat_stream` 附加來源的格式與時機」設計決策；以 `tests/test_rag_stream.py` 新增測試驗證累積輸出的最終字串等於 "回答本文\n\n參考資料：\n- 來源1\n- 來源2"
- [x] 2.2 驗證 `sources` 為空清單時 RAG 分支輸出不附加任何區塊，維持與現況相同的純文字，符合「Streamed answer includes an appended source list」的空清單情境；以 `tests/test_rag_stream.py` 測試案例驗證 `sources=[]` 時最終 yield 字串等於原始 `full_response`
- [x] 2.3 確認 guardrail、off-topic 分支的回應不受影響、不附加來源區塊，符合「Streamed answer includes an appended source list」中「Guardrail and off-topic responses are unaffected」情境；以 `tests/test_rag_stream.py` 既有的 guardrail/off-topic 測試案例驗證輸出字串不含「參考資料：」子字串

## 3. 快取讀寫儲存來源清單

- [x] 3.1 修改 `src/cache_service.py` 的 `PromptCacheService.set_cached_response`，新增 `sources: list[str]` 參數並寫入 Redis 快取值的 `sources` 欄位（JSON 序列化），實作「Cached responses store the source list」需求與「`PromptCacheService` 快取值 schema 新增必要欄位 `sources`」設計決策；以 `tests/test_cache_service.py` 新增測試驗證寫入含 `sources=["問題A"]` 後可從 Redis 讀出相同欄位值
- [x] 3.2 修改 `PromptCacheService.get_cached_response`，解析並回傳快取值中的 `sources` 欄位（不做缺欄位 fallback），實作「Cached responses store the source list」的讀取情境；以 `tests/test_cache_service.py` 驗證 `get_cached_response` 回傳 dict 含 `sources` 鍵且值與寫入時一致
- [x] 3.3 修改 `chat_stream` 的 cache 命中分支，呼叫 `set_cached_response`/讀取 `cached_response["sources"]` 後比照 RAG 分支組出附加來源區塊的字串再逐字 yield，實作「Cache-hit answer also gets a source block appended」情境；以 `tests/test_rag_stream.py` 新增測試模擬 cache 命中並驗證最終 yield 字串含「參考資料：」區塊且內容與快取的 `sources` 一致

## 4. 部署步驟：一次性 flush prompt cache

- [x] 4.1 在部署流程文件或 runbook 中新增一步，依「Deployment requires a one-time prompt cache flush」需求與「部署時一次性 flush prompt cache namespace」設計決策，於本次程式碼上線的同一維護窗口內對 Redis 執行 `redis-cli --scan --pattern 'prompt_cache:*' 加上批次刪除`，清空所有舊 schema 快取；以人工檢查 runbook 文件確認該步驟已列在部署上線前置作業清單中，且順序早於新版程式碼開始服務流量
