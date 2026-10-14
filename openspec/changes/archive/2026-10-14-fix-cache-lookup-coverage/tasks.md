## 1. 分支

- [x] 1.1 依 CLAUDE.md 分支規則，從 master 建立分支 feature/fix-cache-lookup-coverage 再開始實作；驗證：執行 git branch --show-current 輸出 feature/fix-cache-lookup-coverage。

## 2. 測試基礎

- [x] 2.1 在 tests/test_cache_service.py 新增記憶體內假 Redis，行為貼近真實：成員與值以 bytes 回傳；Sorted Set 同分時依成員字典序、支援負索引；ZREM 與 DEL 帶空參數時報錯；MGET 空清單回傳空清單；交易（pipeline）依序執行、回傳各指令結果並保留交易內的指令清單供測試檢查；對型別為 Set 的 key 執行 ZADD 時拋出類似 WRONGTYPE 的錯誤；記錄各指令呼叫次數；驗證：假 Redis 自身的測試通過（排序與同分規則、負索引、空參數報錯、MGET 空清單、交易順序與內容、型別衝突）。

## 3. 設定

- [x] 3.1 實作「查詢涵蓋整個有上限的索引且上限預設為 50」的設定面，滿足需求 Maximum size setting is validated：在 src/config.py 新增 CACHE_MAX_ENTRIES，把解析邏輯抽成可直接呼叫的函式（未設定時回傳 50 且不警告；非整數或小於 1 時回傳 50 並記錄警告；不丟出例外），此階段保留 CACHE_MAX_SEARCH_RESULTS 以免匯入中斷；驗證：測試直接呼叫解析函式，涵蓋未設定（無警告）、10、0、-3、abc 五種輸入的有效值與警告是否記錄。

## 4. 索引與寫入

- [x] 4.1 實作「索引改為以寫入時間排序的 Sorted Set 並使用新 key 名稱」：PromptCacheService 建構子新增有預設值的選用時鐘與選用上限（上限未提供時於呼叫時讀取設定值）；set_cached_response 先讀既有值保留 hit_count（既有值無法解析時 hit_count 為 0 並照常寫入），再 setex，並以浮點秒數為 score 把資料 key 登記到 prompt_cache:index:zset；同時改寫既有的儲存與去重相關測試；驗證：重寫同一 key 不增筆、成為最新且保留 hit_count；既有值為非 JSON 時寫入成功且 hit_count 為 0；對舊 Set 並存的環境寫入成功；PromptCacheService 以位置參數建構的既有用法不受影響。
- [x] 4.2 實作「寫入以交易淘汰最舊並刪除其資料 key」，滿足需求 Index size is bounded with oldest-first eviction：登記、取出最新 N 筆以外的成員（範圍 0 到 -(N+1)）、以相同範圍移除，三者在同一個交易中依序執行，交易後僅在成員非空時刪除這些成員的資料 key；驗證：時鐘遞增、上限 3 時依序寫入 A、B、C、D 後索引為 B、C、D 且 A 的資料 key 不存在；斷言交易內的指令順序，且刪除的資料 key 恰好等於交易移除的成員；上限從 5 調降到 3 後下一次寫入使索引剩 3 筆；筆數不足上限時不發出任何以空參數呼叫的 DEL 或 ZREM。

## 5. 查詢

- [x] 5.1 實作需求 Lookup covers every indexed entry：get_cached_response 取新索引最近 N 筆（由新到舊），索引為空時不發 MGET，否則單一 MGET 讀回，對全部條目計算 cosine 相似度並回傳嚴格大於門檻且最高的一筆，命中時 hit_count 加 1，回傳鍵不變；同時移除 cache_service 對 CACHE_MAX_SEARCH_RESULTS 的匯入與 src/config.py 中的該常數，並改寫所有會呼叫 get_cached_response 的既有測試（檢索、sources 往返、過期、低相似度、缺 embedding），使它們確實走到相似度比對且沒有任何測試在此階段變紅；驗證：20 筆中最舊一筆命中、三筆相似度 0.96、0.99、0.97 時回傳 0.99、相似度等於門檻時為 miss、索引為空時 MGET 呼叫次數為 0、每次查詢 MGET 只呼叫 1 次；全專案的 src 與 tests 搜尋不再出現 CACHE_MAX_SEARCH_RESULTS；tests/test_cache_service.py 既有案例全數通過。
- [x] 5.2 實作「以單一 MGET 讀回條目並逐筆容錯」，滿足需求 Expired entries are purged during lookup 與 Corrupted entries do not disable the cache：值為 None 的成員移出索引；非 JSON 或非物件的成員略過並移出索引；answer 不是非空字串、或 sources 缺漏或不是字串清單的成員略過並移出索引；缺 embedding、embedding 無效或維度不符的成員只略過；移出索引只在清單非空時發出；驗證：5 筆過期加 1 筆有效時一次查詢後 5 筆都不在索引且有效者可命中；非 JSON、JSON 但非物件、缺 embedding、維度不符、缺 sources、answer 為 null 各一筆搭配一筆有效時，有效者都被回傳，且該移出索引的成員確實被移出。

## 6. 相容性與管理

- [x] 6.1 實作「舊 Set 索引不遷移且清除快取時一併刪除」，滿足需求 Legacy index coexists safely：查詢與寫入不讀寫 prompt_cache:index；clear_cache 刪除新索引、舊 Set 索引與兩者登記的所有資料 key 並回傳去重後的數量；get_cache_stats 改讀新索引；同時改寫既有的管理與統計測試；驗證：假 Redis 預先放入舊 Set 時，寫入與查詢都成功且舊 Set 內容不變；兩個索引共同登記一個資料 key 時，清除後該 key 不存在且回傳數量只計一次；統計只反映新索引。

## 7. 量測與文件

- [x] 7.1 新增 scripts/benchmark_cache_lookup.py 並提交：以合成的 50 筆與 100 筆 1536 維條目（含答案文字）量測 JSON 解析、numpy 轉換與 cosine 比對的耗時，不連 Redis；把結果與環境補記在 design.md「查詢涵蓋整個有上限的索引且上限預設為 50」決策之下；驗證：腳本可執行並輸出兩種筆數的耗時，design.md 出現量測數字；若 50 筆的單次合計耗時超過 50 毫秒，必須把預設上限調降到耗時不超過 50 毫秒的值並同步修改 src/config.py、spec 與 design，否則維持 50。
- [x] 7.2 依 CLAUDE.md 文件規則更新 architecture.md，並在 .env.example 補上 CACHE_MAX_ENTRIES 的說明（以 BM25_RELOAD_INTERVAL_SECONDS 為先例）：語意快取章節改述為 Sorted Set 索引、涵蓋全部有上限的條目與淘汰規則，移除「任意 5 筆」警語；關鍵參數表以 CACHE_MAX_ENTRIES 取代 CACHE_MAX_SEARCH_RESULTS 並註明正式環境尚未接線；已知問題表改為列出「快取查詢與寫入以同步 Redis 呼叫在 async 路徑執行」與「手冊同步後不清除語意快取」兩列，不留空表；驗證：搜尋 architecture.md 不再出現 smembers、CACHE_MAX_SEARCH_RESULTS 與「任意 5 筆」，已知問題表有上述兩列，.env.example 有該變數說明。

## 8. 驗證

- [x] 8.1 在以 numpy、redis、pytest 建立的暫時虛擬環境執行 tests/test_cache_service.py 與設定解析的測試全部通過；驗證：pytest 輸出貼入 PR 說明。
- [x] 8.2 確認「PromptCacheService 對外介面維持不變」並執行完整測試套件：專案映像的 .dockerignore 排除 tests/，所以以掛載 tests 目錄的方式在映像內執行；若本機無法執行，需在 PR 說明逐項註明未執行的測試；驗證：git diff --stat 的清單不含 src/chat_service.py，tests/test_chat_service.py 不經修改即通過，pytest 輸出貼入 PR 說明。
- [x] 8.3 在本機 Redis 做一次手動驗證（寫入超過上限、查詢、舊 Set 並存），確認 redis-py 8.0.1 預設通訊協定下 Sorted Set 與交易指令的行為與假 Redis 一致；驗證：操作與結果記錄在 PR 說明。

## 9. 部署前確認與提交

- [x] 9.1 由維運者確認正式環境 Redis 對單次回應大小與指令數的限制，必要時調整預設值並重新部署，或另案把 CACHE_MAX_ENTRIES 接進 docker-compose.yml 與 Terraform；這是人工步驟，驗證：PR 說明記錄確認結果與最終採用的值。
- [ ] 9.2 提交訊息不得提及任何 AI 工具；提交、推送與開 PR 前先取得使用者確認；驗證：git log 的提交訊息不含 AI 工具名稱，PR 已指定 code review。
