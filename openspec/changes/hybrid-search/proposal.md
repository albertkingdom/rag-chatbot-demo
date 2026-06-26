## Why

目前 RAG 管線只使用語意向量檢索（Pinecone + OpenAI Embeddings）取得 Top 10 候選文件。對於包含精確專有名詞、代碼、序號、或用戶手冊表格節號（如「4.2」「範疇三」）的查詢，純語意檢索容易稀釋關鍵字的權重，導致該命中且未命中者被推進沒相關的語意鄰居稀釋排名。Hybrid Search 將關鍵字檢索（BM25）與語意檢索分數融合，兼顧精確詞比對與語意召回，是改善檢索品質最直接且成效已被廣泛驗證的下一步。

## What Changes

- 新增 BM25 關鍵字檢索索引，基於同一份 Q&A 知識庫文件建立，並在文件同步時一起更新。
- 新增分數融合層（Reciprocal Rank Fusion, RRF）將 BM25 與向量檢索結果按排名倒數加權合併，產生融合候選清單。
- 修改檢索管線：由「向量檢索 Top 10 → Reranker Top 3」改為「雙路檢索（BM25 Top N + 向量 Top N）→ RRF 融合 → Reranker Top 3」。
- 在 LangSmith 追蹤中暴露 BM25、向量、融合三階段的分數與來源，供觀察與調校。
- 檢索參數（BM25 Top N、向量 Top N、RRF k 常數）集中至 src/config.py 供調校。

## Non-Goals

- 不替換或移除 Pinecone 向量資料庫；BM25 為並行補充而非取代。
- 不引進外部 BM25 服務（如 Elasticsearch、OpenSearch）；使用輕量 in-process BM25 實作以維持單一 Docker 服務架構。
- 不改動 BGE Reranker 最終重排階段與 Top 3 邊界。
- 不改動生成模型、Guardrail、Intent Classification、Prompt Cache 與 MongoDB 日誌流程。
- 不在此 change 處理多語系分詞最佳化（中文分詞採預設行為，留待後續 change）。

## Capabilities

### New Capabilities

- `hybrid-retrieval`: 結合 BM25 關鍵字檢索與語意向量檢索的分數融合檢索能力，產生融合排名候選清單供後續重排使用。
- `bm25-index`: 基於本地 Q&A 知識庫的 in-process BM25 索引，於文件同步時一起重建或增量更新，供 hybrid-retrieval 查詢。

### Modified Capabilities

(none)

## Impact

- Affected specs: hybrid-retrieval, bm25-index
- Affected code:
  - New: src/bm25_index.py（BM25 索引建構、查詢、同步邏輯）
  - New: src/hybrid_retriever.py（雙路檢索協調 + RRF 融合實作）
  - Modified: src/build_vector_store.py（文件同步時一併更新 BM25 索引）
  - Modified: src/app.py（get_retrieval_chain 改為使用 hybrid retriever；chat_stream 檢索段不變）
  - Modified: src/config.py（新增 BM25/RRF 相關參數）
  - New: tests/test_bm25_index.py
  - New: tests/test_hybrid_retriever.py
- Affected dependencies: 新增 `rank_bm25`（輕量 BM25 純 Python 套件）至 requirements.txt
