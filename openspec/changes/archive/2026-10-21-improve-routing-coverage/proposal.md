## Problem

換成範例手冊後，路由規則層只直接判定手冊 60 題中的 40 題，其餘 20 題交給 LLM 路由，每題多 1.0–2.6 秒。其中「如何修改個人資料？」「如何開啟兩步驟驗證？」被 LLM 穩定判為 `direct`（重跑 3 次皆同），回答不查知識庫、沒有來源，給出通用而非 CarbonM 的步驟。RAGAS 評估直接呼叫檢索 chain、不經過路由，因此分數沒有反映這個問題。

## Root Cause

`RAG_KEYWORDS` 依先前的知識庫挑選，缺少範例手冊常用的詞（廠區、盤查、範疇、基準年、填報、佐證、查證、係數等）。LLM 路由的 prompt 只以「碳盤查、排放、ESG、CarbonM 系統操作等」描述知識庫，沒有說明帳號與安全設定（個人資料、兩步驟驗證）也屬於 CarbonM 系統操作。

## Proposed Solution

- `RAG_KEYWORDS` 補上範例手冊中 CarbonM 專屬的複合詞（例如「範疇一」而非「範疇」、「數據填報」而非「填報」），單字詞在子字串比對下容易撞到一般問題；在一般對話也常見的詞（例如「數據」「個人資料」「報表」「兩步驟驗證」）不加入，因為「iPhone 如何開啟兩步驟驗證？」這類問題會檢索到 CarbonM 的步驟並以高分回答，低信心機制攔不下來。
- LLM 路由 prompt 列出知識庫涵蓋的系統功能類別，並說明：屬於這些功能類別、且沒有指明其他產品的問題判為 `rag`；指明其他產品，或與這些功能無關的一般操作、常識、閒聊判為 `direct`。
- 評估腳本 `scripts/eval_sample_manual.py` 在 RAGAS 前，以實際路由檢查範例手冊全部問題與評估集問題（必須為 `rag`），以及固定的一般問題反例（必須為 `direct`）；LLM 路由出錯時重試一次並與誤判分開回報。
- 單元測試：範例手冊全部問題、評估集問題、前端建議問題經規則層判定後不得為 `direct`；一般問題的反例維持不判為 `rag`。

## Non-Goals

- 不改路由的兩階段架構、RouterResult 結構、改寫邏輯與低信心機制。
- 不追求規則層 100% 涵蓋範例手冊；泛用詞交由 LLM 路由判斷。
- 不調整檢索、生成與快取。

## Success Criteria

- 範例手冊 60 題經 `route_query` 實際路由全部為 `rag`（評估腳本的路由關卡通過）。
- 規則層直接判定範例手冊的題數由 40 提高到至少 42，其餘由 LLM 路由判為 `rag`；新增關鍵字不讓 29 個一般問題反例被規則層判為 `rag`。
- `tests/test_query_router.py` 的一般問題反例（例如「什麼是機器學習？」「推薦一本好看的小說」）仍不判為 `rag`，並新增「iPhone 如何開啟兩步驟驗證？」「Excel 報表怎麼做樞紐分析？」「數據分析要學什麼？」「如何修改 Gmail 的個人資料？」等反例。

## Impact

- Affected specs: `query-routing-coverage`（新增）
- Affected code:
  - Modified: src/query_router.py, scripts/eval_sample_manual.py, tests/test_query_router.py, tests/test_eval_sample_manual.py, architecture.md, README.md
