## Tasks

- [ ] 1. 新增 `src/query_router.py`：LLM 路由模組（structured output），判斷 rag/direct 並完成查詢改寫
- [ ] 2. 新增 `src/retrieval_grader.py`：品質評估函式（reranker score）+ 改寫重試函式
- [ ] 3. 修改 `src/config.py`：新增 `GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES` 設定
- [ ] 4. 修改 `src/rag_pipeline.py`：整合 router + 品質評估 + 重試迴圈；移除 intent classifier 呼叫與 `rewrite_query`
- [ ] 5. 移除 `src/intent_classifier.py`（確認無其他模組引用後）
- [ ] 6. 清理 `src/services.py`：移除 intent classifier 相關 provider
- [ ] 7. 新增 `tests/test_query_router.py`：測試路由判斷邏輯
- [ ] 8. 新增 `tests/test_retrieval_grader.py`：測試品質評估與改寫重試邏輯
- [ ] 9. 更新 `README.md`：反映 agentic RAG 架構（通用助手 + 碳管理專業知識）
- [ ] 10. 驗證串流回應在 Gradio UI 正常運作（rag + direct 兩條路徑）
- [ ] 11. 執行 RAGAS 評估確認碳管理回答品質未退步
