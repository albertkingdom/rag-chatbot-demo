## Why

目前 `HybridRetriever.retrieve()` 把「BM25 + 向量 + RRF 融合」與「BGE Reranker 重排取 Top 3」綁在同一個方法內，導致 retriever 必須依賴 `reranker_scorer`。這違反單一職責：retriever 應只負責候選文件的召回與融合，rerank 屬於下游獨立階段。耦合也讓 unit test 必須 mock reranker、讓 reranker 的載入與重試邏輯無法獨立演化，並阻礙未來替換 reranker 實作（Cohere API、ONNX 等）。

## What Changes

- **BREAKING**：`HybridRetriever.__init__` 移除 `reranker_scorer` 參數；`HybridRetriever` 不再認識 reranker。
- **BREAKING**：`HybridRetriever.retrieve()` 回傳值改為融合後的 top-M 候選（`list[Document]` + `fusion_metadata`），不再回傳 `rerank_scores` 或 top-3 `docs`。
- 新增獨立 rerank 階段：將既有 `src/app.py` 的 `rerank_analysis()` 提升為正式組件，於 `get_retrieval_chain` 內串接在 retriever 之後，輸入 top-M 候選、輸出 top-3 + `rerank_scores`。
- `get_retrieval_chain` 的新管線：「HybridRetriever（BM25+vector+RRF → top-M）→ Reranker（top-M → top-3）」，最終回傳 dict 格式與呼叫端契約不變（`context`、`contexts`、`docs`、`rerank_scores`、`fusion_metadata`）。
- `src/app.py` 的 `get_hybrid_retriever()` 不再傳入 `reranker_scorer`；reranker singleton 改由 rerank 階段直接取用 `get_reranker_model().score`。
- 更新 `tests/test_hybrid_retriever.py`：移除 `reranker_scorer` fixture 與 rerank 相關斷言；新增「回傳 top-M 候選」與「不再執行 rerank」的測試。
- 新增 rerank 階段的 unit test（覆蓋 top-M → top-3 截斷、空候選、score 排序）。

## Non-Goals

- 不改動 BM25Index、RRF 公式、`FUSION_TOP_M` / `BM25_TOP_N` / `VECTOR_TOP_N` / `RRF_K` 參數語意。
- 不替換或移除 BGE Reranker 模型本身，模型載入 singleton 仍由 `get_reranker_model()` 管理。
- 不改動 Generation、Guardrail、Intent Classification、Prompt Cache、MongoDB 日誌階段。
- 不引入 LangGraph 或新框架；rerank 階段以既有 `RunnableLambda` / 純函式實作。
- 不改動 `chat_stream` 對 `get_retrieval_chain()` 的呼叫介面（輸入 query 字串、回傳含 `context`/`contexts` 的 dict）。

## Capabilities

### New Capabilities

- `rerank-stage`: 獨立的 rerank 階段組件，接收 top-M 候選 Document 與 query，以 BGE Reranker `.score()` 計算相關性、排序並截斷至 Top 3，回傳含 `docs`、`rerank_scores` 的 dict。

### Modified Capabilities

- `hybrid-retrieval`: `HybridRetriever` 職責縮減為「BM25 + 向量 + RRF 融合 → top-M 候選」，移除 reranker 依賴與 rerank 行為；`retrieve()` 回傳形狀變更。

## Impact

- Affected specs: `rerank-stage`（新）、`hybrid-retrieval`（修改）
- Affected code:
  - Modified: `src/hybrid_retriever.py`（移除 `reranker_scorer` 參數與 rerank 段）、`src/app.py`（`get_hybrid_retriever`、`get_retrieval_chain`、`rerank_analysis` 整併）、`tests/test_hybrid_retriever.py`
  - New: `src/rerank_stage.py`（`rerank_candidates` 純函式）、`tests/test_rerank_stage.py`（rerank 階段 unit test）
  - Removed: 無檔案刪除
