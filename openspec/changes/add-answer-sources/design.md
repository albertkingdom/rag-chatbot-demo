## Context

`get_retrieval_chain()`（`src/rag_pipeline.py`）目前回傳 `context`、`contexts`、`docs`、`rerank_scores`、`fusion_metadata`；`docs` 是 rerank 後排序好的 top-3 `Document`，每個 `Document.metadata` 至少含 `doc_id`、`answer`，部分含 `text`（FAQ 原始問題文字，`src/build_vector_store.py` 寫入），`Document.page_content` 也是原始問題文字（用於 embedding/rerank）。`chat_stream`（`src/rag_pipeline.py`）在 RAG 分支逐字 yield `full_response`；在 cache 命中分支從 `PromptCacheService`（`src/cache_service.py`）取出快取的 `answer` 字串逐字 yield。兩條路徑都需要在回答文字尾端附加來源清單。

## Goals / Non-Goals

**Goals:**

- RAG 生成分支與 cache 命中分支都在回答文字尾端附加去重後的來源清單。
- 快取寫入時一併儲存來源清單，讀取快取時可直接取得，不需重新查詢向量庫。
- 部署時透過一次性 flush 讓新舊快取 schema 不必相容。

**Non-Goals:**

- 不支援舊快取 schema 向後相容讀取（見 proposal Non-Goals）。
- 不新增可點擊來源 UI 元件，僅純文字 markdown。
- 不改變 rerank top-3 截斷或候選數量邏輯。

## Decisions

### 來源清單取自 rerank 後 docs 的 question 欄位，缺值時退回 doc_id

`_rerank` 內部呼叫 `_format_docs` 組出 `context`/`contexts`；新增一個 `_format_sources(docs)` helper，走訪同一份 `ranked["docs"]`，對每個 doc 取 `doc.metadata.get("text") or doc.page_content`（原始問題）與 `doc.metadata.get("answer")`（原始答案），組成 `"Q: <question> A: <answer>"`；超過 100 字元時截斷為前 100 字元並加上 `"..."`（沿用 `rerank_stage.py` 既有的 100 字元截斷慣例）。若問題或答案任一缺值，改用 `doc.metadata.get("doc_id")` 當該筆的來源字串。依序去重（保留首次出現順序）後回傳字串陣列。放進 `_rerank` 回傳的 dict 成為 `sources` 鍵。

替代方案：只顯示 `question` 標題不含 `answer`——使用者反饋希望看到「原始QA內容」以便對照答案是否忠實於知識庫，因此改為顯示問答合併內容；改用 `doc_id` 當唯一鍵、額外查表取得顯示用標題——多一次查詢，且知識庫的 `text`/`answer` 已經是人類可讀文字，不需要額外查表。

### `chat_stream` 附加來源的格式與時機

RAG 分支：`full_response` 生成完畢、guardrail 檢查通過後，若 `sources` 非空，組出 `full_response + "\n\n參考資料：\n" + "\n".join(f"- {s}" for s in sources)`，再逐字 yield 這個組合後的完整字串（取代目前只 yield `full_response`）。`sources` 為空清單時不附加，行為與現在相同。

Cache 命中分支：`cached_response` 除了現有的 `answer`、`similarity`、`hit_count`，新增 `sources` 鍵；比照 RAG 分支的方式組出 `cached_answer + 來源區塊` 後再逐字 yield，取代目前只 yield `cached_answer`。

替代方案：把來源清單當成獨立的 Gradio 訊息或 metadata 附件——需要修改 `ChatInterface` 的訊息結構（`src/ui.py`），超出本次「純文字附加」的範圍（見 Non-Goals）。

### `PromptCacheService` 快取值 schema 新增必要欄位 `sources`

`set_cached_response(question_embedding, message, answer, sources)` 新增第四個參數，寫入快取 hash 時新增 `sources` 欄位（JSON 序列化的字串陣列）。`get_cached_response` 讀取時解析 `sources` 欄位並包含在回傳 dict 中；該欄位視為必要欄位，不做預設值 fallback——因為部署時已一次性 flush，所有存活的快取條目都保證有這個欄位。

替代方案：讀取時對缺欄位的舊快取做 `sources = []` fallback——這會讓「一次性 flush」的部署動作變成非必要的裝飾，且違反 proposal 已確認的 Non-Goals（不做向後相容）。

### 部署時一次性 flush prompt cache namespace

`PromptCacheService` 使用 Redis 儲存快取，key 帶有 `prompt_cache:` 前綴（見 `cache-key-stability` spec）。部署此變更時，於發版流程新增一個手動/腳本化步驟：對 Redis 執行 `redis-cli --scan --pattern 'prompt_cache:*' | xargs redis-cli del`（或等效的批次刪除），在新版本程式碼上線的同一個維護窗口內執行，避免舊 schema 快取值被新程式碼讀到而發生 `KeyError`。

替代方案：讓 `get_cached_response` 捕捉 `KeyError`/缺欄位並當作 cache miss——這是選項 C（lazy migration），已被使用者於討論階段否決，選擇選項 A（flush）。

## Implementation Contract

**行為**：
- 使用者在 Gradio 聊天介面收到的回答文字，只要該次回答有經過 RAG 檢索（無論是即時生成或 cache 命中），文字尾端都會多一段「參考資料：」加上以 `- ` 開頭的來源清單；guardrail、off-topic 分支的回答不受影響，維持原樣。
- 若某次 RAG 檢索的 `sources` 為空清單（理論上不會發生，因為 rerank 恆定回傳最多 3 筆，但保留這個邊界情況），回答文字不附加任何區塊。

**介面 / 資料形狀**：
- `get_retrieval_chain()` 回傳 dict 新增鍵 `sources: list[str]`（去重後的 `"Q: <text/page_content> A: <answer>"` 字串，超過 100 字元截斷加 `"..."`，缺值時退回 `doc_id`，依 rerank 排序）。
- `PromptCacheService.set_cached_response(question_embedding: list[float], message: str, answer: str, sources: list[str]) -> None`：函式簽章新增 `sources` 參數。
- `PromptCacheService.get_cached_response(...)` 回傳 dict 新增鍵 `sources: list[str]`。

**失敗模式**：
- 若 Redis 中殘留未 flush 的舊 schema 快取值（缺 `sources` 欄位），`get_cached_response` 對該筆資料的解析行為視為未定義（不在本次防護範圍內），因為部署步驟已明訂必須先 flush；這不是本次程式碼需要處理的執行期分支。

**驗收標準**：
- `tests/test_rag_pipeline_history.py`（或新增測試）驗證：給定 3 個 rerank 後 docs（含重複 question），`get_retrieval_chain()` 回傳的 `sources` 去重且保留排序。
- `tests/test_rag_stream.py` 驗證：RAG 分支 yield 出的最終累積字串包含「參考資料：」與對應的 `- ` 項目；guardrail/off-topic 分支的 yield 內容不包含該區塊。
- `tests/test_cache_service.py` 驗證：`set_cached_response` 寫入的 `sources` 可被 `get_cached_response` 原樣讀回；cache 命中時 `chat_stream` yield 的最終字串同樣包含來源區塊。

**範圍邊界**：
- 範圍內：`src/rag_pipeline.py`（`_rerank`/`_format_docs`/新 `_format_sources`、`chat_stream` 兩條分支）、`src/cache_service.py`（`set_cached_response`/`get_cached_response` 簽章與 Redis 欄位）、對應測試檔案、部署文件中新增一步「上線前 flush prompt cache」。
- 範圍外：`src/ui.py` 的 `ChatInterface` 結構、rerank 候選數量/排序邏輯、舊快取相容讀取、任何非 FAQ 型來源（外部檔案、URL）。

## Risks / Trade-offs

- [風險] 一次性 flush 若在部署當下漏執行，讀取舊 schema 快取值時 `sources` 欄位缺失，可能拋出未預期例外 → [緩解] 把 flush 指令寫進部署 tasks 並在 code review 中明確列為上線前置步驟，而非程式碼內做防禦性 fallback（維持 Non-Goals 的一致性）。
- [風險] Flush 後短期內 cache miss 率上升，reranker 負載回升（先前已優化到穩態 3.4s/次）→ [緩解] 使用者已確認接受這個代價（討論階段選項 A），不需要程式面額外處理，只需在部署溝通中提醒維運人員預期會有短暫負載上升。
- [風險] `question` 欄位在部分舊資料中可能為空字串（僅有 `answer`）→ [緩解] `_format_sources` fallback 到 `doc_id`，確保清單一律有值可顯示。
