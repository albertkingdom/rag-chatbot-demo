## Context

現有 RAG 檢索流程位於 `src/app.py` 的 `get_retrieval_chain`：以 Pinecone 向量檢索 Top 10，再交由 BGE Reranker 重排取 Top 3。知識庫內容為碳管理系統用戶手冊的 Q&A 文件（位於 `uploaded_files/`），同步流程在 `src/build_vector_store.py` 的 `sync_vector_store` 中批次 embed 後 upsert 至 Pinecone。

純語意檢索對精確專有名詞（節號、代碼、數值）比對力不足。Hybrid Search 並行 BM25 關鍵字檢索，將兩路結果以 Reciprocal Rank Fusion（RRF）融合排名，再交給既有的 Reranker 取 Top 3。

技術棧限制：維持單一 Docker 服務架構，不引入外部搜尋引擎。LangChain 已在專案中廣泛使用。

## Goals / Non-Goals

**Goals:**

- 在既有向量檢索之外，並行執行 BM25 關鍵字檢索，兩路結果以 RRF 融合排名。
- BM25 索引基於與 Pinecone 同一份 Q&A 文件建立，於文件同步時一併更新。
- 融合後的候選清單（預設 10 筆）餵給既有 BGE Reranker，Top 3 邊界不變。
- 檢索過程在 LangSmith 中可觀察 BM25、向量、融合三階段分數與來源。
- 所有檢索參數集中於 `src/config.py`。

**Non-Goals:**

- 不替換或移除 Pinecone。
- 不引入 Elasticsearch / OpenSearch 等外部服務。
- 不改動 Reranker、Generation、Guardrail、Intent Classification、Prompt Cache、MongoDB 日誌階段。
- 不處理中文分詞最佳化（採 BM25 套件預設行為，留待後續 change）。

## Decisions

### In-process BM25 via rank_bm25

採用純 Python 套件 `rank_bm25`（BM25Okapi variant）。理由：無外部服務依賴、零網路延遲、與單一 Docker 服務架構一致；知識庫規模（數百至數千 Q&A 文件）足以在行程內完成檢索。捨棄 Elasticsearch/OpenSearch 係因引入叢集會破壞現有 `docker-compose.yml` 的輕量編排並增加維運成本。

### Reciprocal Rank Fusion 作為融合策略

採 RRF 公式 `score(d) = Σ 1/(k + rank_i(d))` 取代加權線性融合。理由：RRF 不需兩路分數尺度對齊（BM25 與向量 cosine 量級不同），對超參數不敏感，業界作法（Microsoft Fabric、Weaviate）亦以此為預設。`k` 常數預設 60。

### BM25 索引持久化為 JSON

BM25 語料庫與 token 化結果序列化為 `bm25_corpus.json` 暫存於本地 `models/` 目錄，啟動時載入、同步時重寫。理由：避免每次冷啟動重新 tokenize 整份知識庫（數百文件毫秒級即可，但無謂開銷仍該省）。

### 雙路檢索並行呼叫

BM25 與向量檢索在 `asyncio.gather` 中並行執行，兩者皆為 in-process 或網路 I/O，並行可隱藏延遲。融合為純 CPU 排序，放回主執行緒。

### 檢索參數集中至 config.py

新增 `BM25_TOP_N`、`VECTOR_TOP_N`、`RRF_K` 三項常數至 `src/config.py`，便於調校與 unit test 覆蓋。

## Implementation Contract

**行為**

- 檢索管線對呼叫端（`chat_stream`）的介面不變：輸入 rewritten query 字串，回傳含 `context`（拼裝文字）與 `contexts`（Document 清單）的 dict，格式與既有 `get_retrieval_chain` 輸出一致。
- 內部由「向量 Top 10 → Reranker Top 3」改為「BM25 Top N + 向量 Top N → RRF 融合 Top M → Reranker Top 3」。
- Hybrid 模式下 Reranker 輸入仍為 10 筆（融合後取前 10），Top 3 邊界不變。

**介面 / 資料形狀**

- `BM25Index`（`src/bm25_index.py`）：
  - `build_from_documents(docs: list[Document]) -> None`：建立詞頻結構與持久化檔。
  - `search(query: str, top_n: int) -> list[tuple[str, float]]`：回傳 `(doc_id, bm25_score)` 排序列表。
  - `get_doc(doc_id: str) -> Document`：以 doc_id 取回原始 Document。
- `HybridRetriever`（`src/hybrid_retriever.py`）：
  - `__init__(bm25_index, vector_store, reranker_scorer)`
  - `async retrieve(query: str, vector_top_n: int, bm25_top_n: int, rrf_k: int) -> dict`：回傳 `{"docs": list[Document], "rerank_scores": list, "fusion_metadata": {...}}`。
- `sync_vector_store` 末尾新增 `BM25Index.build_from_documents(local_docs)` 呼叫。

**失敗模式**

- BM25 索引未初始化時，`BM25Index.search` 拋 `RuntimeError("BM25 index not built")`；`HybridRetriever` 捕捉後退化為純向量檢索並在 LangSmith metadata 記錄 `fallback: "vector_only"`，不中斷回答。
- BM25 語料為空（無文件）時同上述退化路徑。
- 持久化檔案損毀時，視為未初始化並走退化路徑，stdout 印出警告。

**驗收準則**

- `tests/test_bm25_index.py`：`build_from_documents` 後 `search` 對含精確節號（如「4.2」）的查詢能回傳含該節號的 doc_id 且排名高於純語意檢索。
- `tests/test_hybrid_retriever.py`：
  - RRF 融合排序可重現（給定固定兩路兩路輸入，輸出固定排名）。
  - BM25 不可用時走 `vector_only` fallback，錯誤被記錄且不拋出。
  - 融合清單長度與 `VECTOR_TOP_N + BM25_TOP_N` 上界一致後裁切至 10。
- 手動驗證：含節號查詢的 Top 3 命中正確率較純向量檢索提升。

**範圍邊界**

- In scope：`src/bm25_index.py`、`src/hybrid_retriever.py`、`src/build_vector_store.py` 同步段、`src/app.py` 的 `get_retrieval_chain`、`src/config.py`、`requirements.txt`、兩支新測試。
- Out of scope：Reranker 模型替換、生成 prompt、Guardrail、Intent Classification、Prompt Cache、MongoDB 日誌、中文分詞最佳化、LangGraph 改寫。

## Risks / Trade-offs

- [RRF 對極端短查詢可能欠佳] → 退化時可調 `RRF_K`，留 config 外露。
- [BM25 index 重建時間隨文件量成長] → 增量重建留待後續 change；現況數千文件全量重建可接受。
- [in-process BM25 共享記憶體於多 worker 並發] → RQ worker 與 web 各自持有 instance；語料為唯讀，無鎖爭用；同步寫入僅在 admin 上傳任務序列中執行。
- [rank_bm25 中文分詞為空白切分] → 短階段可接受；長期見 Non-Goals，另立 change 處理。
