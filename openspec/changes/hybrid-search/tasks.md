## 1. 依賴與設定

- [x] 1.1 在 requirements.txt 新增 `rank_bm25` 套件並執行 `pip install -r requirements.txt` 成功；此任務實作 design 的「In-process BM25 via rank_bm25」決策（採純 Python in-process BM25Okapi 實作，不引入外部搜尋引擎）。驗收：`python -c "import rank_bm25; print('ok')"` 印出 ok。
- [x] 1.2 在 src/config.py 新增常數 `BM25_TOP_N=10`、`VECTOR_TOP_N=10`、`RRF_K=60`、`FUSION_TOP_M=10`；驗收：`python -c "from src.config import BM25_TOP_N, VECTOR_TOP_N, RRF_K, FUSION_TOP_M; print(BM25_TOP_N, VECTOR_TOP_N, RRF_K, FUSION_TOP_M)"` 印出 `10 10 60 10`。此任務實作 design 的「檢索參數集中至 config.py」決策，並對應 spec 的「Retrieval parameters are centralized」需求。

## 2. BM25 索引模組

- [x] 2.1 建立 src/bm25_index.py，實作 `BM25Index` 類別與 `build_from_documents(docs: list[Document]) -> None` 方法：以空白切分對每份 Document 的 page_content 做 tokenization，產生 BM25Okapi 語料並以 doc_id（取自 metadata）對應；語料非空時寫入 `models/bm25_corpus.json` 持久化檔。驗收：呼叫 `build_from_documents` 傳入 50 份 Document 後，`models/bm25_corpus.json` 存在且可被 `json.load` 解析。此任務實作 spec 的「Build BM25 index from Q&A documents」需求與 design 的「BM25 索引持久化為 JSON」決策。
- [x] 2.2 實作 `BM25Index.search(query, top_n)` 方法：以同一 tokenizer 切分查詢，回傳 `(doc_id, bm25_score)` 排序列表，長度 ≤ top_n。驗收：在 `tests/test_bm25_index.py` 的 `test_exact_keyword_match_ranks_first` 中，給定含 "4.2" token 的語料，`search("4.2", top_n=3)` 回傳的第一個 doc_id 對應含 "4.2" 的文件且分數 > 0。此任務對應 spec 的「Search returns ranked doc_ids and scores」需求。
- [x] 2.3 實作 `BM25Index.get_doc(doc_id)` 方法：回傳原始 Document（含 page_content 與 metadata），未知 doc_id 回傳 None 且不拋例。驗收：`tests/test_bm25_index.py` 的 `test_get_doc_returns_document` 與 `test_get_doc_unknown_returns_none` 通過。此任務對應 spec 的「Retrieve original document by doc_id」需求。
- [x] 2.4 實作 `BM25Index.__init__` 的冷啟動載入：若 `models/bm25_corpus.json` 存在且可解析則載入語料；不存在或解析失敗時保持未建狀態並在 stdout 印出警告。驗收：`tests/test_bm25_index.py` 的 `test_load_valid_persisted_file`、`test_corrupt_persisted_file_falls_back` 通過。此任務對應 spec 的「Load persisted index on startup」需求與 design 的「BM25 索引持久化為 JSON」決策。

## 3. Hybrid 檢索模組

- [x] 3.1 建立 src/hybrid_retriever.py，實作 `HybridRetriever.__init__(bm25_index, vector_store, reranker_scorer)`，其中 reranker_scorer 為既有的 BGE Reranker score 函式。驗收：模組可 import 且 `HybridRetriever` 可被實例化（注入 stub 元件）。
- [x] 3.2 實作 `HybridRetriever.retrieve(query, vector_top_n, bm25_top_n, rrf_k)` 非同步方法：以 `asyncio.gather` 並行執行 BM25 與向量檢索（design 的「雙路檢索並行呼叫」決策），對兩路結果套用 Reciprocal Rank Fusion `score(d)=Σ 1/(rrf_k + rank_i(d))`（design 的「Reciprocal Rank Fusion 作為融合策略」決策），融合後取前 `FUSION_TOP_M` 筆交給 reranker_scorer 重排取 Top 3，回傳 `{"docs": list[Document], "rerank_scores": list, "fusion_metadata": {...}}`。驗收：`tests/test_hybrid_retriever.py` 的 `test_rrf_fusion_is_deterministic` 給定固定兩路輸入斷言排名可重現；`test_fused_list_truncated_to_cap` 斷言融合清單不超過 FUSION_TOP_M。此任務對應 spec 的「Hybrid retrieval combines BM25 and vector search」、「Fused candidate set feeds existing reranker」需求。
- [x] 3.3 實作 vector-only fallback：當 BM25Index.search 拋 RuntimeError 或語料為空時，捕捉錯誤、改以純向量檢索取得候選、在回傳的 fusion_metadata 記錄 `{"fallback": "vector_only"}`，不向呼叫端拋例。驗收：`tests/test_hybrid_retriever.py` 的 `test_fallback_when_bm25_not_built` 與 `test_fallback_when_corpus_empty` 斷言回傳 docs 來源為向量且 metadata 含 `fallback: "vector_only"`。此任務對應 spec 的「Graceful fallback to vector-only retrieval」需求。
- [x] 3.4 在 retrieve 內將 per-stage 資料寫入 fusion_metadata：鍵含 `bm25_results`、`vector_results`、`fusion_results`、`rerank_scores`，以便 LangSmith 觀察。驗收：`tests/test_hybrid_retriever.py` 的 `test_trace_metadata_contains_all_stages` 斷言回傳 dict 的 fusion_metadata 含上述四鍵。此任務對應 spec 的「Observability of retrieval stages」需求。

## 4. 既有管線接線

- [x] 4.1 修改 src/build_vector_store.py 的 `sync_vector_store`：在 Pinecone upsert/delete 階段完成後，呼叫 `BM25Index.build_from_documents(local_docs)` 同步重建 BM25 索引；若 build 拋例則印出警告但保留已成功的向量同步結果。驗收：`tests/test_bm25_index.py` 的 `test_sync_updates_bm25_index` 注入 fake vector 寫入與 fake BM25Index，斷言 build_from_documents 被以 local_docs 呼叫；`test_sync_build_failure_does_not_break_vector_sync` 斷言 build 拋例時向量同步已完成。此任務對應 spec 的「Synchronize BM25 index during knowledge base sync」需求。
- [x] 4.2 修改 src/app.py 的 `get_retrieval_chain`：改為回傳以 `HybridRetriever.retrieve` 為主的 RunnableLambda，輸出格式維持 `{"context": str, "contexts": list, "rerank_scores": list}`，chat_stream 不需改動。驗收：啟動應用後於 chatbot UI 詢問含節號的問題（如「範疇 4.2 排放」），Top 3 命中正確且可回答；LangSmith span 可見 bm25_results、fusion_results 鍵。此任務整合上述所有需求，完成 hybrid 管線上線。
- [x] 4.3 於 src/app.py 啟動時初始化單例 `_bm25_index`（呼叫建構子觸發冷啟動載入），並提供 `get_bm25_index()` 取得函式，供 sync 與 retrieve 共用。驗收：`python -c "from src.app import get_bm25_index; print(type(get_bm25_index()).__name__)"` 印出 `BM25Index`。此任務對應 design 的「BM25 索引持久化為 JSON」與冷啟動載入決策。

## 5. 觀察與文件

- [x] 5.1 在 architecture.md 的 RAG 核心流程「深度檢索層」段落補上 BM25 + RRF 融合步驟說明，更新 Key Parameters 表加入 BM25_TOP_N、VECTOR_TOP_N、RRF_K、FUSION_TOP_M。驗收：architecture.md 含「BM25」「RRF」字樣且 Key Parameters 表含四新參數（人工審閱）。
- [x] 5.2 更新 TODO.md 將第 5 項「結合 BM25 作關鍵字檢索」標記為 `[done]` 並附實作概要一行。驗收：TODO.md 第 5 項前綴為 `[done]`（人工審閱）。
