## Context

目前 `chat_stream`（`src/rag_pipeline.py`）完全信任 Gradio `ChatInterface` 從瀏覽器端傳回的 `history` 參數，伺服器端沒有任何地方保存這份歷史。頁面刷新、分頁關閉重開、或 client 端狀態遺失時，`rewrite_query` 與 `format_history` 收到的 `history` 會是空的，多輪對話退化成單輪問答。`src/conversation_db.py`（MongoDB）雖然每輪都存，但那是供離線分析的長期完整記錄，沒有針對「依 session 快速取回近期幾輪」做索引設計，也不適合在每次請求時同步查詢重建上下文。

`src/services.py` 已有 `get_redis_conn()` 共用連線（`REDIS_URL`，見 `src/config.py`），`src/cache_service.py` 的 `PromptCacheService` 是目前唯一使用該連線的 Redis 服務，可作為風格參考（constructor 注入 `redis.Redis`、JSON blob 儲存、例外時 log 並回傳安全預設值）。

**重要限制（決定了 session 識別鍵的選擇）**：Gradio 的 `request.session_hash` 是瀏覽器端 `Client` 物件在建立當下用 `Math.random()` 隨機產生的值，未存進 cookie 或 localStorage（已核對 Gradio 前端 bundle，`localStorage` 使用次數為 0）。也就是說，每次頁面重新整理都會產生全新的 `session_hash`，與上一次的值毫無關聯。若歷史儲存以 `session_hash` 為 key，proposal 想解決的「頁面刷新後歷史遺失」情境會完全命中不到 Redis 裡的舊資料，退化成跟現況一樣——等於沒解決本來要解決的問題。

專案裡已經有一個會跨頁面刷新存活的識別鍵：`src/access_control.py` 的 `SESSION_COOKIE = "session_id"`，是登入時透過 `create_session()` 簽發、寫入瀏覽器 cookie、並在 Redis 以 `session:<sid>` 儲存的識別碼（`validate_session()` 讀取）。這個 cookie 由瀏覽器保存，重新整理頁面時仍會隨請求一併送出。`gr.Request` 是 `fastapi.Request` 的薄包裝（`__getattr__` 會透傳到底層 request），因此在 `chat_stream` 內可用 `request.cookies.get(SESSION_COOKIE)` 取得這個持久化的 session id。

**追加限制（第一輪實作上線後、實測發現的缺口）**：`history_key` 綁定登入 session cookie，其存活期涵蓋整個登入 session（`SESSION_TTL_SECONDS`，預設 24 小時），遠長於一次對話。Gradio `gr.Chatbot` 元件內建「清除對話」垃圾桶圖示會觸發 `chatbot.clear` 事件，已核對 `gradio/chat_interface.py` 原始碼：其既有 handler 只是 `lambda x: (x, x)` 之類的前端 state 同步，完全不會呼叫使用者提供的 `fn`（也就是 `chat_stream`），因此無法單靠修改 `chat_stream` 偵測到「使用者按下清除對話」。若不處理，使用者點擊清除對話後，下一輪提問仍會從 Redis 撈回「舊對話」歷史，違反使用者想開新對話的明確意圖。

## Goals / Non-Goals

**Goals:**

- 新增以「跨頁面刷新存活的 session 識別鍵」為 key、具閒置 TTL 的短期對話歷史儲存，讓 `rewrite_query` 與 `format_history` 在 client 端 history 遺失（含頁面刷新、分頁關閉重開）時仍能取得近期對話上下文。識別鍵優先使用 `request.cookies.get(SESSION_COOKIE)`（`src/access_control.py` 既有的登入 session cookie），僅在該 cookie 不存在時（例如 `AUTH_ENABLED=false` 的本機開發模式，未走登入流程）才 fallback 使用 `request.session_hash`。
- 沿用既有 `get_redis_conn()`，不新增獨立連線或設定來源。
- 對 Redis 故障保持 fail-open：任何 Redis 例外都不應讓 `chat_stream` 整個請求失敗，最差退化成現有「只信任 client history」的行為。
- 使用者點擊 `gr.Chatbot` 內建的「清除對話」垃圾桶圖示時，同步清空該 session 在 Redis 的短期記憶，避免「畫面顯示新對話、但下一輪提問仍延續舊脈絡」的意圖違反。

**Non-Goals:**

- 不修改 `src/conversation_db.py` 或 MongoDB 的長期對話記錄邏輯，兩者職責不變、並存。
- 不修改 `_parse_history_turns` / `format_history` / `rewrite_query` 既有函式簽章與內部行為。
- 不修改 `src/access_control.py` 既有的登入/session cookie 簽發與驗證邏輯，僅讀取其 cookie 名稱常數與 request 上已存在的 cookie 值。
- 不處理多分頁/多裝置間的 session 合併：同一使用者若用不同瀏覽器或已登出重新登入，會拿到不同的 session cookie，視為不同 session，這是既有登入機制的既有限制，不在此變更範圍內處理。
- 不引入分散式鎖或跨請求併發控制。
- 不啟用 Gradio `ChatInterface` 的 `save_history=True`（多對話側邊欄、明確的「New Chat」按鈕）—— 那是更大範圍的 UI 改動，目前專案未使用該功能，本次只處理既有、一定會出現的 `Chatbot` 清除圖示。

## Decisions

### Session 識別鍵的選擇（持久 session cookie 優先於 session_hash）

歷史儲存的 key 使用 `history_key = request.cookies.get(SESSION_COOKIE) or request.session_hash`（`SESSION_COOKIE` 常數從 `src/access_control.py` import，不重新定義）。理由：`SESSION_COOKIE`（`session_id`）是登入時簽發、瀏覽器端保存的 cookie，頁面刷新、分頁關閉重開時仍會隨請求送出，是唯一能達成 proposal「頁面刷新後歷史不遺失」目標的識別鍵；`request.session_hash` 每次頁面載入都會重新隨機產生，無法跨刷新對應到同一把 Redis key（見上方 Context 的核對結果）。當 `AUTH_ENABLED=false`（本機開發、未啟用登入）時沒有此 cookie，此時 fallback 用 `request.session_hash`——這種情境下歷史一樣無法跨刷新存活，但這是本機開發模式的已知限制，不影響正式環境（`AUTH_ENABLED=true`）的行為。

備選方案：僅用 `request.session_hash`（原始設計）— 已排除，理由如上，無法達成 proposal 的核心目標。另一個備選是在 client 端（瀏覽器 localStorage）額外產生一個持久化 id 並隨每次請求送出——需要修改前端/`ui.py` 傳遞邏輯，超出本次「沿用既有機制」的範圍，故不採用。

### Redis 資料結構與 Key 設計

Key：`chat_history:{history_key}`（`history_key` 定義見上一節）。Value：JSON 字串，內容為訊息陣列 `[{"role": "user"|"assistant", "content": str}, ...]`，格式與 Gradio `history` 參數相容，可直接餵給既有的 `_parse_history_turns` / `format_history` / `rewrite_query`。整包用 `SETEX` 覆寫（而非 Redis List + `RPUSH`/`LTRIM`/`EXPIRE`），理由：單一 session 儲存的訊息筆數很少（見下方 `CHAT_HISTORY_MAX_TURNS`），整包讀寫比維護 List 型別再轉換成 dict 更簡單，且與 `PromptCacheService` 既有的 JSON blob 風格一致。

備選方案：Redis List（`RPUSH`/`LTRIM`/`LRANGE`）— 省去每次整包覆寫的頻寬，但需要額外的 `EXPIRE` 呼叫與 bytes→dict 的型別轉換，複雜度增加而筆數很少時效益不明顯，故不採用。

### TTL 與續期策略

新增設定 `CHAT_HISTORY_TTL_SECONDS`（env 同名，預設 `1800` 秒 = 30 分鐘閒置逾期）。只有在 `append_turn` 實際寫入新一輪對話時才用 `SETEX` 重新整包寫入並刷新 TTL（sliding expiration）；`get_history` 讀取不刷新 TTL，避免使用者只讀不寫卻無限延長 session 存活時間。

### 何時讀取 Redis 歷史、何時 fallback 回 client history

`chat_stream` 進入時，先依「Session 識別鍵的選擇」決策算出 `history_key`（`request.cookies.get(SESSION_COOKIE) or request.session_hash`），若 `history_key` 存在，呼叫 `get_history(history_key)`；若回傳非空 list，以其覆蓋（取代）原本 Gradio 傳入的 `history` 參數，作為後續 `rewrite_query`/`format_history` 使用的 effective history。若 Redis 回傳空 list（新 session、TTL 已過期、或 Redis 讀取例外），則 fallback 使用 Gradio 傳入的 `history` 參數 —— 與變更前行為一致，不因 Redis 而讓功能整個失效。

### 哪些回覆要寫回 Redis

只有「RAG 正常生成」與「cache 命中」兩種成功回覆分支，其對話內容視為有效的多輪上下文，寫回 Redis 並續期。Guardrail（prompt injection / PII 攔阻）與 off-topic 的回應不寫入 Redis 歷史：這些是安全機制的固定訊息或拒答，不是使用者實際想延續的對話內容，寫入反而會污染後續 query rewriting 的上下文品質。

### Redis 失效時的容錯（fail-open）

比照 `PromptCacheService` 的作法：`ChatHistoryService.get_history` / `append_turn` 對任何 Redis 例外一律 log warning 後回傳安全預設值（`get_history` 回傳 `[]`，`append_turn` 靜默略過並直接返回），絕不讓 Redis 故障導致 `chat_stream` 拋出例外或請求失敗。

### 清除對話（Chatbot 內建清除圖示）同步清空 Redis 歷史

在 `src/ui.py` 建立 `gr.Chatbot` 元件時保留其變數參照（原本是就地建構後直接傳給 `gr.ChatInterface`），並在 `ChatInterface` 建構完成後，對該 `Chatbot` 物件額外註冊一個 `.clear()` event handler：`clear_chat_history(request: gr.Request) -> None`。這個 handler 依「Session 識別鍵的選擇」同一套邏輯算出 `history_key`，呼叫 `ChatHistoryService(get_redis_conn()).clear_history(history_key)`。

理由：Gradio 允許同一元件的同一事件註冊多個 listener，彼此獨立觸發、互不影響——`ChatInterface` 內建的 `chatbot.clear(...)` listener（只同步前端 state）會繼續正常運作，我們額外掛的 listener 專責清 Redis，兩者不衝突。之所以不能透過修改 `chat_stream` 來處理，是因為 Gradio 內建的清除圖示從不呼叫 `chat_stream`（見上方 Context 的核對結果），必須直接掛在 `Chatbot` 元件的事件上才抓得到「使用者按下清除」這個動作。

備選方案：等待專案未來啟用 `save_history=True`（Gradio 內建的多對話管理，含明確的「New Chat」按鈕與 `conversation_id`），屆時可用其 `new_chat_button.click` 事件取代——已排除，理由是目前專案未使用該功能，屬於更大範圍的 UI 改動，超出本次範圍；且即使日後啟用，`chatbot.clear`（垃圾桶圖示）仍會獨立存在並可被點擊，同樣需要處理。

## Implementation Contract

**Behavior**：

- 同一 `history_key`（優先為登入 session cookie，見「Session 識別鍵的選擇」）在閒置 TTL 內，即使瀏覽器端傳入的 `history` 參數是空的（例如頁面刷新後 Gradio 重新初始化 client state 導致 `session_hash` 換新），伺服器仍能從 Redis 讀回近期歷史，維持多輪對話的 query rewriting 與 prompt 歷史拼接正確運作。
- 新 session（Redis 無資料）或超過閒置 TTL 後，行為退化為「只用 client 端傳入的 history」，與變更前完全相同，不會報錯。
- Redis 不可用時，`chat_stream` 仍可正常完成整個請求（fallback 到 client history，且不寫入歷史），只是失去跨刷新的持久化能力。
- `AUTH_ENABLED=false`（本機開發、無登入 cookie）時，`history_key` fallback 為 `request.session_hash`，此時歷史一樣無法跨頁面刷新存活（與原始設計相同的限制），僅影響本機開發模式，不影響正式環境。
- 使用者點擊 `Chatbot` 內建的清除對話垃圾桶圖示後：畫面立即清空（既有行為不變），且該 session 在 Redis 的 `chat_history:{history_key}` 也會被刪除；下一輪提問不會再從 Redis 撈回清除前的舊對話。

**Interface / data shape**：

- 新增 `src/chat_history_service.py`，`ChatHistoryService` class：
  - `__init__(self, redis_connection: redis.Redis)`。
  - `get_history(self, history_key: str | None) -> list[dict]`：回傳 `[{"role": "user"|"assistant", "content": str}, ...]`；`history_key` 為 `None`、key 不存在、TTL 已過期、或 Redis 例外時一律回傳 `[]`。
  - `append_turn(self, history_key: str | None, user_message: str, assistant_message: str) -> None`：將這一輪追加進既有歷史、依 `CHAT_HISTORY_MAX_TURNS` 只保留最近 N 輪（即最多 `CHAT_HISTORY_MAX_TURNS * 2` 則訊息，超出部分從最舊開始捨棄）、以 `CHAT_HISTORY_TTL_SECONDS` 整包 `SETEX` 寫回刷新 TTL；`history_key` 為 `None` 時為 no-op。
  - `clear_history(self, history_key: str | None) -> None`：刪除 Redis 中 `chat_history:{history_key}` 這把 key；`history_key` 為 `None` 時為 no-op；Redis 例外時 log warning 並吞掉，不向外拋出。
- `src/config.py` 新增兩個設定值：
  - `CHAT_HISTORY_TTL_SECONDS`（env `CHAT_HISTORY_TTL_SECONDS`，預設 `1800`）。
  - `CHAT_HISTORY_MAX_TURNS`（env `CHAT_HISTORY_MAX_TURNS`，預設 `3`）。
- `src/rag_pipeline.py` 的 `chat_stream`：
  - 取得 `request` 後，依 `history_key = request.cookies.get(SESSION_COOKIE) or request.session_hash if request else None` 算出 `history_key`（`SESSION_COOKIE` 從 `src/access_control.py` import），建立 `ChatHistoryService(get_redis_conn())`，呼叫 `get_history(history_key)`；非空則作為後續使用的 effective history 取代傳入的 `history` 參數，其餘沿用原本的 `history` 參數。
  - 在「cache 命中」分支與「RAG 正常生成」分支，各自於產出答案文字後、`return` 前呼叫一次 `append_turn(history_key, message, <該分支的回答文字>)`。
  - Guardrail（prompt injection / PII）與 off-topic 分支、以及最外層 `except` 例外分支，不呼叫 `append_turn`。
  - 既有的 `session_id = request.session_hash if request else None`（用於 MongoDB `conversation_db` 記錄）維持不變，`history_key` 是給 `ChatHistoryService` 專用的獨立變數，兩者不可混用。
- `src/ui.py`：
  - 建立 `gr.Chatbot(height=500)` 時保留變數參照（例如 `chatbot_component = gr.Chatbot(height=500)`），傳入 `gr.ChatInterface(..., chatbot=chatbot_component, ...)`。
  - 新增函式 `clear_chat_history(request: gr.Request) -> None`：依同一套 `history_key` 推導邏輯（`request.cookies.get(SESSION_COOKIE) or request.session_hash if request else None`，`SESSION_COOKIE` 從 `src/access_control.py` import）算出 `history_key`，呼叫 `ChatHistoryService(get_redis_conn()).clear_history(history_key)`。
  - 在 `ChatInterface` 建構完成後，對 `chatbot_component.clear(clear_chat_history, inputs=None, outputs=None)` 額外註冊這個 handler。

**Failure modes**：

- 任何 Redis 例外（連線失敗、逾時等）：`get_history` log warning 後回傳 `[]`；`append_turn`/`clear_history` log warning 後直接返回，三者都不得向外拋出例外或中斷 `chat_stream`／清除對話的前端流程。

**Acceptance criteria**：

- `tests/test_chat_history_service.py`（比照 `tests/test_cache_service.py` 風格，mock `redis.Redis`）覆蓋：
  - `get_history`：無資料回傳 `[]`、有資料回傳正確格式、`history_key=None` 回傳 `[]`、Redis 例外回傳 `[]`。
  - `append_turn`：正常寫入後可被 `get_history` 讀回、超過 `CHAT_HISTORY_MAX_TURNS` 時正確修剪只保留最近 N 輪、每次寫入都以設定的 TTL 呼叫 `SETEX`、`history_key=None` 時不呼叫任何 Redis 寫入方法、Redis 例外時不拋出例外。
  - `clear_history`：對已存在的 key 呼叫後，`get_history` 讀回 `[]`；`history_key=None` 時不呼叫任何 Redis 方法；Redis 例外時不拋出例外。
- 針對 `chat_stream` 的整合驗證（可用 mock `ChatHistoryService`/`get_redis_conn`，並 mock `request.cookies` 內含 `SESSION_COOKIE` 值）：同一 `history_key`（同一 session cookie 值）第二次呼叫時傳入空的 `history` 參數，驗證實際餵給 `rewrite_query`/`format_history` 的 history 是第一輪寫回 Redis 的內容而非空 list；另外驗證 `request.cookies` 沒有 `SESSION_COOKIE` 時會 fallback 使用 `request.session_hash` 作為 `history_key`。
- 針對 `src/ui.py` 的 `clear_chat_history` 函式，新增測試（可 mock `ChatHistoryService`/`get_redis_conn` 與帶 `.cookies`/`.session_hash` 的假 `request`）驗證：呼叫後 `clear_history` 被以正確的 `history_key` 呼叫一次。

**Scope boundaries**：

- 範圍內：新增 `ChatHistoryService`（含 `clear_history`）、Redis key/TTL 設計、`chat_stream` 的讀取與寫回串接、`src/config.py` 新增設定值、`src/ui.py` 對 `Chatbot.clear` 事件額外掛載清除 Redis 歷史的 handler。
- 範圍外：`src/conversation_db.py`（MongoDB 長期記錄）不變；不修改 `_parse_history_turns` / `format_history` / `rewrite_query` 的既有簽章與行為；不新增獨立 Redis 連線或設定來源；不處理多分頁/多裝置的 session 合併；不引入分散式鎖；不啟用 `save_history=True`（多對話側邊欄／明確 New Chat 按鈕）。

## Risks / Trade-offs

- [Risk] 每個 active session 佔用一把 Redis key，session 數量多時記憶體用量隨之增加 → Mitigation：有 TTL 自動釋放，且單一 key 的訊息數上限為 `CHAT_HISTORY_MAX_TURNS * 2`，單一 value 大小可控。
- [Risk] `append_turn` 採整包覆寫（read-modify-write），並非原子操作。由於改用登入 session cookie 當 key（同一次登入下，多個分頁/視窗共用同一 `history_key`），使用者若同時開兩個分頁對同一帳號分別發問，兩個 `chat_stream` 請求可能並發寫入同一把 Redis key，後寫可能覆蓋先寫的那輪對話 → Mitigation：最差情況只是遺失其中一輪的歷史記錄（下一輪的 query rewriting context 不完整），不會造成資料損毀或請求失敗；使用者單一分頁內的操作仍是序列的（送出後等待串流回覆才能再送下一句），多分頁併發屬於邊緣情境，目前風險等級可接受、不引入分散式鎖，後續若需要可加 Redis `WATCH`/樂觀鎖再收斂。
- [Risk] Redis 故障時歷史退化為只用 client history，若同時 client history 也遺失（例如刷新頁面且 Redis 剛好不可用），多輪上下文會完全遺失 → Mitigation：這與變更前的既有行為相同（只是機率疊加），屬於可接受的 fail-open 設計取捨，不在此變更範圍內做額外備援。
- [Risk] `AUTH_ENABLED=false` 時 fallback 回 `request.session_hash`，此模式下歷史依然無法跨頁面刷新存活 → Mitigation：這與本機開發模式沒有登入機制的既有限制一致，正式環境一律 `AUTH_ENABLED=true`，不受影響。
- [Risk] 使用者若開兩個分頁對同一登入 session 操作，其中一個分頁點擊清除對話會把另一個分頁仍在使用的 Redis 歷史一併清掉（因為兩個分頁共用同一 `history_key`）→ Mitigation：這是與「Redis 資料結構與 Key 設計」相同的既有取捨（同登入 session 下多分頁本來就共用一份短期記憶），使用者主動點擊清除的意圖應優先於另一分頁的隱性延續，屬於可接受行為，不在此變更範圍內做分頁級隔離。

## Migration Plan

純新增行為，無需資料遷移：新 key 命名空間 `chat_history:*` 與既有 `prompt_cache:*`、`session:*`（見 `src/access_control.py` 的登入 session）不衝突。兩個新設定值皆有預設值，GCP Cloud Run 環境無需額外設定即可運作；如需調整 TTL 或保留輪數，設定對應環境變數即可。若需要回滾，直接還原程式碼即可，Redis 中殘留的 `chat_history:*` key 會依 TTL 自然過期，無需手動清理。

## Open Questions

（無）
