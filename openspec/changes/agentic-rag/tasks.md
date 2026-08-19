## Tasks

- [ ] 1. 新增 `langgraph` 至 `requirements.in`，重新編譯 `requirements.txt`
- [ ] 2. 新增 `src/agent_graph.py`：定義 State schema、router / retrieve / grade_documents / rewrite / generate 節點、conditional edges
- [ ] 3. 修改 `src/config.py`：新增 `AGENT_MAX_RETRIES`、`GRADE_SCORE_THRESHOLD` 設定
- [ ] 4. 修改 `src/services.py`：新增 agent graph singleton provider
- [ ] 5. 修改 `src/rag_pipeline.py`：`chat_stream` 改為呼叫 agent graph，移除 `rewrite_query` 函式與 intent classifier 呼叫
- [ ] 6. 移除 `src/intent_classifier.py`（確認無其他模組引用後）
- [ ] 7. 新增 `tests/test_agent_graph.py`：測試各節點與路由邏輯
- [ ] 8. 更新 `README.md`：反映新的 agentic RAG 架構
- [ ] 9. 驗證串流回應（streaming）在 Gradio UI 正常運作
- [ ] 10. 執行 RAGAS 評估確認回答品質未退步
