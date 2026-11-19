## 1. 重現問題的測試

- [x] 1.1 在 `tests/test_query_router.py` 新增測試，涵蓋 Self-contained questions keep their original wording 的範例表各列：規則路由與 LLM 路由兩條路徑下，「不是追問但模型輸出不同文字」時改寫問題等於原話；追問時採用去除空白後的改寫；追問但改寫為空時用原話；沒有歷史時不呼叫 LLM；規則路由改寫失敗、回傳 None 或缺判斷欄位時用原話；LLM 路由回傳 None 時沿用回退 direct（這一項修正前就會通過，屬於保護既有行為的測試，不算重現）；LLM 路由沒有歷史時改寫問題等於原話。mock 以 `SimpleNamespace` 表示結構化結果（不 import 尚不存在的 `RewriteResult`），並同時讓 `llm.ainvoke` 回傳「忘記CarbonM的密碼怎麼辦？」，使修正前的純文字路徑也因斷言失敗。驗證：修改程式前執行，確認「不是追問」等新案例因改寫問題不等於原話而斷言失敗（不是匯入或名稱錯誤），記下失敗訊息。
- [x] 1.2 依「既有測試依新結構調整」修改 `tests/test_query_router.py` 的 `test_rag_rule_route_with_history_rewrites_query`、`test_rule_route_with_history_only_calls_rewrite_not_routing`（斷言以 `RewriteResult` 呼叫結構化輸出、不以 `RouterResult` 呼叫，mock 回傳 `is_follow_up=True`）與 `test_llm_route_rewrites_in_single_call`（mock 補上 `is_follow_up=True`），以及所有建立 `RouterResult` 但沒有 `is_follow_up` 的測試（`tests/test_query_router.py`、`tests/test_eval_sample_manual.py`，以 `grep -n "RouterResult(" tests` 找出），滿足 Rule-routed questions are not routed again by the language model。驗證：調整後的測試在修正後通過，測試意圖與原本相同。

## 2. 修正

- [x] 2.1 依「以結構化欄位判斷追問，由程式決定是否採用改寫」與「RouterResult 加上 is_follow_up 欄位」修改 `src/query_router.py`：新增 `RewriteResult`，`_rewrite_only` 改用結構化輸出並依 `is_follow_up` 決定回傳；`RouterResult` 加 `is_follow_up`，`_llm_route` 依歷史與 `is_follow_up` 決定 `rewritten_query`，提示詞說明追問的判斷方式與「完整問題不補內容」。`_llm_route` 的錯誤回退明確給 `is_follow_up=False`。驗證：`python -m pytest tests -q` 全部通過（含 `tests/test_eval_sample_manual.py`）。
- [x] 2.2 改壞驗證：暫時讓兩條路徑都忽略 `is_follow_up`（一律採用模型文字），確認 1.1 的「不是追問」案例失敗後還原。驗證：記錄失敗的測試名稱與修正後的測試數量，寫進 fix commit 內文。

## 3. 實測與文件

- [x] 3.1 實測（會呼叫付費 API，**先取得使用者同意**）：在本機 web 容器以 2026-11-18 session `DEy0YY` 的對話歷史呼叫 `route_query`，確認「忘記密碼怎麼辦？」的改寫問題等於原話、追問「那要多久？」被改寫成包含主題的問題；對話歷史先存成檔案再使用（Redis 中的歷史可能已過 TTL，MongoDB 紀錄仍在）；端到端確認：同一 session 先問「忘記密碼怎麼辦？」，聊一題後再問一次，第二次 `response_source` 為 cache；並以 `scripts/eval_sample_manual.py` 在 demo index 跑路由關卡（範例手冊與評估集全部為 rag、`ROUTING_NEGATIVES` 都不是 rag）。依 eval-rag 不跑 RAGAS：評估流程不帶對話歷史，改寫結果不受影響。驗證：記錄三項結果；路由關卡全部通過。
- [x] 3.2 更新 `architecture.md` 的「混合路由與查詢改寫」段落，說明改寫只在判斷為追問時採用、否則使用原話；並移除已知問題表中「相同問題第二次詢問沒有命中語意快取」一列。驗證：內容審查，`python -m pytest tests -q` 全部通過。
