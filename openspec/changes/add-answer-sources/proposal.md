## Why

目前 `chat_stream` 產生的回答只包含 LLM 生成文字，使用者無法得知答案依據哪些 FAQ 條目，降低答案可信度與可追溯性。加入來源清單可讓使用者驗證答案依據，並協助之後除錯（哪個 FAQ 條目導致了錯誤答案）。

## What Changes

- Retrieval chain（`get_retrieval_chain`）在既有的 `context`/`contexts`/`docs`/`rerank_scores`/`fusion_metadata` 之外，新增組出 `sources`：從 rerank 後 top-3 `docs` 的 `metadata["question"]`（缺少時退回 `doc_id`）去重取值的字串清單，保留原始排序。
- `chat_stream` 在完成 RAG 生成分支的串流後，於回答末尾附加一段來源清單（markdown，例如「參考資料：\n- <question 1>\n- <question 2>」），來源為空清單時不附加該區塊。
- `PromptCacheService` 寫入快取時，於快取值中新增 `sources` 欄位（字串陣列），讀取快取命中時把 `sources` 併入回傳的回答文字（附加來源清單）一併串流輸出。
- **BREAKING**：因快取值 schema 新增必要欄位，部署此功能時需要對 Redis 的 prompt cache namespace 執行一次性 flush，讓所有舊快取條目失效重新產生（同時帶有 `sources`）。舊 schema 的快取值不相容，讀取時一律視為 cache miss 處理不在本次範圍內（見 Non-Goals）。
- Guardrail、off-topic 分支不受影響，不附加來源（本來就沒有走 RAG 檢索）。

## Non-Goals

- 不支援讀取舊版（無 `sources` 欄位）的快取值並向後相容；改用一次性 flush 取代 lazy migration。
- 不引入外部文件/URL 型來源（如 PDF 頁碼、網頁連結）；來源僅限於知識庫內的 FAQ 條目（`question` 文字或 `doc_id`）。
- 不新增可點擊來源的獨立 UI 元件（例如側邊欄、footnote popup）；來源以純文字 markdown 附加於回答文字尾端，複用現有的 Gradio `ChatInterface` 純文字串流。
- 不重新設計 rerank 候選數量或排序邏輯；只讀取既有 `docs` 排序結果來源，不改變 `FUSION_TOP_M`/top-3 截斷行為。

## Capabilities

### New Capabilities

- `answer-sources`: 從 rerank 後的 top-3 文件組出去重來源清單、附加於串流回答尾端，並定義 cache 命中/未命中兩條路徑下皆需附加來源、以及一次性 cache flush 部署步驟的行為契約。

### Modified Capabilities

- `rerank-stage`: 「Retrieval chain assembles context from answer metadata」需求擴充——除了 `context`/`contexts`，檢索鏈最終回傳的 dict 也必須包含 `sources`（去重後的 `question` 清單）。

## Impact

- Affected specs: `answer-sources` (new), `rerank-stage` (modified)
- Affected code:
  - Modified: src/rag_pipeline.py
  - Modified: src/cache_service.py
  - Modified: tests/test_rag_pipeline_history.py
  - Modified: tests/test_cache_service.py
  - Modified: tests/test_rag_stream.py
