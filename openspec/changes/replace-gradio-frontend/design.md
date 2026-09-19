## Background and Goal

現有 Gradio 介面同時承擔 UI 組裝與應用邏輯入口：`src/ui.py` 直接呼叫 `chat_stream`、讀取 Gradio request cookie、複製上傳檔案、enqueue RQ/Cloud Run Job 並 polling。本變更的目標是建立可長期維護的正式 Web 介面，同時讓 RAG 應用服務不再知道 Gradio 的 request 型別或 UI history 形狀。

受影響的使用者包含一般聊天使用者與現有共用金鑰的維運/管理使用者。完成後，他們能在手機與桌面瀏覽器上使用一致介面，並在重新整理頁面後看到同一 session 的對話。工程上則可對 API、前端狀態與業務流程分別測試。

## Scope

### In Scope

- React + TypeScript + Vite SPA 與基礎 design tokens、responsive layout、keyboard/focus 狀態。
- 登入、登出、session 狀態查詢與 401 統一處理。
- 單一當前對話的讀取、聊天、停止當次前端接收、清除、範例問題。
- 結構化回答呈現：正文、參考資料、回應時間、階段狀態與錯誤。
- 管理員手冊上傳、後端驗證、排程結果與狀態輪詢。
- FastAPI API router、request/response schema、NDJSON streaming、同源靜態檔案提供與 SPA fallback。
- 前後端自動化測試、Docker build、文件與切換/回復步驟。

### Non-Goals

- 多帳號、RBAC、SSO、多對話清單與分享。
- 變更 RAG 模型、檢索或資料儲存方案。
- 由 CDN 或獨立前端 host 部署。
- 完整內容管理、檔案清單與刪除。
- 企業級安全與治理：SSO/RBAC、完整 PII/DLP、自動 redaction、分散式 request lock、WAF/IP reputation、惡意檔案掃描與合規稽核。
- 高併發、multi-instance 一致性與容量 SLO；本 side project 維持目前單 instance 部署，預期僅供少量使用者操作。

## Implementation Rationale and Guide

實作時先不要從畫面開始，而是先把 Gradio 隱性提供的 request/streaming 契約明文化。建立一個與 HTTP 無關的聊天應用服務：輸入是 `message` 與 `history_key`，輸出是 typed events。RAG、cache、guardrail、MongoDB 與 Redis 流程維持相同；HTTP router 只負責驗證、狀態碼、序列化與基本 disconnect handling。這使核心流程可在不啟動瀏覽器的情況下測試，也避免日後再次被某個 UI framework 綁定。Stop/斷線在 side-project 範圍內只保證停止前端接收；若後端已完成完整答案，仍可依現有規則持久化，不提供跨 process 的強取消保證。

API 使用 `/api/v1` 版本前綴。聊天採 `POST` + `application/x-ndjson` 而非 browser `EventSource`，因為問題文字屬於 request body，不應放在 URL；`fetch` 的 readable stream 可逐行剖析 NDJSON，也能用 `AbortController` 取消前端接收。事件會區分 `status`、`delta`、`sources`、`metadata`、`done`、`error`，不再用「正在檢索」這類顯示文字猜測流程階段。輸出來源與 timing 也保留結構，不與 Markdown 正文黏在一起。

前端以 feature-oriented 目錄區分 `auth`、`chat`、`admin`，共用 API client 統一處理 credentials、401、429 與錯誤格式。畫面狀態保持簡單：server 的 Redis history 是恢復對話的真實來源，client 只保有目前呈現與正在串流的暫存。不先引入全域狀態框架；當未來出現多對話或複雜 optimistic update 再評估。

產品部署使用 Docker multi-stage build：Node stage 建立 `frontend/dist`，Python runtime 只複製成品。FastAPI 先掛載 API/auth/health，最後才提供 SPA fallback，避免 `/api/*` 錯誤被 `index.html` 遮蔽。切換以不同 image/revision 驗收：舊 revision 保留 Gradio，新 revision 提供 SPA；驗收後才將流量切到新 revision。失敗時將流量切回前一個 image/revision，不要求兩套 UI 同時占用同一個 root path。

## Architecture and Main Decisions

### Frontend stack: React + TypeScript + Vite

- React 適合串流聊天、上傳狀態與組件化介面；TypeScript 可讓前後端 event schema 在 compile time 被檢查。
- Vite 輸出靜態成品，不需 SSR 或 Node production runtime，適合目前單一 FastAPI/Cloud Run 部署。
- 不使用 Next.js：本應用登入後才有主要內容，SEO/SSR 收益小，卻會多一個 server runtime 與部署邊界。

### Same-origin deployment

- Production 的 SPA 與 `/api/v1` 共用 origin，HttpOnly cookie 不需跨網域設定，也減少 CORS 與環境變數錯誤。
- Development 由 Vite proxy `/api` 至本機 FastAPI，前端仍使用相對 URL。

### Structured streaming contract

每行為一個 JSON object，最小事件契約如下：

```json
{"type":"status","stage":"retrieving","message":"正在從知識庫檢索相關資訊…"}
{"type":"delta","text":"密碼"}
{"type":"sources","items":[{"label":"Q: ... A: ..."}]}
{"type":"metadata","elapsedMs":1320,"responseSource":"rag","cacheHit":false}
{"type":"done"}
```

- `delta.text` 是新增字串，前端以 append 處理，不再傳送整個累積回答。
- `status.stage` 是穩定 identifier，`message` 僅用於顯示。
- `sources` 與 `metadata` 不混入 answer Markdown；保留未來將來源變為可點擊物件的空間。
- 預期串流結束順序為 zero-or-more `status`/`delta`，然後 optional `sources`、optional `metadata`、exactly one `done`。無法開始的 request 使用 HTTP error；已開始串流後的錯誤使用 `error` 事件結束。

### Authentication and request security

- Browser 登入 API 收取 `apiKey`，比對後建立 Redis session，只把 opaque `session_id` 放入 `HttpOnly` cookie；前端不儲存原始金鑰。
- Cookie 使用 `HttpOnly` + `SameSite=Lax` + production `Secure`（由設定或 forwarded protocol 決定）。
- Cookie-authenticated 的 state-changing API 驗證 `Origin`/`Referer` 為同源；`X-API-Key` 機器用法保留，不依賴 cookie。
- 錯誤不回傳 raw API key、本機路徑、stack trace 或 provider secret。
- 沿用現有共用 `APP_API_KEY` 與每 key 60 RPM rate limit；本次不拆分 per-session quota 或另做 login IP throttling，視為 side-project 已知限制。
- UI 顯示「請勿輸入密碼、API key、身分證、信用卡等敏感資料」；本次不實作自動 PII/DLP 或 log redaction，正式多人使用前需另案處理。

### Upload safety

- 只允許 `.pdf`、`.xlsx`、`.csv`，後端同時檢查副檔名、MIME 與 configurable maximum bytes；不信任前端 `accept` 屬性。
- 後端對檔名取 basename、產生不衝突的 server-side 名稱，不允許 path traversal 與靜默覆寫既有檔案。
- 上傳完成後才 enqueue；enqueue 失敗時回報一致錯誤並記錄可追蹤 log。
- 本次不做 antivirus、magic-byte 深度鑑識、ZIP bomb 掃描或 per-user job ownership；所有持有共用 key 的登入者都具上傳能力，屬已知限制。

## API Impact

### Authentication

- `GET /api/v1/auth/session`
  - `200`: `{"authenticated": true}`
  - auth disabled 時亦回 `200`，另附 `authEnabled: false`
  - 未登入且 auth enabled: `401`
- `POST /api/v1/auth/login`
  - Body: `{"apiKey":"..."}`
  - `204` + session cookie；無效金鑰 `401`；格式錯誤 `422`
- `POST /api/v1/auth/logout`
  - 撤銷 Redis session、清 cookie，回 `204`；重複登出仍幂等成功。

### Conversation and chat

- `GET /api/v1/conversations/current/messages`
  - `200`: `{"items":[{"role":"user|assistant","content":"..."}]}`
  - 以 authenticated session id 作為 history key，不接受任意 client-supplied Redis key。
  - auth disabled 的本機開發模式不提供持久 history，回傳空 `items`；避免所有匿名使用者共用同一把 key。
- `DELETE /api/v1/conversations/current/messages`
  - 清除當前 session Redis history，回 `204`；沒有紀錄時仍成功；Redis 刪除失敗回安全的 `503`，前端保留現有畫面。
- `POST /api/v1/chat/stream`
  - Body: `{"message":"..."}`，去除前後空白後必須為 1..configured-max characters。
  - `200 application/x-ndjson`：逐行回傳 typed event。
  - 開始串流前可回 `401`/`413`/`422`/`429`/`503`；開始串流後用 `error` 事件。
  - disconnect/abort 時停止往 client 寫入；已開始的 provider 呼叫是否可取消依 SDK 能力。不得寫入半完成 assistant history，但若後端已產生完整且通過 guardrail 的回答，允許依現有規則完成持久化。

### Admin upload

- `POST /api/v1/admin/manuals`
  - `multipart/form-data` 的 `file`。
  - `202`: `{"jobId":"...","status":"queued"}`
  - 無檔案/類型不支援 `422`，過大 `413`，enqueue/provider 不可用 `503`。
- `GET /api/v1/admin/jobs/{job_id}`
  - `200`: `{"jobId":"...","status":"queued|running|succeeded|failed","message":"..."}`
  - 格式錯誤 `422`，不存在 `404`，不可用 `503`。

### Compatibility

- `/health` 維持原樣，不改變 load balancer/Cloud Run probe。
- `X-API-Key` 維持支援 API client。
- 切換後 Gradio 的內部 queue/API 不保證相容，屬於刻意移除的 UI 實作細節。
- 原 `GET/POST /login` 在一個 release 內保留相容 redirect 至 SPA `/login`，之後可另案移除。

## Data Impact

- MongoDB conversation document schema: No impact。
- Redis chat-history/session/rate-limit schema: No impact。新 API 只能以已驗證 session 決定 key。
- Pinecone/vector data: No impact。
- Database migration/seed data: No impact。
- 前端不把 API key、session id 或完整對話存入 `localStorage`。
- 清除對話只清除 Redis chat history 與目前畫面，不刪除既有 MongoDB conversation、prompt cache 或 LangSmith trace；UI 文案必須清楚表達此範圍。

## Business and Security Rules

- Auth enabled 時，除 `/health`、SPA 必要靜態檔案與 login/session bootstrap 外，所有 API 必須驗證。
- 現階段 admin upload 不另分角色，已登入的共用 key 皆可使用；UI 必須明確標示這是管理功能。
- 聊天 history 綁定 server session，client 不能查詢或清除他人的 key。
- 維持現有持久化語意：完整的 RAG/direct/cache 回答寫入 Redis history；prompt cache 僅儲存符合既有條件的完整 RAG 回答；guardrail 事件依現況可寫入 MongoDB 稽核紀錄，但不寫入 Redis history；任何路徑都不得寫入半完成 assistant history。
- Markdown 必須以禁用 raw HTML 的 sanitizer/renderer 呈現，來源欄位以純文字顯示，防止知識庫或 model output 注入 DOM。
- 前端所有操作保持可鍵盤完成，有可見 focus，status/error 透過適當的 `aria-live` 呈現，顏色不是唯一狀態識別。

## Errors and Edge Cases

- 空白訊息、超過 2,000 字元訊息或重複 submit：前端阻擋屬於使用體驗，後端仍獨立驗證；同一頁面在串流期間停用再次送出。本 side project 不實作跨分頁或跨 process 的 distributed request lock。
- 401：前端終止串流、清除內存中的私人狀態並導向 `/login`；不無限重試。
- 429：顯示可重試訊息並採用 `Retry-After`；不自動重送可能造成雙重回答的聊天 request。
- 串流 JSON 行被切在 network chunk 中：parser 必須 buffer 到 newline 才解析，結束時若有非空未完整行視為 protocol error。
- 來源為空：正常完成回答，不呈現空的參考資料區塊。
- Model/provider/Redis/MongoDB 失敗：用戶只看到安全、可理解錯誤；後端 log 保留 correlation id 與技術細節。
- 上傳同名檔案：不覆寫；使用 server-generated 唯一名稱或明確 conflict 規則。
- 輪詢在 job 成功/失敗時終止，頁面 hidden 時降低頻率，逾時後提供手動重查，不永久 polling。
- JavaScript bundle 載入失敗時顯示可辨識的 HTML fallback/重試指引，API path 絕不回傳 SPA `index.html`。

## Scenarios and Acceptance Criteria

### Login and session

- Given auth enabled 且使用者未登入，when 開啟根路徑，then 看到 SPA 登入頁且無法讀取聊天 API。
- Given 正確 `APP_API_KEY`，when 登入，then 取得 HttpOnly session cookie 並進入聊天頁，API key 不出現於 URL、`localStorage` 或 log。
- Given session 失效，when 呼叫受保護 API，then 前端導向登入且不無限重試。

### Chat

- Given 已登入，when 送出問題，then 使用者訊息立即出現，狀態依事件更新，answer delta 依序 append，完成時顯示來源與回應時間。
- Given direct/guardrail 回答無來源，when 完成，then 不顯示空的來源面板。
- Given 聊天已完成，when 重新整理，then `GET .../messages` 恢復同一 session 的完整 user/assistant turns。
- Given 使用者確認清除，when Redis delete 成功，then UI 與 Redis history 同時為空，下一題不使用已清除脈絡；when Redis delete 失敗，then API 回 `503` 且 UI 保留原內容並允許重試。
- Given 使用者 abort 正在接收的回答，when stream 中止，then UI 清楚標示停止接收且 server 不儲存半完成 assistant turn；若 server 已完成完整回答，允許依現有規則持久化並於下次 refresh 顯示。

### Admin upload

- Given 合法支援檔案，when 上傳，then API 回 `202` + job id，UI 顯示 queued/running 並在成功或失敗時終止輪詢。
- Given 檔案不支援、過大或檔名含 traversal，when 上傳，then 後端拒絕，不在資料目錄留下可用檔案且不 enqueue。

### Responsive, accessibility, and deployment

- 在 360px 寬手機與主流桌面寬度下，主要聊天、登入與上傳流程無水平溢出，輸入/動作可見且可操作。
- 手機主要操作的觸控目標至少 44×44 CSS pixels，文字輸入欄至少 16px，開啟虛擬鍵盤時送出按鈕仍可操作。
- 所有主要動作可僅用鍵盤完成，focus 可見，串流狀態與錯誤可被 screen reader 通知。
- 生產 Docker image 啟動後，`/health`、SPA deep link、`/api/v1/*` 均正確，runtime image 不需 Node.js process。
- 新 UI 完成功能同等驗收前 Gradio 仍可回復；驗收通過後不再包含 Gradio runtime dependency。

## Test Strategy

### Backend unit tests

- Typed event builder/serializer：status、delta、sources、metadata、done、error 與 event order。
- Chat application service：RAG/direct/cache/guardrail/error/abort，依現有規則驗證 history/cache/DB 寫入時機，不測試分散式取消或跨 instance lock。
- Upload validation：extension、MIME、size、basename/path traversal、collision、enqueue failure。
- Auth/cookie/origin validation 與 job id 格式 validation；不包含企業級 PII/DLP、WAF、惡意檔案掃描或 per-user job ownership。

### Backend API/controller/security tests

- 各 endpoint 的 2xx/401/409/413/422/429/503、media type、headers 與錯誤 schema。
- NDJSON 在任意 chunk boundary 下仍可還原、stream exception 產生 terminal error event。
- Cookie session 只存取自己的 history，state-changing cookie request 驗證同源。
- Static/SPA fallback 不抵銷 API 404，`/health` 保持公開且輕量。

### Frontend unit/component tests

- NDJSON incremental parser 處理 split lines、multiple lines/chunk、malformed/truncated data。
- Login form、auth guard、chat reducer、status/delta/source/timing 呈現、abort、429 與 401。
- History restore/clear confirm、upload validation、polling termination/timeout。
- Markdown XSS payload 不產生可執行 DOM。

### Integration and E2E

- FastAPI TestClient/async client 以 fake chat service 驗證完整 stream contract。
- Playwright 覆蓋：登入 → 聊天串流 → 來源 → refresh 恢復 → 清除 → 登出。
- Playwright 覆蓋：上傳 → queued/running → success/failure，API 使用 stub/fake job runner，不呼叫真實 LLM/Cloud Run。
- Docker smoke test：build image、`/health`、SPA root/deep link、API 404 media type。
- 手動驗收：360px/桌面、Chrome/Firefox/Safari 當前穩定版、鍵盤流程與基礎 screen-reader smoke test。

## Risks and Trade-offs

- 串流重構可能改變 history/cache/DB 寫入時機：先建 application-service contract tests，再接 API 與 UI。
- 同一 release 同時替換 UI 與傳輸契約風險大：採用 API-first 兩階段切換，在移除 Gradio 前完成 E2E 與 Docker smoke test。
- Node build 增加 CI 時間與 dependency surface：使用 lockfile、固定 Node major version、runtime image 不攜帶 node_modules。
- 共用 `APP_API_KEY` 不是完整企業身分方案：本案清楚標示為相容過渡，未來多人/對外使用前另案引入 SSO/RBAC。
- 本 side project 接受單 instance、共用 60 RPM bucket、所有登入者皆可上傳、Stop 僅停止前端接收，以及 MongoDB/cache/trace 不隨「清除對話」刪除等限制；README 必須列出，若轉為正式多人服務則重新評估。
