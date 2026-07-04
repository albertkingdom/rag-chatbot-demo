## Why

目前多輪對話的歷史記錄完全依賴 Gradio `ChatInterface` 在瀏覽器端維護、每次請求隨 `history` 參數整包送回伺服器（`src/rag_pipeline.py:217` 的 `chat_stream`）。伺服器端沒有任何地方保存這份歷史：頁面重新整理、瀏覽器分頁關閉重開、或 client 端狀態遺失時，Query Rewriting（`rewrite_query`）與歷史拼接（`format_history`）就完全失去上下文，退化成單輪問答。另外 `src/conversation_db.py` 雖有 MongoDB 儲存每輪對話，但那是供離線分析用的長期記錄（無索引化查詢近期對話的用途），不適合、也不應該在每次請求時同步讀取來重建多輪上下文。需要一個具 TTL 的短期記憶層，讓對話上下文在活躍 session 期間可靠存在，且不依賴 client 端狀態完整送達。

需注意：儲存這份短期記憶的 key，不能用 Gradio 的 `request.session_hash`——已核對 Gradio 前端程式碼，`session_hash` 是每次頁面載入時在瀏覽器端用 `Math.random()` 隨機產生、未存進 cookie/localStorage 的值，頁面重新整理就會換成全新的值，無法跨刷新對應回同一筆 Redis 資料，等於沒解決「頁面刷新後歷史遺失」這個核心問題。改用 `src/access_control.py` 既有的登入 session cookie（`SESSION_COOKIE = "session_id"`，由 `create_session()`/`validate_session()` 簽發與驗證）當 key，才能真正跨頁面刷新存活。

## What Changes

- 新增 Redis 短期對話記憶服務，以「跨頁面刷新存活的 session 識別鍵」為 key 儲存最近幾輪對話（user/assistant 訊息），並設定閒置 TTL，超過 TTL 自動過期釋放。識別鍵優先使用 `request.cookies.get(SESSION_COOKIE)`（`src/access_control.py` 既有的登入 session cookie），僅在該 cookie 不存在時（`AUTH_ENABLED=false` 的本機開發模式）才 fallback 使用 `request.session_hash`。
- `chat_stream`（`src/rag_pipeline.py:217`）改為：請求進來時先從 Redis 讀出歷史（而非只信任 Gradio 傳入的 `history` 參數）供 `rewrite_query` 和 `format_history` 使用；產生完整回覆後（RAG 正常生成或 cache 命中），將這一輪的 user/assistant 訊息寫回 Redis 並刷新 TTL。
- 沿用既有的 `get_redis_conn()`（`src/services.py`）共用連線，不新增獨立的 Redis 連線或設定來源，維持地端/上線 Redis 皆可透過同一組 `REDIS_URL` 運作。
- 沿用 `src/access_control.py` 既有的 `SESSION_COOKIE` 常數（僅 import 讀取，不修改該檔案的登入/session 簽發邏輯）。
- `src/conversation_db.py`（MongoDB）的職責不變，繼續作為長期分析用的完整對話記錄，兩者並存、互不取代。

**追加（第一輪實作後發現的缺口）**：`history_key` 綁定的是登入 session cookie（`SESSION_COOKIE`），其存活期是整個登入 session（`SESSION_TTL_SECONDS`，預設 24 小時），而 Gradio `Chatbot` 元件內建「清除對話」垃圾桶圖示（`chatbot.clear` 事件）目前只會清空前端畫面，完全不會呼叫 `chat_stream` 或碰觸 Redis（已核對 `gradio/chat_interface.py` 原始碼：`chatbot.clear` 綁定的 handler 只是同步前端 state 的 `lambda x: (x, x)`）。這代表使用者點擊「清除對話」明確表達要開新對話時，下一輪提問仍會從 Redis 撈回「舊對話」歷史餵給 query rewriting，違反使用者的明確意圖，比「刷新頁面看不到泡泡」更嚴重。因此追加：

- `ChatHistoryService` 新增 `clear_history(history_key)` 方法，刪除該 session 在 Redis 的 `chat_history:{history_key}` key。
- `src/ui.py` 對 `gr.Chatbot` 的 `clear` 事件額外掛一個 server-side handler，在使用者點擊清除對話圖示時，依同一套 `history_key` 推導邏輯呼叫 `clear_history`，讓「清除對話」同時清空前端畫面與 Redis 的短期記憶。

## Capabilities

### New Capabilities

- `chat-history-persistence`: 以 Redis 為後端、依 session 隔離、具 TTL 的短期對話歷史儲存與讀取行為，供多輪對話的 Query Rewriting 與 prompt 歷史拼接使用。

### Modified Capabilities

(none)

## Impact

- Affected specs: chat-history-persistence（新增）
- Affected code:
  - New: src/chat_history_service.py
  - Modified: src/rag_pipeline.py
  - Modified: src/config.py
  - Modified: src/ui.py
  - Removed: (none)
