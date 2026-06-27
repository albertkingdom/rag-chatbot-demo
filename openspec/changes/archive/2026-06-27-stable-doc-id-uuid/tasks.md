## 1. 來源資料 uuid 與擷取

- [x] 1.1 `extract_from_csv` / `extract_from_xlsx` 讀入來源 `uuid` 欄並放入回傳 dict 的 `uuid` 鍵（缺欄或空值時為 `None`）。驗證：`tests/test_document_identity.py` 對含 uuid 的列驗證回傳 dict 帶正確 uuid。（對應 D1：doc_id 來源 = 來源資料的 uuid 欄；Requirement "Stable doc_id sourced from a uuid column"）
- [x] 1.2 `sync_vector_store` 組 Document 時將 uuid 寫入 `metadata['doc_id']`，並保留既有 `answer`、`text`。驗證：`tests/test_document_identity.py` 驗證產出的 `Document.metadata['doc_id']` 等於來源 uuid。（對應 D1：doc_id 來源 = 來源資料的 uuid 欄；Requirement "Stable doc_id sourced from a uuid column"）

## 2. 缺 uuid 生成與寫回

- [x] 2.1 `sync_vector_store` 在 upsert 前掃描每列，缺 `uuid` 者以 `uuid.uuid4()` 字串補齊。驗證：`tests/test_document_identity.py` 對無 uuid 的列驗證 sync 後該列取得非空 uuid。（對應 D2：缺 uuid 時生成並寫回來源檔；Requirement "Missing uuids are generated and persisted to the source"）
- [x] 2.2 將補齊後的 csv/xlsx 以原欄位順序（含新 `uuid` 欄）採「暫存檔→原子置換」寫回原路徑；寫回失敗時 sync 回報錯誤而不繼續 upsert。驗證：`tests/test_document_identity.py` 用暫存來源檔，sync 後重讀檔案確認每列都有持久化 uuid；模擬寫回失敗時 sync 回傳錯誤狀態。（對應 D2；Requirement "Missing uuids are generated and persisted to the source"）
- [x] 2.3 對未變動且已含 uuid 的來源重複執行 sync，產生的 `doc_id` 集合不變。驗證：`tests/test_document_identity.py` 連續兩次 sync，斷言兩次 `local_docs` 的 key 集合相等。（對應 D2；Requirement "Missing uuids are generated and persisted to the source"）

## 3. doc_id 去重與雙存儲

- [x] 3.1 `local_docs` 改以 `uuid`（= doc_id）為 key，使「問題相同、答案不同」的列各自保留；來源出現重複 uuid 時記錄警告並保留後者。驗證：`tests/test_document_identity.py` 以同問題不同答案的兩列驗證兩者皆收錄、key 為各自 uuid。（對應 D3：去重改以 uuid 為單位、消除 126 vs 117）
- [x] 3.2 Pinecone upsert 以 uuid 作為向量 `id`，並在向量 `metadata` 加入 `doc_id`（= uuid）。驗證：`tests/test_document_identity.py`（或既有 sync 測試）以 mock Pinecone index 斷言 upsert 的 vector id 與 metadata['doc_id'] 皆為 uuid。（對應 D4：Pinecone upsert 帶 doc_id metadata；Requirement "doc_id is persisted to both stores and read consistently"）

## 4. 檢索端讀取 doc_id

- [x] 4.1 `BM25Index.build_from_documents` 改為 `doc_id = doc.metadata["doc_id"]`，移除 `hash(page_content)` fallback；缺 `metadata['doc_id']` 時 raise `ValueError` 且不寫持久檔。驗證：`tests/test_bm25_index.py` 驗證以 metadata doc_id 建索引、search 回傳該 doc_id；缺 doc_id 的 Document 觸發 `ValueError`。（對應 D5：BM25 build 嚴格讀 metadata['doc_id']；Requirement "Build BM25 index from Q&A documents"）
- [x] 4.2 `HybridRetriever._vector_search` 改以 `doc.metadata.get("doc_id")` 取 doc_id，移除 `hash(page_content)` fallback。驗證：`tests/test_hybrid_retriever.py` 驗證向量端 doc_id 來自 metadata。（對應 D4；Requirement "doc_id is persisted to both stores and read consistently"）
- [x] 4.3 同一份文件同時出現在 BM25 與向量結果時，因 doc_id 相同被 RRF 融合成單一 entry。驗證：`tests/test_hybrid_retriever.py` 構造兩路命中同一 doc_id，斷言 fused 結果該文件僅一筆且分數為兩路 RRF 貢獻之和。（對應 D4；Requirement "Hybrid retrieval combines BM25 and vector search"）

## 5. 遷移與整體驗證

- [x] 5.1 對既有 `uploaded_files/generated_user_manual.xlsx` 執行一次 sync 完成 uuid 回填與 Pinecone 全量重建，確認來源檔已含 uuid 欄、`models/bm25_corpus.json` 以 uuid 為 doc_id。驗證：手動執行 sync 後檢查 xlsx 含 uuid 欄、BM25 持久檔 doc_ids 為 uuid 格式、Pinecone 向量 id 為 uuid。（對應 D6：既有 Pinecone 一次性全量重建）
- [x] 5.2 執行完整測試套件（`pytest`）全數通過。驗證：`pytest` 結束碼為 0，無失敗或錯誤。
