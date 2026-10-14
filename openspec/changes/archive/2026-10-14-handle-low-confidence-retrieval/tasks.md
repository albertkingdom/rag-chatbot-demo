## 1. 分支

- [x] 1.1 依 CLAUDE.md 分支規則，從 master 建立分支 feature/handle-low-confidence-retrieval 再開始實作；驗證：執行 git branch --show-current 輸出 feature/handle-low-confidence-retrieval。

## 2. 測試基礎

- [x] 2.1 先更新 tests/test_chat_service.py 的 fixture，再動任何程式：rerank_scores 改為含 rank 與 score（字串）欄位的真實格式且預設為高分；grade_documents 的替身預設通過但可由測試覆寫；retrieval 的替身可依序回傳不同結果；新增 rewrite_for_retry 的替身；能擷取傳給生成鏈的輸入內容與生成結果；驗證：在尚未修改任何 src 程式的情況下，該檔所有既有案例通過且斷言意圖不變。

## 3. 評分輔助

- [x] 3.1 實作「低信心以最終結果的評分判定並集中在 grade_documents」：在 src/retrieval_grader.py 新增取得 top-1 分數的函式（依 rank 最小者取分、空清單回傳 None），並讓 grade_documents 內部使用它，簽章與回傳不變；驗證：tests/test_retrieval_grader.py 新增案例涵蓋正常、空清單、清單順序無關，以及固定門檻為 0.30 時分數 0.29 與 0.30 兩個函式的結果一致，既有案例全部通過。
- [x] 3.2 修正 src/retrieval_grader.py 檔頭說明，使其寫明重試迴圈位於聊天服務層而不是 rag_pipeline；驗證：內容審查，檔頭不再提到 rag_pipeline 擁有重試迴圈。

## 4. 生成提示詞

- [x] 4.1 實作「低信心答案由伺服器固定加註一句並以提示詞禁止杜撰」的提示詞面：在 src/rag_pipeline.py 新增常數 LOW_CONFIDENCE_PREFIX 與 LOW_CONFIDENCE_NOTE，並讓生成提示詞接受選用輸入 confidence_note、缺省為空字串且放在 system 訊息最後，滿足需求 Low-confidence answers are labelled and carry no sources 中「省略說明的呼叫端仍可運作」與「固定說明不觸發防護規則」；驗證：新增 tests/test_rag_prompt.py，涵蓋未提供 confidence_note 時可渲染、不含說明且渲染結果與修改前逐字相同、提供時說明出現在 system 訊息、傳入的值覆蓋預設值，並對兩個常數執行 detect_prompt_injection 與 detect_pii 皆未命中。

## 5. 聊天服務層

- [x] 5.1 實作「重試保留 top-1 分數較高的檢索結果並與所有已試過的查詢比對」與「改寫查詢與任何已試過的查詢相同時停止重試」，滿足需求 Retry keeps the better retrieval 與 Redundant retries are skipped：只有 top-1 分數嚴格較高的重試結果取代保留結果，空結果視為最低分，改寫結果去除空白後為空或與任何已試過的查詢相同時停止且不檢索，重試次數只計實際執行的檢索，保留結果通過評分時提早結束；驗證：tests/test_chat_service.py 案例涵蓋 0.28 對 0.05 保留原結果、0.10 對 0.45 採用重試、0.20 對 0.20 保留原結果、原結果為空而重試有結果時採用重試、改寫回傳原查詢或空字串時檢索只發生 1 次且重試次數為 0、上限為 2 時第一次重試即通過就不再重試、第二次改寫重複第一次重試查詢時停止且重試次數為 1。
- [x] 5.2 實作「低信心答案由伺服器固定加註一句並以提示詞禁止杜撰」的流程面，滿足需求 Low-confidence answers are labelled and carry no sources：以最終保留結果的 grade_documents 判定低信心，低信心時傳入 LOW_CONFIDENCE_NOTE，在生成後、輸出防護之前把固定說明與空行加在答案開頭，來源視為空；驗證：測試固定門檻為 0.30，涵蓋 0.29 為低信心與 0.30 不是、兩次檢索都沒有文件時為低信心且仍呼叫生成、低信心時事件序列無 sources 且串流文字開頭為固定說明、高信心時 confidence_note 為空字串且答案無固定說明並有 sources。
- [x] 5.3 實作「低信心答案不送來源事件且不寫入語意快取」，滿足需求 Low-confidence answers are not cached 與 High-confidence behavior is unchanged：低信心時不寫快取，歷史與 MongoDB 紀錄寫入含固定說明的最終答案，回應來源維持 rag；若答案被輸出防護攔截則沿用既有行為（防護訊息、回應來源 guardrail、不寫歷史與快取）；高信心時快取照寫；驗證：測試涵蓋低信心時快取寫入次數 0、歷史追加 1 次、歷史與紀錄的文字與串流文字一致、metadata 事件的回應來源為 rag；低信心加上輸出防護攔截時無歷史與快取寫入且紀錄的回應來源為 guardrail；高信心時快取寫入 1 次。
- [x] 5.4 實作「檢索信心記錄到對話紀錄的 metadata」，滿足需求 Retrieval confidence is recorded with each RAG conversation：rag 路徑成功與輸出防護攔截兩種寫入都帶 retrieval 物件（top1_score、attempt_scores、retry_count、kept_attempt、low_confidence、threshold、retry_skipped），防護攔截時保留 pii_type 與 injection_pattern，快取命中與 direct 路徑不帶；驗證：測試固定門檻為 0.30，以 0.10 對 0.45 的重試、0.20 對 0.05 的低信心、重試被略過、輸出防護攔截四種情境檢查儲存的 metadata 每個欄位，並確認 direct 與快取命中的紀錄沒有 retrieval 欄位。

## 6. 文件

- [x] 6.1 依 CLAUDE.md 文件規則更新 architecture.md：Chat 請求流程圖在重試之後加入低信心分支；流程圖之後的說明段落修正「只有 rag 路徑會寫入語意快取」（低信心的答案除外）；第 1 節關於「忘記 Gmail 密碼」的說明註明低信心時的行為；第 6 節說明保留較佳結果與停止重複重試；第 7 節說明固定說明與低信心說明；第 8 節事件表的 sources 列與 metadata 說明補上低信心時的行為與檢索欄位；循序圖中寫入快取與送出來源的步驟標明低信心時省略；Evaluation 段註明評估腳本繞過評分與重試所以不涵蓋低信心路徑；驗證：內容審查，上述位置都有對應描述，且既有「context 不相關時可改用一般知識」的說明仍保留。
- [x] 6.2 更新 README.md 的功能條目「自省式檢索」，補一句低信心時標示查無資料、不顯示來源也不快取；驗證：內容審查，README 只改該條目且不重複 architecture.md 的細節。

## 7. 驗證與提交

- [x] 7.1 執行完整測試套件：專案映像的 .dockerignore 排除 tests/ 與 markdown 檔，所以以掛載 tests 目錄的方式在映像內執行；若本機無法執行，需在 PR 說明逐項註明未執行的測試；驗證：pytest 輸出貼入 PR 說明，tests/test_chat_service.py、tests/test_retrieval_grader.py、tests/test_rag_prompt.py 全數通過。
- [x] 7.2 由使用者執行人工驗證（需要金鑰且會產生費用）：3 個知識庫外的 CarbonM 問題與 3 個知識庫內的問題，確認低信心回答以固定說明開頭、沒有來源、Redis 沒有新增快取條目、MongoDB 紀錄含 retrieval 欄位，知識庫內問題的行為與現在相同；若 scripts/real_rag_smoke.py 的來源斷言在知識庫內問題失敗，視為低信心回歸訊號並記錄；驗證：結果記錄在 PR 說明。
- [ ] 7.3 提交訊息不得提及任何 AI 工具；提交、推送與開 PR 前先取得使用者確認；驗證：git log 的提交訊息不含 AI 工具名稱，PR 已指定 code review。
