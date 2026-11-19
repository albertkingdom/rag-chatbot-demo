## Context

`src/query_router.py` 的 `route_query` 先跑規則層。規則層判為 rag 或 direct 時，有對話歷史就呼叫 `_rewrite_only`（LLM 純文字輸出改寫結果）；判不出來時呼叫 `_llm_route`（`with_structured_output(RouterResult)`，同時決定路由與改寫）。`chat_service.py` 用 `rewritten_query` 做 embedding、查語意快取與檢索。

## Goals / Non-Goals

**Goals:**

- 不是追問的問題，一字不改地使用原話；是不是追問由結構化欄位決定，採不採用改寫文字由程式決定。
- 兩條路徑行為一致。

**Non-Goals:**

- 減少 LLM 呼叫、改快取查詢方式、調門檻（理由見 proposal）。

## Decisions

### 以結構化欄位判斷追問，由程式決定是否採用改寫

`_rewrite_only` 改用 `get_llm().with_structured_output(RewriteResult)`，`RewriteResult` 有兩個欄位：`is_follow_up: bool`（是否需要對話歷史才看得懂）與 `rewritten_query: str`。`is_follow_up` 為 false 時回傳原話；為 true 時回傳去除前後空白的 `rewritten_query`，空字串則回傳原話；例外時回傳原話並記警告。替代方案是只加強提示詞要求原樣回傳：目前已經這樣要求，模型仍會補內容，不可靠。

### RouterResult 加上 is_follow_up 欄位

`RouterResult` 新增 `is_follow_up: bool`，**沒有預設值、為必填欄位**：有預設值的欄位在 schema 裡不是必填，模型可能省略，真正的追問會無聲地退回原話。規則路由建立 `RouterResult` 時明確給值（`is_follow_up` 依 `_rewrite_only` 的判斷）。`RewriteResult` 的兩個欄位同樣必填。結構化輸出回傳 None、缺欄位或解析失敗時視為呼叫失敗：規則路由回傳原話，`_llm_route` 沿用現有回退（route=direct、原話）。`_llm_route` 取得結果後，若沒有對話歷史或 `is_follow_up` 為 false，把 `rewritten_query` 設為原話；為 true 且改寫為空時也設為原話。提示詞說明：只有在不靠對話歷史就看不懂時（例如有代名詞、省略主詞）才是追問；完整的問題不是追問，不要補上產品名稱或細節。

### 既有測試依新結構調整

受影響的既有測試：`test_rag_rule_route_with_history_rewrites_query` 與 `test_rule_route_with_history_only_calls_rewrite_not_routing` 改成斷言 `with_structured_output` 以 `RewriteResult` 呼叫、且不以 `RouterResult` 呼叫，mock 回傳 `is_follow_up=True`；`test_llm_route_rewrites_in_single_call` 的 mock 補上 `is_follow_up=True`。`is_follow_up` 改為必填後，其他建立 `RouterResult` 的地方也要明確給值：`tests/test_query_router.py` 的 `test_uncertain_query_falls_through_to_llm`、`test_structured_output_format` 與其他直接建立 `RouterResult` 的測試、`tests/test_eval_sample_manual.py` 中建立 `RouterResult` 的假路由，以及 `src/query_router.py` 中 `_llm_route` 的錯誤回退（給 `is_follow_up=False`）。這樣「追問會被改寫」「規則路由不會再走 LLM 路由」的原意都保留，不是放寬斷言。

## Implementation Contract

**行為**

- `route_query(message, history)`：
  - history 為空或 None：不呼叫 LLM 改寫；規則路由時 `rewritten_query == message`；LLM 路由時 `rewritten_query == message`。
  - 有 history、模型判斷 `is_follow_up=false`：`rewritten_query == message`，即使模型輸出的 `rewritten_query` 不同。
  - 有 history、`is_follow_up=true`：`rewritten_query` 為模型輸出去除前後空白；為空則等於 `message`。
  - LLM 呼叫失敗：規則路由時 `rewritten_query == message`；LLM 路由沿用現有的回退（route=direct、`rewritten_query == message`）。
- 規則路由的問題不會以 `RouterResult` 呼叫結構化輸出（不重新走 LLM 路由）。

**驗收**

- `tests/test_query_router.py` 新增的測試在修正前失敗、修正後通過；改壞驗證（讓程式不看 `is_follow_up`）使其失敗。
- 以 2026-11-18 session `DEy0YY` 的對話歷史實測：「忘記密碼怎麼辦？」改寫結果等於原話；一個真正的追問（例如「那要多久？」）被改寫成包含主題的問題。
- `scripts/eval_sample_manual.py` 的路由關卡全部通過（在 demo index 執行，會呼叫付費 API，需使用者同意）。

**範圍內**：`src/query_router.py` 的 `RewriteResult`、`RouterResult`、`_rewrite_only`、`_llm_route`、`route_query`；對應測試；`architecture.md` 路由段落與已知問題表。

**範圍外**：`chat_service.py` 的快取與檢索邏輯、快取門檻、規則層關鍵字。

## Risks / Trade-offs

- [模型把真正的追問判成不是追問] → 會用原話檢索，結果與目前「沒有歷史」時相同；提示詞明確舉出代名詞、省略主詞等追問特徵，並以實測確認一個真正的追問仍被改寫。
- [結構化輸出讓 `_rewrite_only` 的回應格式改變] → 例外一律回到原話，與現行失敗行為相同。
- [`_llm_route` 提示詞變動影響路由決定] → 以範例手冊路由關卡驗證。路由關卡不帶歷史，碰不到追問判斷；依 eval-rag 的判斷不跑 RAGAS：評估流程不帶對話歷史，改寫結果與修改前相同。
- [追問被誤判為非追問時，意思不完整的原話會連同有主題的答案寫進語意快取，之後其他對話可能誤中] → 與修改前「改寫後補錯內容」相比風險相當；提示詞明列追問特徵，並以實測確認真正的追問仍被改寫。之後若觀察到誤中，可只在判斷為非追問時才寫入快取。
- [規則命中 direct 時改寫結果用不到，仍多一次結構化呼叫] → 與修改前相同，不在這次範圍。
