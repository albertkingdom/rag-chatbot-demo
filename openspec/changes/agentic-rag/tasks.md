## Tasks

- [ ] 1. 新增 `src/retrieval_grader.py`：品質評估函式（根據 reranker score）+ 改寫重試函式（LLM 改寫查詢）
- [ ] 2. 修改 `src/config.py`：新增 `GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES` 設定
- [ ] 3. 修改 `src/rag_pipeline.py`：在檢索與生成之間插入品質評估與重試迴圈
- [ ] 4. 新增 `tests/test_retrieval_grader.py`：測試品質評估與改寫重試邏輯
- [ ] 5. 更新 `README.md`：反映 self-reflective retrieval 功能
- [ ] 6. 驗證串流回應（streaming）在 Gradio UI 正常運作
- [ ] 7. 執行 RAGAS 評估確認回答品質未退步
