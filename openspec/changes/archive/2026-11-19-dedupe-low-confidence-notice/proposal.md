## Problem

同一個對話中，第二次出現低信心回答時，回答開頭會連續出現兩次固定提示「知識庫中查無與此問題直接相關的資料，以下為一般性參考說明，實際操作請以官方操作手冊或系統畫面為準。」。2026-11-18 的本機測試中，session `DEy0YY` 第 3 題（CarbonM 的密碼長度與字元組成有什麼規定？）的 MongoDB 紀錄含兩次提示；同一題在沒有對話歷史的壓測請求中只出現一次。

## Root Cause

低信心回答在加上固定提示後，原樣寫入 Redis 對話歷史。下一題生成時，對話歷史以文字放進 prompt，模型看到上一輪助理回答以這段提示開頭，便模仿格式自行寫出同一段提示；prompt 中的「不需要重複說明知識庫查無資料」沒有被遵守。chat service 生成後又在開頭加上一次提示，結果出現兩次。高信心或 direct 路徑的回答同樣會看到歷史裡的提示，模型若照抄，會讓一般回答被誤標為低信心。

## Proposed Solution

在 rag 與 direct 兩條生成路徑，以及語意快取命中的路徑中，回答文字確定之後、加上固定提示與執行輸出防護之前，先移除所有與固定提示完全相同的文字（連同緊接其後的空白），再去除前後空白；不含提示的文字原樣保留。低信心回答再由伺服器在開頭加上一次提示，因此提示恰好出現一次；高信心、direct 與快取回答不含提示。移除後若輸出為空，低信心回答不加提示（目前程式已是如此，這次一併寫進規格）。

快取路徑也要處理：修正前，高信心回答若抄了提示，會原樣寫進語意快取，之後每次命中都會送出並寫進歷史，繼續被模型抄寫。

移除邏輯集中在 `src/rag_pipeline.py` 的一個函式（與 `LOW_CONFIDENCE_PREFIX` 放在一起），chat service 的兩條路徑都呼叫它。

另外從源頭減少抄寫：把對話歷史組進生成 prompt 時（`format_history`），先移除助理回答中的固定提示，模型就看不到可以模仿的範例；Redis 與 MongoDB 儲存的內容不變。輸出端的去重只能處理一字不漏的抄寫，模型改寫過的提示要靠這一步降低發生機率。

## Non-Goals

- 不改變對話歷史與 MongoDB 紀錄的內容規則：低信心回答仍以含提示的文字寫入歷史與紀錄，與串流給使用者的文字相同。
- 不修改路由器改寫問題時使用的對話歷史（`query_router.py`）：它產生的是檢索用的問題，不是回答，提示不會被抄進回答。
- 不處理措辭相近但不完全相同的提示（模型改寫過的版本）；只移除完全相同的固定文字。
- 不修改 `LOW_CONFIDENCE_NOTE` 的內容。
- 快取答案去重後若變成空字串，維持現有的快取命中處理，不改成視為未命中；高信心答案只有提示的情況極少，不值得多一條分支。
- 不處理同次測試發現的另外兩個問題（手冊有寫的問題被判為低信心、重複問題沒命中語意快取），它們各自另開 change。

## Success Criteria

- 對話歷史中已有低信心回答、模型輸出又以固定提示開頭時，送給使用者、寫入歷史與 MongoDB 的回答都恰好含一次提示。
- 高信心 rag 回答、direct 回答與快取命中的回答中，固定提示被移除，回答不含提示。
- 模型輸出只有固定提示時，低信心回答不加提示（與空生成相同）。
- 沒有抄寫提示的回答內容完全不變。
- 生成 prompt 中的對話歷史不含固定提示，儲存的對話歷史與紀錄仍含提示。
- `python -m pytest tests -q` 全部通過，新增的測試在修正前因此問題失敗。

## Impact

- Affected specs: low-confidence-handling（修改「Low-confidence answers are labelled and carry no sources」補上空生成例外與去重順序；新增「The low-confidence notice is not duplicated」與「The model does not see the notice in the conversation history」）
- Affected code:
  - Modified: src/rag_pipeline.py, src/chat_service.py, tests/test_chat_service.py, architecture.md
  - New: (none)
  - Removed: (none)
