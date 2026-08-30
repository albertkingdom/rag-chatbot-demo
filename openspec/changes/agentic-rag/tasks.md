## Tasks

### Phase 1：核心模組

- [ ] 1. 新增 `src/query_router.py`：混合路由模組（規則 + LLM fallback），判斷 rag/direct 並完成查詢改寫
  - 加入 `@traceable(name="Query Routing")` 以追蹤 LangSmith
  - **驗證**：
    - `pytest tests/test_query_router.py` — 規則路由測試（碳管理關鍵字 → rag、一般問答 → direct、模糊 → uncertain），全部 mock LLM
    - 本地有 API key 時：`pytest tests/test_query_router.py -m integration` — 真實 LLM 路由測試（uncertain case），確認 structured output 回傳格式正確
    - LangSmith：確認 trace 出現 "Query Routing" run，檢查 input/output 結構

- [ ] 2. 新增 `src/retrieval_grader.py`：品質評估函式（reranker score）+ 改寫重試函式
  - 改寫函式加入 `@traceable(name="Retrieval Grade")` 和 `@traceable(name="Retry Rewrite")`
  - **驗證**：
    - `pytest tests/test_retrieval_grader.py` — mock reranker scores 測試閾值判斷（高分通過、低分觸發重試、邊界值）；mock LLM 測試改寫函式回傳
    - 本地有 API key 時：手動跑改寫函式，確認 LLM 產出合理的改寫查詢
    - LangSmith：確認品質不足時 trace 出現 "Retry Rewrite" run

- [ ] 3. 修改 `src/config.py`：新增 `GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES` 設定
  - **驗證**：`python -c "from src.config import GRADE_SCORE_THRESHOLD, RETRIEVAL_MAX_RETRIES; print(GRADE_SCORE_THRESHOLD, RETRIEVAL_MAX_RETRIES)"` 確認預設值 0.3 和 1

### Phase 2：管線整合

- [ ] 4. 修改 `src/rag_pipeline.py`：整合 router + 品質評估 + 重試迴圈；移除 intent classifier 呼叫與 `rewrite_query`
  - 保留 `@traceable(name="RAG_DEMO Chat")` 最外層 trace，新增子 trace 可在 LangSmith 看到完整流程
  - **驗證**：
    - `pytest tests/test_app_pipeline.py` — 現有 mock 測試仍通過（retrieval chain 邏輯不變）
    - 新增 mock 測試：direct 路徑不走 retrieval、rag 路徑走 retrieval + grade、品質不足觸發重試
    - LangSmith：完整 trace tree 應顯示 `RAG_DEMO Chat → Query Routing → (Retrieve → Retrieval Grade → [Retry Rewrite → Retrieve]) → Generate`

- [ ] 5. 移除 `src/intent_classifier.py`（確認無其他模組引用後）
  - **驗證**：`grep -r "intent_classifier\|IntentClassifier" src/` 回傳空

- [ ] 6. 清理 `src/services.py`：移除 intent classifier 相關 provider
  - **驗證**：`python -c "import src.services"` 無 import error；`grep -r "intent" src/services.py` 回傳空

### Phase 3：測試

- [ ] 7. 新增 `tests/test_query_router.py`：測試路由判斷邏輯
  - 規則路由：碳管理關鍵字（中/英）、一般問答、模糊查詢分類
  - LLM 路由（mock）：structured output 格式驗證、查詢改寫（有/無對話歷史）
  - LLM 路由（integration，需 API key）：真實 LLM 呼叫，驗證 uncertain case 回傳合理路由
  - **驗證**：`pytest tests/test_query_router.py -v` 全部通過

- [ ] 8. 新增 `tests/test_retrieval_grader.py`：測試品質評估與改寫重試邏輯
  - 品質評估：分數高於/低於閾值、空結果處理
  - 重試迴圈：重試次數上限、改寫後重新檢索
  - **驗證**：`pytest tests/test_retrieval_grader.py -v` 全部通過

### Phase 4：文件與端對端驗證

- [ ] 9. 更新 `README.md`：反映 agentic RAG 架構（通用助手 + 碳管理專業知識）
  - **驗證**：人工審查架構圖和功能描述是否正確

- [ ] 10. 驗證串流回應在 Gradio UI 正常運作（rag + direct 兩條路徑）
  - 需要本地環境（有 API key + Redis）
  - **驗證方式**：
    - 碳管理問題（如「什麼是碳盤查？」）→ 走 rag 路徑，有 context 引用
    - 一般問題（如「1+1 等於多少？」）→ 走 direct 路徑，LLM 直接回答
    - 模糊問題（如「永續發展跟企業有什麼關係？」）→ LLM 路由判斷
    - 追問（接續前一問題追問細節）→ 查詢改寫正確
    - LangSmith：每次對話可在 LangSmith dashboard 看到完整 trace tree，確認路由決策和品質評分

- [ ] 11. 執行 RAGAS 評估確認碳管理回答品質未退步
  - 需要本地環境（有 API key + Pinecone）
  - **驗證**：`python -m tests.evaluate_rag`，四項指標（faithfulness、answer_relevancy、context_precision、context_recall）均 ≥ 0.70 閾值
  - 對比改動前後報告（`tests/rag_eval_report.json`），確認分數無顯著下降
