## 1. Intent classifier 接受並使用對話歷史

- [x] 1.1 修改 `src/intent_classifier.py` 的 `IntentClassifier.classify()`，新增 `history: list = None` 參數，實作「Intent classification incorporates recent conversation history」需求：`history` 非空時在 prompt 中附上最近幾輪對話（沿用 `rag_pipeline.py` 既有的 history 格式與截取邏輯），`history` 為空或未提供時 prompt 內容與現況完全相同；以 `tests/test_intent_classifier.py` 新增測試驗證兩種情境送給 LLM 的 prompt 內容差異，並驗證「Prompt construction with and without history」example 表格中 `[]`、`None`、非空 history 三種輸入對應的 prompt 是否含歷史區塊
- [x] 1.2 同步修改 `IntentClassifier.is_relevant()`，新增 `history` 參數並轉傳給 `classify()`，維持既有的 `confidence_threshold` 判斷邏輯不變；以 `tests/test_intent_classifier.py` 驗證 `is_relevant(question, history=[...])` 會把 `history` 轉傳到底層 `classify()` 呼叫

## 2. chat_stream 傳遞對話歷史給分類器

- [x] 2.1 修改 `src/rag_pipeline.py` 的 `chat_stream`，把呼叫改為 `intent_classifier.classify(rewritten_query, history)`，實作「chat_stream passes conversation history to intent classification」需求，讓分類器跟 `rewrite_query(message, history)` 共用同一份對話歷史；以 `tests/test_rag_pipeline_history.py` 新增測試驗證 `chat_stream` 呼叫 `intent_classifier.classify` 時的第二個參數等於當次使用的 `history`
