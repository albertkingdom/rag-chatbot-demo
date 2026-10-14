## Problem

語意快取在快取累積超過 5 筆之後，只有「任意 5 筆」有機會命中，其餘條目永遠不會被比對。快取筆數為 N 時，同一問題再次出現的命中率約為 5/N（均勻抽問的模擬推估：N=20 約 25%、N=126 約 4%）。每次該命中卻未命中，就多跑一次檢索、重排與 LLM 生成，延遲與費用都增加。相似度門檻 0.95 不變，所以問題在命中率，而不是錯誤命中。

## Root Cause

1. 快取索引是 Redis Set，沒有順序。查詢時把 smembers 的結果轉成 Python list 後只取前 5 筆，這 5 筆取決於 Python 對 bytes 的雜湊值：不同行程（雜湊種子隨機化）不同，新增一個 key 後也可能改變。同一行程且內容不變時則固定，所以同一個問題會「固定命中」或「固定不命中」。
2. 索引只增不減：寫入沒有筆數上限也沒有淘汰機制；過期條目只有在剛好被抽進那 5 筆時才會被移出索引。
3. 初始設計的意圖是「只查最近的項目以控制成本」，但 Set 沒有「最近」的概念，限制沒有達到原意。

## Proposed Solution

- 索引改為 Redis Sorted Set，以寫入時間（浮點秒數，時鐘可注入以利測試）為 score，使用新的 key 名稱，避免與既有 Set 型別衝突而產生 WRONGTYPE 錯誤。
- 查詢時取最近 CACHE_MAX_ENTRIES 筆（預設 50），索引為空時不發出任何讀取，否則以單一 MGET 讀回；已過期、損壞或結構異常的單筆條目被略過，不會讓整次查詢失敗；已過期、無法解析、或答案與來源欄位無效的條目順手移出索引。
- 寫入時以交易完成「登記、找出超額的最舊條目、從索引移除」，再刪除這些被移除條目的資料 key，使刪除的資料 key 恰好等於這次移除的成員，避免孤兒資料與誤刪其他寫入者剛登記的條目。
- CACHE_MAX_ENTRIES 必須是不小於 1 的整數；未設定時採用預設值 50（不警告，正式環境不傳這個變數）；設定了但不是整數或小於 1 時採用預設值並記錄警告，不讓應用程式因快取設定而無法啟動。
- 清除快取與統計改讀新索引；清除時一併處理舊 Set 索引與其登記的資料 key。
- 不遷移舊索引：部署後快取等同冷啟動，舊資料 key 在 24 小時內自然過期。
- 新增可提交的量測腳本，以合成資料量測解析與比對成本（不連 Redis），並把結果記在 design。
- 設定 CACHE_MAX_SEARCH_RESULTS 以 CACHE_MAX_ENTRIES 取代，語意由「比對筆數」改為「索引上限，同時也是比對範圍」。

## Non-Goals

- 不改相似度門檻（0.95，嚴格大於）、TTL（24 小時）與命中後 hit_count 的行為。
- 不改 embedding 的儲存格式，不使用 Redis 向量搜尋；行程內快取已解析向量、緊湊編碼 embedding 列為後續項目。
- 不修改聊天服務層，因此不處理「在 async 路徑中同步呼叫 Redis 會阻塞事件迴圈」這個既有問題，只在風險中記錄並列為後續項目。
- 不處理「低分檢索答案不應寫入快取」（由 handle-low-confidence-retrieval 處理），也不在手冊同步後清除快取。
- 不把 CACHE_MAX_ENTRIES 接進 docker-compose.yml 與 Terraform 的環境變數清單；它在本機與測試可由環境變數覆寫，正式環境調整需另行接線。
- 不變更 PromptCacheService 對外介面的既有方法簽章與回傳內容；建構子只新增兩個有預設值的選用參數（時鐘與上限，供測試注入）。

## Success Criteria

- 索引內有 20 筆條目時，任何一筆（包含最舊的）都能被命中，與插入順序及行程無關。
- 寫入使索引超過上限後，索引筆數等於上限，被淘汰條目的資料 key 已被刪除，且每次寫入刪除的資料 key 恰好等於該次從索引移除的成員。
- 一次查詢即可把索引內所有已過期的條目移出索引；索引為空時查詢不發出讀取指令。
- 單筆條目損壞（非 JSON、非物件、缺少或無效的 embedding、維度不符、答案或來源缺漏或型別無效）不會讓整次查詢失敗；既有值損壞也不會讓同一問題無法再寫入快取。
- CACHE_MAX_ENTRIES 未設定時為 50；設為非整數、0 或負數時採用 50 並記錄警告。
- 部署時舊的 Set 索引仍存在，不會造成 WRONGTYPE 錯誤，快取讀寫正常。
- 既有行為不變：同一問題重複寫入時保留 hit_count、sources 寫入後可讀回。
- 量測腳本已提交，結果記錄在 design；架構文件已更新，已知問題表改為反映目前仍存在的問題。

## Capabilities

### New Capabilities

- `semantic-cache-lookup`: 語意快取的查詢涵蓋範圍、索引上限與驗證、淘汰、過期與損壞條目處理，以及舊索引相容性。

### Modified Capabilities

(none)

## Impact

- Affected specs: semantic-cache-lookup（新增）；cache-key-stability 的需求不變。
- Affected code:
  - Modified: src/cache_service.py、src/config.py、tests/test_cache_service.py、architecture.md、.env.example
  - New: scripts/benchmark_cache_lookup.py
  - Removed: 設定項 CACHE_MAX_SEARCH_RESULTS（由 CACHE_MAX_ENTRIES 取代）
- 合併順序：建議在 handle-low-confidence-retrieval 之後合併，或兩者同版部署。查詢範圍擴大後，手冊同步前寫入的舊答案與低分答案會在 24 小時內被穩定命中，而目前的同步流程不會清除快取。
