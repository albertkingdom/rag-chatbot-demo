## 1. 路由規則與 prompt

- [x] 1.1 （Rule keywords cover the sample manual；只加 CarbonM 專屬的複合詞）`RAG_KEYWORDS` 新增設計列出的 6 個複合詞；驗證：`tests/test_query_router.py` 新增案例：範例手冊全部問題經 `_rule_route` 至少 42 題為 `rag`、0 題為 `direct`；評估集與前端建議問題 0 題為 `direct`；29 個一般問題反例皆不為 `rag`。
- [x] 1.2 （LLM routing prompt describes the system's features；LLM 路由 prompt 列出系統功能並限定條件）`_llm_route` 的 prompt 列出七類功能、`rag` 條件（碳盤查概念、系統內操作且未指明其他產品）與 `direct` 排除（其他產品、一般 IT／資安概念、無關操作、常識、閒聊）；驗證：`tests/test_query_router.py` 以 mock LLM 擷取 prompt，斷言含上述內容。

## 2. 評估腳本路由關卡

- [x] 2.1 （Evaluation runner checks routing；評估腳本的路由關卡）`run_evaluation` 在同步檢查後、RAGAS 前檢查正反兩組問題，LLM 錯誤重試一次並分開回報；驗證：`tests/test_eval_sample_manual.py` 以 mock 的 `route_query` 測試全部通過、正例被判 `direct`、反例被判 `rag`、服務錯誤重試後仍失敗四種情況。
- [x] 2.2 以實際環境連續執行兩次評估腳本（demo index，`--audit-index carbon-assistant-sample-index`），路由關卡與 RAGAS 都通過；驗證：兩次 log 都有路由關卡通過的訊息，報告 `num_cases` 為 12。

## 3. 文件

- [x] 3.1 `architecture.md` 更新路由規則層涵蓋率、prompt 說明與評估腳本的路由關卡，改寫「規則層關鍵字依舊知識庫挑選」已知問題；`README.md` 的「40 題」改為實測值；驗證：文件數字與實測一致。
