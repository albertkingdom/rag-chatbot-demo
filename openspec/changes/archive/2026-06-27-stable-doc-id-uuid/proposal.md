## Problem

混合檢索（BM25 + 向量）靠 `doc_id` 當 key 做 RRF 融合，但目前 `doc_id` 是用 Python 內建 `hash(page_content)` 產生的（`src/bm25_index.py` 的 `build_from_documents` 與 `src/hybrid_retriever.py` 的 `_vector_search` 各算一次）。

Python 對字串的 `hash()` 預設受 `PYTHONHASHSEED` 隨機鹽影響，每個 process 結果不同。實測同一字串在三個 process 得到三個不同值。

後果：
- BM25 的 `doc_id` 是在「建索引的 process」算的並存進 `models/bm25_corpus.json`；查詢時向量端的 `doc_id` 在「web 的 process」即時重算。兩端 seed 不同 → 同一份文件的 `doc_id` 不一致。
- `_rrf_fuse` 因此把同一份文件當成兩份，RRF「同時命中兩路加權更高」的核心效果失效，混合檢索退化成兩串結果硬接，不報錯但價值幾乎喪失。
- `bm25-index` spec 已要求 doc_id 必須「stable」且「derived from document metadata」，現行實作違反此規格。

## Root Cause

`doc_id` 來自 `f"qa_{abs(hash(doc.page_content))}"`，而 `metadata['doc_id']` 從未被寫入（sync 只塞 `{"answer", "text"}`），所以兩端都走到不穩定的 `hash()` fallback。`hash()` 的字串雜湊跨 process 不穩定，導致建立端與查詢端產生的 id 無法對齊。

## Proposed Solution

改用「來源資料的 uuid 欄位」作為穩定、跨 process 一致的 `doc_id`，由單一權威來源讀取而非各自重算：

1. 來源 Q&A 資料新增 `uuid` 欄位。`extract_from_*` 讀入 uuid 並放進每個 Document 的 `metadata['doc_id']`。
2. `sync_vector_store` 在處理時偵測缺 uuid 的列，產生 uuid4 並「寫回來源 xlsx」，使 id 對未來的重新同步保持穩定。既有 `uploaded_files/generated_user_manual.xlsx` 一次性回填 uuid 欄。
3. Pinecone upsert 時，向量 id 使用該 uuid，且把 `doc_id` 一併寫進向量 metadata。
4. `BM25Index.build_from_documents` 改為從 `metadata['doc_id']` 取 `doc_id`，移除 `hash(page_content)` fallback；缺 `doc_id` 時明確報錯而非靜默產生不穩定 id。
5. `HybridRetriever._vector_search` 從 `metadata['doc_id']` 取 `doc_id`，與 BM25 端讀同一欄位，使融合 key 對齊。
6. 既有 Pinecone 向量一次性全量重建：切換為 uuid id 後，sync 的 diff 會刪除舊 hash-id 向量並以 uuid-id 重新 upsert（重跑一次 embedding）。

## Non-Goals

- 不改變 RRF 融合演算法、`RRF_K`、`FUSION_TOP_N`/`TOP_M` 等參數。
- 不改變 BM25 斷詞器或向量模型。
- 不引入外部資料庫或 id 對應服務；uuid 直接存在來源檔。
- 不處理 `uploaded_files` 以外的資料來源格式擴充。

## Success Criteria

- 同一份 Q&A 文件，BM25 端與向量端產生的 `doc_id` 完全相同，且跨不同 process／重啟後維持一致。
- 對同一份未變動的來源資料重複執行 `sync_vector_store`，產生的 `doc_id` 集合不變（不會誤判為全量新增／刪除）。
- 來源檔缺 uuid 的列在 sync 後，來源 xlsx 會被寫回並帶有穩定 uuid；再次 sync 不會重新生成。
- `BM25Index.build_from_documents` 對缺 `metadata['doc_id']` 的 Document 會明確拋錯，不再產生 `hash()` 衍生的 id。
- 既有測試（`tests/test_bm25_index.py`、`tests/test_hybrid_retriever.py`）更新後通過，新增涵蓋「跨來源 doc_id 一致」與「缺 uuid 寫回」的測試通過。

## Impact

- Affected specs: document-identity（新增）、bm25-index（修改）、hybrid-retrieval（修改）
- Affected code:
  - Modified:
    - src/build_vector_store.py
    - src/bm25_index.py
    - src/hybrid_retriever.py
    - uploaded_files/generated_user_manual.xlsx
    - tests/test_bm25_index.py
    - tests/test_hybrid_retriever.py
  - New:
    - tests/test_document_identity.py
  - Removed: (none)
