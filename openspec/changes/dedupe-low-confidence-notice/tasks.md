## 1. 重現問題的測試

- [x] 1.1 在 `tests/test_chat_service.py` 新增測試，沿用既有 providers 的 monkeypatch 慣例，讓假生成 chain 輸出「固定提示＋空行＋本文」，涵蓋 spec 範例表的各列（rag 低信心的提示在開頭、重複兩次、在結尾、只有提示；rag 高信心的提示在開頭與中間；direct；不含提示的文字原樣保留），滿足 The low-confidence notice is not duplicated 與 Low-confidence answers are labelled and carry no sources 的空生成情境。另加兩個情境：(a) 讓 `get_history` 回傳一筆以固定提示開頭的低信心回答，低信心生成又以提示開頭時，送出的回答、`append_turn` 寫入的歷史與 MongoDB 紀錄都只含一次提示；(b) 語意快取命中、快取答案以提示開頭時，送出的回答與寫入的歷史不含提示。驗證：在尚未修改程式碼前執行這些測試，確認它們因提示重複或殘留而斷言失敗（不是匯入或名稱錯誤），記下失敗訊息供 commit 內文使用。

## 2. 修正

- [x] 2.1 在 `src/rag_pipeline.py` 新增一個函式（放在 `LOW_CONFIDENCE_PREFIX` 旁邊），移除文字中所有與固定提示完全相同的片段；不含提示的文字原樣回傳。函式移除每個完全相同的提示連同緊接其後的空白，有移除時才去除前後空白。在 `src/chat_service.py` 的 rag 與 direct 兩條路徑（模型輸出收完之後、加上提示與輸出防護之前），以及快取命中路徑（取得快取答案之後、輸出防護之前，之後的 PII／注入檢查、MongoDB 紀錄與歷史都用去重後的文字）呼叫它；rag 低信心路徑沿用現有「非空才加提示」的判斷。驗證：1.1 的測試全部通過，`tests/test_chat_service.py` 其餘測試仍通過。
- [x] 2.2 改壞驗證：暫時讓 2.1 的函式直接回傳原文，確認 1.1 的測試失敗後還原。驗證：記錄改壞時失敗的測試名稱與修正後的測試數量，寫進 fix commit 內文。

## 3. 文件與整體驗證

- [x] 3.1 更新 `architecture.md` 第 7 節「低信心」：說明生成後會先移除模型抄寫的固定提示，再由伺服器加上一次，以及 direct、高信心與快取命中的回答也會移除。驗證：內容審查，並執行 `python -m pytest tests -q` 全部通過。

## 4. 從源頭避免抄寫

- [x] 4.1 在 `tests/test_chat_service.py` 新增測試，滿足 The model does not see the notice in the conversation history：讓 `get_history` 回傳一筆使用者訊息與一筆以固定提示開頭的助理回答，分別走 rag 與 direct 路徑，斷言生成輸入（假生成 chain 記錄的 payload）的 `history` 含使用者訊息與助理回答本文、不含固定提示，且 `get_history` 回傳的 list 沒有被修改；另加一個助理回答只有固定提示的案例，斷言該輪仍在、助理文字為空。驗證：修改程式前執行，確認因 `history` 含固定提示而斷言失敗，記下失敗訊息。
- [x] 4.2 在 `src/rag_pipeline.py` 的 `format_history` 中，對每個助理回答套用 `strip_low_confidence_notice` 後再組成文字，使用者訊息不變。驗證：4.1 的測試與 `tests/test_chat_service.py` 全部通過；改壞驗證：暫時拿掉這個呼叫，確認 4.1 失敗後還原；`architecture.md` 第 7 節補一句說明；`python -m pytest tests -q` 全部通過。

