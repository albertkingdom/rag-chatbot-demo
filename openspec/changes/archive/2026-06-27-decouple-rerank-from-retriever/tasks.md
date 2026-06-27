## 1. HybridRetriever 職責縮減

- [x] 1.1 移除 `HybridRetriever.__init__` 的 `reranker_scorer` 參數與 `self.reranker_scorer` 屬性，使 `HybridRetriever` 不再持有 reranker 參考。驗證：`tests/test_hybrid_retriever.py` 的 `TestInstantiation` 改為 `HybridRetriever(bm25_index, vector_store)` 不傳第三參數即可實例化，且測試通過。
- [x] 1.2 重寫 `HybridRetriever.retrieve()` 的回傳形狀為 `{"candidates": list[Document], "fusion_metadata": dict}`，移除 rerank 段（不再呼叫 scorer、不再排序 top 3、不再組 `context`/`contexts`/`rerank_scores`），對應 design 的「HybridRetriever.retrieve() 回傳形狀變更為 top-M 候選」決策。`fusion_metadata` 保留 `bm25_results`、`vector_results`、`fusion_results`、`fallback`，移除 `rerank_scores` 鍵，對應「Observability of retrieval stages」需求。驗證：`tests/test_hybrid_retriever.py` 新增斷言 `result` 含 `candidates` 鍵、不該出現 `docs`/`rerank_scores`/`context`/`contexts` 鍵，且 `fusion_metadata` 不含 `rerank_scores`。
- [x] 1.3 確認「Hybrid retrieval combines BM25 and vector search」需求在 reranker 移除後仍成立：BM25 與向量並行、RRF 融合、回傳 top `FUSION_TOP_M` 候選的行為不變。驗證：`tests/test_hybrid_retriever.py` 的 `TestRRFFusion` 決定性測試與截斷測試仍通過。

## 2. Rerank 階段實作

- [x] 2.1 新增 `rerank_candidates(query, candidates, scorer)` 純函式，接收 query 字串、`list[Document]` 候選、`scorer: Callable`，呼叫 `scorer([(query, d.page_content) for d in candidates])`、按分數降序排序、截斷至 Top 3、回傳 `{"docs": top_docs, "rerank_scores": summary}`，對應 design 的「Rerank 階段以純函式實作，不引入新類別」決策與「Rerank stage output contract」需求。`rerank_scores` 每筆含 `rank`（1-indexed int）、`score`（4 位小數字串）、`content`（`page_content` 前 100 字 + `...`）；回傳 dict 恰含 `docs` 與 `rerank_scores` 兩鍵，不含 `context`/`contexts`。驗證：`tests/test_rerank_stage.py` 新增測試 `test_top3_truncation_and_order`，給定 4 個候選與 mock scorer 回傳 `[0.60, 0.95, 0.70, 0.80]`，斷言回傳 `docs` 為 [B, D, C]、`rerank_scores` 三筆 rank 1/2/3。
- [x] 2.2 實作空候選短路：當 `candidates` 為空 list 時，`rerank_candidates` 直接回傳 `{"docs": [], "rerank_scores": []}` 且不呼叫 scorer，對應 design 的「空候選時 rerank 階段短路回傳空結果」決策與「Empty candidates short-circuit without invoking scorer」需求。驗證：`tests/test_rerank_stage.py` 新增測試 `test_empty_candidates_short_circuits`，傳入空 list 與會 raise 的 mock scorer，斷言 scorer 未被呼叫、回傳 `docs` 與 `rerank_scores` 兩個空欄位。
- [x] 2.3 驗證 rerank 階段不組裝 context：`rerank_candidates` 回傳 dict 只含 `docs`/`rerank_scores`，`context`/`contexts` 由 retrieval chain 末端以 `_format_docs` 從 `metadata["answer"]` 組裝（對應「Retrieval chain assembles context from answer metadata」需求）。驗證：`tests/test_rerank_stage.py` 新增測試 `test_return_keys_are_docs_and_rerank_scores_only`，斷言回傳 dict 不含 `context`/`contexts` 鍵。

## 3. Retrieval Chain 重組

- [x] 3.1 修改 `get_hybrid_retriever()` 移除 `reranker_scorer=get_reranker_model().score` 傳入，使 `HybridRetriever` 以兩參數建構，對應 design 的「Reranker scorer 仍由 `get_reranker_model().score` 提供」決策。驗證：grep 確認 `get_hybrid_retriever` 內不再出現 `reranker_scorer`。
- [x] 3.2 重組 `get_retrieval_chain()` 為兩段 `RunnableLambda`：第一段呼叫 `HybridRetriever.retrieve()` 回傳 `candidates` + `fusion_metadata`；第二段呼叫 `rerank_candidates(query, candidates, scorer=get_reranker_model().score)` 取得 `docs`/`rerank_scores`，再以既有 `_format_docs` 從 `metadata["answer"]` 組裝 `context`/`contexts`，合併 `fusion_metadata` 成最終 dict，對應 design 的「Rerank 階段置於 RunnableLambda 管線中而非回呼函式」決策與「Rerank scorer is injected as a callable」、「Retrieval chain assembles context from answer metadata」需求。`fusion_metadata` 從第一段傳遞至最終 dict 保留。驗證：新增/更新 app 管線測試，呼叫 `get_retrieval_chain().ainvoke(query)` 斷言回傳 dict 含 `context`、`contexts`、`docs`、`rerank_scores`、`fusion_metadata` 五鍵，且 `context` 內容來自 `metadata["answer"]`。
- [x] 3.3 整併既有 `rerank_analysis()` 並完成「Fused candidate set feeds existing reranker」需求的移轉：將 `src/app.py` 中未被 `chat_stream` 直接呼叫的 `rerank_analysis` 邏輯移除或改為內部呼叫 `rerank_candidates`，避免雙份 rerank 邏輯殘留；該需求原本由 `HybridRetriever` 內部 rerank 滿足，現由獨立 `rerank-stage` 接手。驗證：grep 確認 `src/app.py` 不再存在獨立的 `rerank_analysis` 函式，或已改為 thin wrapper 呼叫 `rerank_candidates`。

## 4. 測試更新與驗證

- [x] 4.1 更新 `tests/test_hybrid_retriever.py`：移除 `reranker_scorer` fixture 與 `retriever` fixture 的第三參數；移除任何對 `result["docs"]`/`result["rerank_scores"]` 的斷言；新增對 `result["candidates"]` 的斷言；`TestFallback` 與 `TestObservability` 改為斷言 `fusion_metadata` 不含 `rerank_scores`。驗證：`pytest tests/test_hybrid_retriever.py` 全綠。
- [x] 4.2 新增 `tests/test_rerank_stage.py` 覆蓋「Rerank stage transforms top-M candidates into top-3」、「Rerank stage output contract」、「Empty candidates short-circuit without invoking scorer」、「Rerank scorer is injected as a callable」四項需求的所有 scenario。驗證：`pytest tests/test_rerank_stage.py` 全綠。
- [x] 4.3 執行完整測試套件 `pytest` 確認無回歸，並手動以含節號查詢（如「4.2」）驗證 Top 3 命中行為與重構前一致。驗證：`pytest` 通過、手動查詢 Top 3 結果符合預期。
