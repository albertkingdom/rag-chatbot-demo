## Context

`HybridRetriever`（`src/hybrid_retriever.py`）目前承擔兩個職責：(1) BM25 + 向量並行檢索後以 RRF 融合出 top-M 候選；(2) 呼叫 BGE Reranker 對候選重新打分並截斷至 Top 3。第二個職責使 retriever 必須在 `__init__` 接收 `reranker_scorer: Callable`，並在 `retrieve()` 內部執行 rerank。

下游呼叫端 `get_retrieval_chain`（`src/app.py`）取得 retriever 結果後直接餵給 generation；同時 `src/app.py` 已存在一個獨立的 `rerank_analysis()` 函式，具備相同的「pairs → score → 排序 → top 3」邏輯，但未被 retrieval chain 使用。這表示 rerank 階段已有可重用實作，只是被複製進 retriever 內部。

技術棧限制：維持單一 Docker 服務、LangChain `RunnableLambda` 管線、BGE Reranker singleton 由 `get_reranker_model()` 管理。

## Goals / Non-Goals

**Goals:**

- `HybridRetriever` 職責縮減為「BM25 + 向量 + RRF 融合 → 回傳 top-M 候選」，不再執行 rerank、不再持有 reranker 參考。
- 將 rerank 提升為獨立、可測試的階段，串接在 retriever 與 generation 之間。
- `get_retrieval_chain()` 對 `chat_stream` 的回傳契約維持不變（`context`、`contexts`、`docs`、`rerank_scores`、`fusion_metadata`）。
- Reranker 模型載入路徑與 singleton 行為不變。

**Non-Goals:**

- 不改 RRF 公式、`FUSION_TOP_M` / `BM25_TOP_N` / `VECTOR_TOP_N` / `RRF_K` 數值或語意。
- 不替換 BGE Reranker 模型；`.score()` 仍為唯一評分入口。
- 不改動 Generation、Guardrail、Intent Classification、Prompt Cache、MongoDB 日誌。
- 不引入 LangGraph 或新框架；rerank 階段以純函式 + `RunnableLambda` 實作。
- 不改 `chat_stream` 對 `get_retrieval_chain().ainvoke(query)` 的呼叫方式。

## Decisions

### Rerank 階段以純函式實作，不引入新類別

採用一個純函式 `rerank_candidates(query: str, candidates: list[Document], scorer: Callable) -> dict`，內部呼叫 `scorer([(query, d.page_content) for d in candidates])`、排序、截斷至 3、回傳 `{"docs": top_docs, "rerank_scores": summary}`。理由：(1) `rerank_analysis()` 已是純函式形態，提升為正式組件無需新類別；(2) 純函式易於 unit test（直接注入 mock scorer）；(3) 避免 over-engineering，reranker 無內部狀態可封裝。捨棄「建立 `Reranker` 類別包裝 scorer」係因類別只會薄包一個函式，不提供額外價值。

### Rerank 階段置於 RunnableLambda 管線中而非回呼函式

`get_retrieval_chain` 改為兩段 `RunnableLambda`：第一段呼叫 `HybridRetriever.retrieve()` 回傳 top-M 候選 + fusion_metadata；第二段呼叫 rerank 函式輸出最終 dict。理由：(1) 與既有 LangChain `RunnableLambda` 模式一致；(2) LangSmith 可分別觀測兩個 span；(3) 純函式可獨立 unit test。捨棄「在 retriever 內部以 callback 鉤子觸發 rerank」係因會重新引入耦合。

### Reranker scorer 仍由 `get_reranker_model().score` 提供

rerank 函式的 `scorer` 參數在 `get_retrieval_chain` 組裝時注入 `get_reranker_model().score`（bound method）。理由：(1) 延續 singleton 共用，避免重複載入 2GB 模型；(2) bound method 符合 `Callable[[list[tuple[str, str]]], list[float]]` 簽章；(3) 修正先前「傳入 `HuggingFaceCrossEncoder` 物件卻當 callable 呼叫」導致 `'HuggingFaceCrossEncoder' object is not callable` 的 bug。

### HybridRetriever.retrieve() 回傳形狀變更為 top-M 候選

`retrieve()` 回傳 `{"candidates": list[Document], "fusion_metadata": dict}`，移除 `docs`、`rerank_scores`、`context`、`contexts`。`context`/`contexts` 的拼裝責任上移至 rerank 階段之後（或由 `get_retrieval_chain` 末段拼裝）。理由：retriever 不應知曉下游如何使用候選（是否 rerank、是否拼成 context 字串）。捨棄「保留 `docs` 欄位但改語意為 top-M」係因欄位名稱會誤導呼叫端。

### 空候選時 rerank 階段短路回傳空結果

當 retriever 回傳空候選（BM25 與向量皆空、或融合後無可用 Document），rerank 函式不呼叫 scorer，直接回傳 `{"docs": [], "rerank_scores": [], "context": "", "contexts": []}`。理由：(1) 避免對空 list 呼叫 scorer 觸發未定義行為；(2) 與既有 `rerank_analysis()` 的空 docs 早回傳行為一致；(3) 下游 generation 收到空 context 時會走「不知道」回應路徑，符合既有 guardrail。

## Implementation Contract

**行為**

- `chat_stream` 呼叫 `get_retrieval_chain().ainvoke(query)` 後，取得的 dict 仍含 `context`（拼裝字串）、`contexts`（list[str]）、`docs`（top-3 Document）、`rerank_scores`（rank/score/content 摘要 list）、`fusion_metadata`（bm25/vector/fusion 三階段觀測資料）。對呼叫端而言行為不變。
- `HybridRetriever` 不再執行 rerank；rerank 由獨立階段完成，Top 3 邊界不變。
- LangSmith 中可分別觀渵「retrieval」與「rerank」兩個 span。

**介面 / 資料形狀**

- `HybridRetriever.__init__(bm25_index, vector_store)` — 移除第三參數。
- `async HybridRetriever.retrieve(query, vector_top_n, bm25_top_n, rrf_k) -> dict` 回傳 `{"candidates": list[Document], "fusion_metadata": dict}`；`fusion_metadata` 保留 `bm25_results`、`vector_results`、`fusion_results`、`fallback`，移除 `rerank_scores`。
- 新增 `rerank_candidates(query: str, candidates: list[Document], scorer: Callable[[list[tuple[str, str]]], list[float]]) -> dict`，回傳 `{"docs": list[Document], "rerank_scores": list}`。`context`/`contexts` 不由 rerank 階段組裝，而由 `get_retrieval_chain` 末段沿用既有 `_format_docs`（以 `metadata["answer"]` 拼裝）產生，保留「LLM 收到答案文字而非問題文字」的現行行為。
- `get_retrieval_chain()` 內部以兩個 `RunnableLambda` 串接：第一段 `retrieve → candidates+fusion_metadata`，第二段 `rerank_candidates → docs+rerank_scores` 再以 `_format_docs` 補上 `context`/`contexts` 並合併 `fusion_metadata` 成最終 dict；`scorer` 於組裝時注入 `get_reranker_model().score`。
- `get_hybrid_retriever()` 不再傳入 `reranker_scorer`。

**失敗模式**

- BM25 不可用時 retriever 走 `vector_only` fallback、於 `fusion_metadata` 記錄，行為與現況一致。
- 候選為空時 rerank 階段短路回傳空 `docs`/`rerank_scores`，不呼叫 scorer、不拋例外；`_format_docs` 後續產出空 `context`/`contexts`。
- Reranker 模型載入失敗時仍由 `get_reranker_model()` 拋出，rerank 階段不額外捕捉；與現況一致。

**驗收準則**

- `tests/test_hybrid_retriever.py`：`HybridRetriever(bm25_index, vector_store)` 可不傳 scorer 實例化；`retrieve()` 回傳 `candidates` 鍵且不含 `rerank_scores`/`docs`；RRF 決定性測試仍通過；fallback 測試仍通過；fusion 截斷至 `FUSION_TOP_M` 測試仍通過。
- `tests/test_rerank_stage.py`（新）：給定 4 個候選與 mock scorer 回傳固定分數，`rerank_candidates` 回傳 top-3 且排序正確；空候選時回傳空結果且不呼叫 scorer；`rerank_scores` 含 rank/score/content 三欄；回傳 dict 恰含 `docs` 與 `rerank_scores` 兩鍵。
- `tests/test_app_pipeline.py` 或既有 app 測試：`get_retrieval_chain().ainvoke(query)` 回傳 dict 含 `context`、`contexts`、`docs`、`rerank_scores`、`fusion_metadata` 五鍵，且 `context` 來自 `metadata["answer"]`。
- 手動驗證：含節號查詢的 Top 3 命中行為與重構前一致。

**範圍邊界**

- In scope：`src/hybrid_retriever.py`（移除 reranker 參數與 rerank 段）、`src/app.py`（`get_hybrid_retriever`、`get_retrieval_chain`、`rerank_analysis` 整併為 rerank 階段）、`tests/test_hybrid_retriever.py`、`tests/test_rerank_stage.py`（新）。
- Out of scope：BM25Index、RRF 公式、config 常數、Generation、Guardrail、Intent Classification、Prompt Cache、MongoDB 日誌、LangGraph 改寫、reranker 模型替換。

## Risks / Trade-offs

- [回傳形狀 BREAKING，下游若直接取 `result["docs"]` 會壞] → 唯一呼叫端是 `get_retrieval_chain`，於同 change 內同步更新；無外部呼叫端。
- [rerank 階段與 retriever 分離增加一次 dict 來回] → 兩段皆 in-process、無網路 I/O，額外開銷可忽略；換得獨立 span 觀測與可測性。
- [既有 `rerank_analysis()` 被其他路徑使用] → 經 grep 確認僅 `get_retrieval_chain` 與 `chat_stream` 互動用到檢索結果；`rerank_analysis` 目前未被 `chat_stream` 直接呼叫，可安全整併。
- [空候選短路可能掩蓋檢索異常] → `fusion_metadata` 仍記錄 bm25/vector 兩路結果與 fallback，異常可由 LangSmith 觀測；短路只跳過 scorer 呼叫，不靜默吞掉檢索失敗。
