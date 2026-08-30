## 1. 修改 Vector Search 回傳值

- [x] 1.1 `_vector_search` 方法除了回傳 `(doc_id, score)` 清單外，同時回傳 `doc_id → Document` 的查找字典，使 vector-only candidate 可在 candidate assembly 階段直接取用，無需額外查詢。驗證：`tests/test_hybrid_retriever.py` 中新增測試，確認 `_vector_search` 回傳的字典包含所有帶 `doc_id` 的 Document

## 2. 修改 Candidate Assembly

- [x] 2.1 `_gather_candidates` 接收 vector search 的查找字典作為參數，解析 fused `doc_id` 時依序查找 BM25 index 和 vector lookup dict，不再呼叫 `_find_in_vector_docs`。滿足 spec「Hybrid retrieval combines BM25 and vector search」中「vector-only candidate resolved without additional query」場景。驗證：`tests/test_hybrid_retriever.py` 中新增測試，當 doc_id 僅存在於 vector 結果時，candidate assembly 從 dict 取得 Document 而非呼叫 similarity_search
- [x] 2.2 當 fused `doc_id` 在 BM25 index 和 vector lookup dict 中都找不到時，跳過該 doc_id 不報錯，滿足 spec「candidate not found in either source」場景。驗證：`tests/test_hybrid_retriever.py` 中新增測試，確認缺失的 doc_id 被靜默跳過且不出現在 candidates 清單中

## 3. 移除 _find_in_vector_docs

- [x] 3.1 從 `HybridRetriever` 移除 `_find_in_vector_docs` 方法，確認所有既有測試通過且無殘留引用。驗證：執行 `grep -r "_find_in_vector_docs" src/ tests/` 確認無殘留引用，並執行 `pytest tests/test_hybrid_retriever.py` 全部通過

## 4. 整合驗證

- [x] 4.1 完整 retrieve 流程中，Gather Candidates 步驟不再對 Pinecone 發起額外查詢，回傳的 `candidates` 和 `fusion_metadata` 結構與修改前一致。驗證：執行 `pytest tests/test_hybrid_retriever.py tests/test_app_pipeline.py` 全部通過
