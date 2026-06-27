## Context

混合檢索靠 `doc_id` 當 key 做 RRF 融合。目前 `doc_id` 由 `f"qa_{abs(hash(page_content))}"` 產生，分別在 `BM25Index.build_from_documents`（建索引時）與 `HybridRetriever._vector_search`（查詢時）各算一次。Python 字串 `hash()` 受 `PYTHONHASHSEED` 隨機鹽影響、跨 process 不一致，導致兩端對同一份文件算出不同 id，融合形同失效。

`metadata['doc_id']` 從未被寫入（sync 只寫 `{"answer", "text"}`），所以兩端都落到不穩定的 fallback。`bm25-index` spec 本就要求 doc_id「stable」「derived from metadata」，現況違規。

來源資料 `uploaded_files/generated_user_manual.xlsx` 目前只有 `question`、`answer` 兩欄，沒有天然主鍵。

## Goals / Non-Goals

**Goals:**

- 以來源 `uuid` 欄位作為唯一、跨 process／跨機／跨重新同步皆穩定的 `doc_id`。
- BM25 端與向量端皆從 `metadata['doc_id']` 讀取同一個值，使 RRF 融合 key 對齊。
- 缺 uuid 的列在 sync 時自動補 uuid4 並寫回來源檔，使 id 對未來同步持久穩定。
- 既有 Pinecone 向量一次性全量重建為 uuid id。

**Non-Goals:**

- 不調整 RRF 演算法或 `RRF_K` / `BM25_TOP_N` / `VECTOR_TOP_N` / `FUSION_TOP_M`。
- 不更換 BM25 斷詞器或向量 embedding 模型。
- 不引入外部資料庫或 id 對應服務。
- 不擴充 `uploaded_files` 以外的來源格式。

## Decisions

### D1：doc_id 來源 = 來源資料的 uuid 欄

於 `uploaded_files/generated_user_manual.xlsx` 新增 `uuid` 欄。`extract_from_csv` / `extract_from_xlsx` / `extract_from_pdf` 讀入 uuid，放進回傳 dict 的 `uuid` 欄位；`sync_vector_store` 組 Document 時放入 `metadata['doc_id']`（同時保留既有 `answer`、`text`）。pdf 來源無表格欄位，視為一律缺 uuid，走 D2 生成（但不寫回 pdf，改記錄於旁路；本變更範圍以 csv/xlsx 寫回為主，pdf 僅在記憶體生成以不阻斷流程）。

### D2：缺 uuid 時生成並寫回來源檔

`sync_vector_store` 載入後、進入 upsert 前，掃描每列：缺 `uuid` 者以 `uuid.uuid4()` 生成字串值填入。對 csv/xlsx 來源，將補齊後的資料以原欄位順序（含新 `uuid` 欄）寫回原檔路徑。寫回採「先寫暫存檔再原子置換」避免半寫入毀損來源。既有檔的一次性回填即透過此流程在首次 sync 完成。

### D3：去重改以 uuid 為單位、消除 126 vs 117

現行 `local_docs` 以 `(question, answer)` 去重、但 BM25 又以 `hash(page_content)` 當 key 造成「同問題不同答案」互相覆蓋（126 收錄、117 實存）。改為：每列有穩定 uuid 後，以 `uuid`（= doc_id）作為 `local_docs` 的 key，問題相同但答案不同的列因 uuid 不同而各自保留。完全內容重複（同 uuid 不會發生；若來源出現重複 uuid 視為資料錯誤，記錄警告並保留後者）。

### D4：Pinecone upsert 帶 doc_id metadata

upsert 時向量 `id` 使用該 uuid；`metadata` 除既有 `answer`、`text` 外加入 `doc_id`（= uuid）。`HybridRetriever._vector_search` 改以 `doc.metadata.get("doc_id")` 取 id，移除 `hash(page_content)` fallback。

### D5：BM25 build 嚴格讀 metadata['doc_id']

`BM25Index.build_from_documents` 改為 `doc_id = doc.metadata["doc_id"]`；缺鍵時 raise `ValueError`（明確失敗，不再靜默產生不穩定 id）。`_load` 既有持久檔格式不變（仍是 doc_ids / tokenized_corpus / corpus）。

### D6：既有 Pinecone 一次性全量重建

切換 id 規則後，`sync_vector_store` 既有 diff（`local_ids - existing_ids` / `existing_ids - local_ids`）會自然將舊 hash-id 向量列入刪除、uuid-id 列入新增，重跑一次 embedding 即完成遷移，無需額外遷移程式。

## Implementation Contract

- **Behavior**：sync 後，同一份 Q&A 在 BM25 與向量端的 `doc_id` 相同且跨重啟一致；重複 sync 未變動資料不產生 id 變動；來源缺 uuid 的列於 sync 後在來源檔中帶有穩定 uuid。
- **Interface / data shape**：
  - 來源 csv/xlsx 新增欄位 `uuid`（字串）。
  - extract 回傳 dict 形如 `{"question": str, "answer": str, "uuid": str | None}`。
  - Document 形如 `Document(page_content=question, metadata={"answer": answer, "text": question, "doc_id": uuid})`。
  - Pinecone 向量：`id = uuid`，`metadata` 含 `doc_id = uuid`（與 `answer`、`text`）。
  - `BM25Index.build_from_documents(docs)`：每個 doc 需有 `metadata["doc_id"]`。
- **Failure modes**：
  - `build_from_documents` 遇缺 `metadata["doc_id"]` → raise `ValueError`，不寫持久檔。
  - 寫回來源檔失敗 → 該次 sync 視為失敗並回報錯誤（避免「向量已用新 uuid、但來源未持久化」的不一致）；採暫存檔原子置換降低風險。
  - 來源出現重複 uuid → 記錄警告、保留後者，不中斷。
- **Acceptance criteria**：
  - `tests/test_document_identity.py`：extract 帶 uuid → metadata['doc_id'] 一致；缺 uuid → 生成且寫回（用暫存路徑驗證重讀後有 uuid）；重複 sync 同資料 doc_id 集合不變。
  - `tests/test_bm25_index.py`：build 以 metadata['doc_id'] 為 key；缺 doc_id 列 raise。
  - `tests/test_hybrid_retriever.py`：向量端以 metadata['doc_id'] 取 id；同一文件兩路 doc_id 相同 → 融合為單一 entry。
- **Scope boundaries**：
  - In scope：`src/build_vector_store.py`、`src/bm25_index.py`、`src/hybrid_retriever.py`、`uploaded_files/generated_user_manual.xlsx` 回填、相關測試。
  - Out of scope：RRF/參數、斷詞器、embedding 模型、pdf 來源寫回、web 容器啟動後自動重載 BM25（屬另一議題）。

## Risks / Trade-offs

- **寫回來源檔有副作用**：sync 會改動 `uploaded_files` 內的來源檔。以原子置換降低毀損風險；但使用者若同時手動編輯該檔可能衝突。屬可接受，因 sync 為背景作業且來源檔由系統管理。
- **一次性重 embedding 成本**：遷移會重算所有向量 embedding。126 筆規模成本極小，可接受。
- **pdf 來源無穩定 uuid**：本變更不為 pdf 寫回 uuid，其 doc_id 仍每次生成、跨 sync 不穩定。現有資料僅 xlsx，影響為零；若未來啟用 pdf 來源需另開變更處理。
- **web 記憶體快取未自動更新**：sync 重建 index 後，已啟動的 web 仍需重啟才載入新 BM25。本變更不處理，沿用現況（手動 `docker compose restart web`）。
