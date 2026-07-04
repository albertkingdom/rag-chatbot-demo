## 1. 設定值

- [x] 1.1 在 `src/config.py` 新增 `CHAT_HISTORY_TTL_SECONDS`（env `CHAT_HISTORY_TTL_SECONDS`，預設 `1800`）與 `CHAT_HISTORY_MAX_TURNS`（env `CHAT_HISTORY_MAX_TURNS`，預設 `3`）兩個設定值，落實設計文件「TTL 與續期策略」的決策；驗證：於 Python shell 或測試中 import `src.config` 確認未設環境變數時為預設值、設定環境變數後可覆寫。

## 2. ChatHistoryService（Redis 資料結構、TTL、容錯）

- [x] 2.1 建立 `src/chat_history_service.py`，實作 `ChatHistoryService.__init__(self, redis_connection)` 與 `get_history(self, history_key)`，依設計文件「Redis 資料結構與 Key 設計」使用 key `chat_history:{history_key}`、value 為 JSON 訊息陣列 `[{"role":..., "content":...}, ...]`；完成 Requirement: Session-scoped short-term chat history storage —— `history_key` 對應 key 不存在或已過期時回傳 `[]`，存在時回傳可直接餵給 `format_history`/`rewrite_query` 的訊息陣列；驗證：`tests/test_chat_history_service.py::TestGetHistory` 對應測試通過。
- [x] 2.2 實作 `append_turn(self, history_key, user_message, assistant_message)`，依設計文件「TTL 與續期策略」以 `SETEX` 整包覆寫並用 `CHAT_HISTORY_TTL_SECONDS` 刷新 TTL，並依 `CHAT_HISTORY_MAX_TURNS` 只保留最近 N 輪、捨棄最舊的輪次；完成 Requirement: Chat turns are written back to session history on successful answers 與 Requirement: Stored history is bounded to a maximum number of recent turns；驗證：`tests/test_chat_history_service.py::TestAppendTurn` 涵蓋寫入後可讀回、超過 `CHAT_HISTORY_MAX_TURNS` 時正確修剪、`history_key=None` 時為 no-op，測試通過。
- [x] 2.3 為 `get_history` 與 `append_turn` 落實設計文件「Redis 失效時的容錯（fail-open）」決策，任何 Redis 例外皆 log warning 後回傳安全預設值（`[]` 或直接返回），完成 Requirement: Redis unavailability degrades gracefully without failing the chat request；驗證：`tests/test_chat_history_service.py::TestRedisFailure`（mock Redis 拋出例外）確認兩個方法都不會向外拋出例外。

## 3. 串接 chat_stream

- [x] 3.1 在 `src/rag_pipeline.py` 的 `chat_stream` 中，從 `src/access_control.py` import `SESSION_COOKIE`，算出 `history_key = request.cookies.get(SESSION_COOKIE) or request.session_hash if request else None`，落實設計文件「Session 識別鍵的選擇（持久 session cookie 優先於 session_hash）」決策，完成 Requirement: History key prefers the persistent login session over the ephemeral Gradio session hash；驗證：新增 `tests/test_rag_pipeline_history.py`（用 `unittest.mock.patch` 對 `src.rag_pipeline` 模組內的 `get_redis_conn`、`get_conversation_db`、`get_intent_classifier`、`get_embeddings`、`get_retrieval_chain`、`get_generation_chain` 等 singleton 打樁，並建立一個帶 `.cookies`/`.session_hash` 屬性的假 `request` 物件）斷言：`request.cookies` 內含 `SESSION_COOKIE` 時 `history_key` 取該 cookie 值而非 `session_hash`；`request.cookies` 不含該值時 `history_key` fallback 為 `request.session_hash`。
- [x] 3.2 在 `chat_stream` 中用步驟 3.1 算出的 `history_key` 建立 `ChatHistoryService(get_redis_conn())` 並呼叫 `get_history(history_key)`；落實設計文件「何時讀取 Redis 歷史、何時 fallback 回 client history」決策——回傳非空時以其取代原本的 `history` 參數作為後續 `rewrite_query`/`format_history` 的輸入，回傳空 list 時沿用原本傳入的 `history` 參數；驗證：於 `tests/test_rag_pipeline_history.py` 中模擬同一 `history_key` 第二次呼叫時傳入空 `history` 參數，確認實際餵給 `rewrite_query`/`format_history` 的內容來自 Redis 而非空 list。
- [x] 3.3 在 `chat_stream` 的「cache 命中」與「RAG 正常生成」兩個成功分支，各自於產出答案文字後、`return` 前呼叫一次 `append_turn(history_key, message, <該分支回答文字>)`；guardrail、off-topic 與最外層 `except` 分支不呼叫，落實設計文件「哪些回覆要寫回 Redis」決策；驗證：於 `tests/test_rag_pipeline_history.py` 中對這兩個分支斷言 `append_turn` 被呼叫且參數正確，對 guardrail/off-topic 分支斷言 `append_turn` 未被呼叫。

## 4. 單元測試

- [x] 4.1 新增 `tests/test_chat_history_service.py`，比照 `tests/test_cache_service.py` 的 mock Redis 風格，涵蓋第 2 節所有情境（含 `history_key=None`、Redis 例外、TTL 刷新、修剪邏輯）；驗證：`pytest tests/test_chat_history_service.py -v` 全數通過。
- [x] 4.2 新增 `tests/test_rag_pipeline_history.py`，涵蓋第 3 節所有情境（session 識別鍵優先序、Redis 讀取/fallback、寫回時機）；驗證：`pytest tests/test_rag_pipeline_history.py -v` 全數通過。

## 5. 清除對話同步清空 Redis 歷史

- [x] 5.1 在 `src/chat_history_service.py` 的 `ChatHistoryService` 新增 `clear_history(self, history_key)`，刪除 Redis 中 `chat_history:{history_key}` 這把 key，落實設計文件「清除對話（Chatbot 內建清除圖示）同步清空 Redis 歷史」決策，完成 Requirement: Clearing the visible conversation also clears the session's stored history；`history_key=None` 為 no-op、Redis 例外時 log warning 並吞掉不拋出；驗證：於 `tests/test_chat_history_service.py` 新增 `TestClearHistory`，涵蓋刪除後 `get_history` 讀回 `[]`、`history_key=None` 時不呼叫任何 Redis 方法、Redis 例外時不拋出例外，`pytest tests/test_chat_history_service.py -v` 全數通過。
- [x] 5.2 在 `src/ui.py` 中，將 `gr.Chatbot(height=500)` 的建構結果保留為變數（例如 `chatbot_component`）並傳給 `gr.ChatInterface(..., chatbot=chatbot_component, ...)`；新增函式 `clear_chat_history(request: gr.Request) -> None`，依 `history_key = request.cookies.get(SESSION_COOKIE) or request.session_hash if request else None` 算出 `history_key`（`SESSION_COOKIE` 從 `src/access_control.py` import）並呼叫 `ChatHistoryService(get_redis_conn()).clear_history(history_key)`；在 `ChatInterface` 建構完成後對 `chatbot_component.clear(clear_chat_history, inputs=None, outputs=None)` 額外註冊此 handler，完成 Requirement: Clearing the visible conversation also clears the session's stored history 的 UI 串接；驗證：新增 `tests/test_ui_clear_history.py`，mock `ChatHistoryService`/`get_redis_conn` 與帶 `.cookies`/`.session_hash` 的假 `request`，斷言呼叫 `clear_chat_history(request)` 後 `clear_history` 被以正確的 `history_key` 呼叫一次；`pytest tests/test_ui_clear_history.py -v` 全數通過。
