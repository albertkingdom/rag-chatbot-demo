## Problem

範例手冊裡有答案的問題，常被判為低信心，回答變成一般性說明、不附來源。2026-11-19 的本機基準壓測中，65 題由範例手冊產生的問題有 26 題低信心（40%），遠高於回答品質目標的 10%。例如「CarbonM 的密碼長度與字元組成有什麼規定？」，手冊條目「密碼有什麼規則？」的答案寫了長度與字元組成，但重排分數只有 0.0058（門檻 0.3）。

## Root Cause

知識庫每筆是一組問答，向量與 BM25 只對問題文字建索引，重排也只把使用者問題與手冊的**問題文字**配對評分（`rerank_candidates` 用 `page_content`）。使用者常問答案裡的細節（長度、解鎖方式、有效期限），這些字只出現在答案中，所以正確條目雖然在候選裡，重排分數卻很低，評分判為低信心。

2026-11-19 以 demo index 實測：兩題低信心問題的正確條目都在候選中；離線估算 65 題手冊問題第一次檢索的低信心題數，只比對問題文字為 27 題，比對「問題＋答案」為 6 題，兩者取較高分為 5 題；18 題手冊以外的問題（題庫 `offtopic` 鍵 12 題，加上 6 題：忘記 Gmail 密碼怎麼辦？、iPhone 要怎麼開啟兩步驟驗證？、Excel 怎麼匯出成 PDF？、公司的電費怎麼計算？、碳權交易市場的價格最近如何？、台灣的碳費每噸多少錢？），三種方式誤判通過的題數都是 2 題（既有的關鍵字誤路由），沒有增加。只比對「問題＋答案」會讓 1 題變差（「要怎麼替 CarbonM 帳號加上兩步驟驗證」0.591 → 0.2），取較高分則沒有變差的題目。

## Proposed Solution

重排時，每個候選條目以兩段文字評分：問題文字，以及「問題＋空格＋答案」；條目的分數取兩者較高者，再依此排序、取前 3 名，並作為評分依據。條目沒有答案時只用問題文字。兩段文字在同一次重排呼叫中送出（5 個候選為 10 段，低於託管重排每次 100 段的上限），不增加呼叫次數。

重排階段維持通用：`rerank_candidates` 新增可選參數，讓呼叫端決定每個候選要評分哪些文字，預設仍只用 `page_content`；檢索 chain 傳入「問題、問題＋答案」。

## Non-Goals

- 不改向量與 BM25 的索引內容（候選已包含正確條目）。
- 不調整評分門檻 `GRADE_SCORE_THRESHOLD`。
- 不改 `scripts/compare_rerankers.py` 的比較方式（實驗工具，仍以問題文字比較各家重排器）。
- 不處理「LLM 路由會把 CarbonM 的追問判為 direct」與關鍵字誤路由（「忘記 Gmail 密碼」），另議。

## Risks

- 每次重排送出的文字約為兩倍，延遲可能增加；以 `rag_rerank_duration_seconds` 量測 P95，需遠低於 5 秒逾時。託管重排以請求計費的話次數不變；若依文件數計費，費用約為兩倍。
- 長答案會被託管重排截斷尾端（請求帶 `truncate: END`）；問題文字在前，不受影響。
- `rerank_scores` 的 `content` 仍只顯示問題文字，看不出最高分來自哪一段；這次不補記。

## Success Criteria

- 重排以每個條目兩段文字中較高的分數排序與評分，且只呼叫一次評分函式。
- 以 demo index 重新量測：65 題手冊問題的低信心比例明顯下降（離線估算由 27 題降到約 5 題）。
- RAGAS 評估四項指標都不低於 0.7，與上一份報告的差距在雜訊範圍內。
- `python -m pytest tests -q` 全部通過，新增的測試在修正前因此問題失敗。

## Impact

- Affected specs: rerank-stage（修改「Rerank stage transforms top-M candidates into top-3」與「Rerank stage is resourced for bounded inference latency」；新增檢索 chain 以問題與問題＋答案評分的需求）
- Affected code:
  - Modified: src/rerank_stage.py, src/rag_pipeline.py, tests/test_rerank_stage.py, tests/test_app_pipeline.py, architecture.md, docs/sli-slo.md
  - New: (none)
  - Removed: (none)
