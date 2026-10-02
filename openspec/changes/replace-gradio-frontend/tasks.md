## 1. 確認契約與建立安全網

- [x] 1.1 對照 design 的「In Scope」與「Non-Goals」，以現有 Gradio 功能建立 parity checklist：登入/登出、範例問題、RAG/direct/cache/guardrail 回答、來源、timing、history clear、手冊上傳與 RQ/GCP job 狀態，並確認本案不擴張至多帳號、SSO/RBAC 或多對話。
- [x] 1.2 在新功能 branch `feature/replace-gradio-frontend` 進行實作，確認現有 backend test baseline 通過，並記錄已存失敗。
- [x] 1.3 依「Chat responses use typed NDJSON streaming events」定義 Pydantic event/request/response models 與一致錯誤 schema，以 schema tests 鎖定 API contract。
- [x] 1.4 新增 chat application-service tests，覆蓋 RAG/direct/cache/guardrail/error/abort 與現有 history/cache/MongoDB 寫入時機；不擴張為分散式取消、完整 PII/DLP 或跨 instance lock。

## 2. 解耦聊天業務流程

- [x] 2.1 依「Layered module separation」將 `chat_stream` 拆為不依賴 `gr.Request` 的應用服務，由 caller 傳入 server-derived `history_key`；HTTP 層處理基本斷線偵測。
- [x] 2.2 依「Text responses are streamed in fixed-size chunks」的修訂契約輸出 typed events，將「階段狀態、answer delta、sources、elapsed time/response source/cache hit、done/error」分離，不再把來源與 timing 黏入 answer Markdown。
- [x] 2.3 實作 side-project 範圍的 abort 行為：停止向 client 寫入，半完成 answer 不寫入 history/cache/MongoDB；若 server 已完成完整回答則允許依現有規則持久化。不實作 distributed lock 或跨 instance 強取消。
- [x] 2.4 保留一個暫時 Gradio adapter，讓新 application service 在 SPA 切換前仍可被舊 UI 呼叫，以 parity tests 驗證。

## 3. 建立 FastAPI v1 API

- [x] 3.1 新增 `src/api/` router 與 dependency layer，實作 `POST /api/v1/chat/stream`，正確設定 `application/x-ndjson`、no-buffering headers、newline framing 與 stream error event。
- [x] 3.2 依「Conversation history is owned by the authenticated session」與「Clearing the visible conversation also clears the session's stored history」實作 `GET`/`DELETE /api/v1/conversations/current/messages`，只使用 authenticated session-derived history key；Redis clear 失敗回 `503` 且不得讓 UI 誤判成功，auth-disabled 模式回空 history。
- [x] 3.3 依「Request authentication via API key or session token」、「Browser login endpoint issuing session tokens」與「Browser authentication uses an opaque server session」實作 `GET /api/v1/auth/session`、`POST /api/v1/auth/login`、`POST /api/v1/auth/logout`，設定 HttpOnly/SameSite/Secure cookie 規則並保留 `X-API-Key` 相容。
- [x] 3.4 依「Session token lifecycle and revocation」對 cookie-authenticated state-changing request 實作同源 `Origin`/`Referer` 驗證，測試合法、缺少、異源與 API-key header 情境。
- [x] 3.5 實作 `POST /api/v1/admin/manuals` 與 `GET /api/v1/admin/jobs/{job_id}`，拆出 local RQ/GCP job runner adapter。
- [x] 3.6 依 design 的「Upload safety」與「Manual upload is validated and reports background job status」新增 side-project 基本上傳驗證：allowlist extension/MIME、configurable size limit、basename/唯一 server filename、不覆寫、enqueue 失敗收尾，並覆蓋 413/422/503；不加入 antivirus、ZIP bomb 深度掃描或 per-user job ownership。
- [x] 3.7 依「Backend API/controller/security tests」確認 middleware 對 API 回 JSON/NDJSON 錯誤、對 browser route 交給 SPA；驗證 401/409/413/422/429/503 與 `Retry-After`。

## 4. 建立 React 前端基礎

- [x] 4.1 新增 `frontend/` Vite + React + TypeScript 專案、lockfile、lint/typecheck/test/build scripts，設定 dev proxy 而不寫死 API host。
- [x] 4.2 依「The application provides a responsive standalone web frontend」建立 design tokens、app shell、responsive layout、route/error boundary 與可重複使用的 button/input/status/error 元件。
- [x] 4.3 建立 typed API client 與 NDJSON parser，測試 chunk 切行、單 chunk 多行、malformed line、truncated final line、abort、401 與 429。
- [x] 4.4 實作 `/login` 與 auth bootstrap/guard/logout，確認 API key 不進入 URL、Web Storage 與 client log。

## 5. 實作聊天體驗

- [x] 5.1 實作訊息清單、Markdown 回答（raw HTML disabled + sanitization）、輸入框、submit/IME 行為、範例問題與自動滾動。
- [x] 5.2 實作 typed event reducer，顯示 retrieval/rerank/generation 狀態、incremental answer、sources、elapsed time、cache/source metadata 與 terminal errors。
- [x] 5.3 實作 Stop action 與 incomplete 狀態，避免同一頁面重複 submit，並處理 refresh/navigation 時停止 client 接收；UI 不宣稱一定取消後端工作。
- [x] 5.4 實作初始 history restore 與有確認步驟的 clear flow，只在 server `204` 後清除畫面；文案說明只清除目前對話脈絡，不代表刪除 MongoDB/cache/trace。
- [x] 5.5 新增 component tests 覆蓋空狀態、串流、無來源、error/401/429、history restore/clear、XSS payload 與 keyboard/focus 行為。

## 6. 實作管理上傳體驗

- [x] 6.1 實作明確標示的 Admin 畫面，包含 drag/drop + file picker、支援類型/大小說明、前端即時驗證與 upload progress/state。
- [x] 6.2 實作 job polling，queued/running/succeeded/failed 均有穩定顯示，terminal state 終止、hidden page 降頻、逾時可手動重查。
- [x] 6.3 新增 component/integration tests 覆蓋 unsupported/oversize、upload 202、poll success/failure/404/503/timeout。

## 7. 整合 build 與部署

- [x] 7.1 依「Production serves the frontend and API from one origin」與「No module-level side effects on import」在 FastAPI 先 mount health/auth/API，再 mount hashed assets 與 SPA fallback；測試 `/api/*` 404 不回 `index.html`，SPA deep link 會回 `index.html`，且 runtime 不執行 frontend build。
- [x] 7.2 將 Dockerfile 改為 Node frontend build + Python runtime 多階段，固定 Node major 與 package lock，runtime 不包含 node_modules/Node process。
- [x] 7.3 更新 `docker-compose.yml` 本機開發方式、`.dockerignore`/build context 與 cache layer，依「Compose 共用同步資料」與「Local web and worker share synchronization data」驗證代碼 mount 不遮蔽必要 frontend build，web/worker 共用文件與 BM25 volumes。
- [x] 7.4 新增 Playwright 主流程與 Docker smoke test，使用 fake provider/job runner 避免真實 LLM/GCP 依賴。
- [ ] 7.5 在切換前完成 side-project 基本驗收：XSS、cookie/origin、message 2,000 字邊界、upload traversal/size、1/2 位同時 RAG smoke test，以及前端 bundle 與首次載入檢查；不設定企業級容量 SLO。

## 8. 切換、清理與文件

- [ ] 8.1 依 design 的「Responsive, accessibility, and deployment」與 parity checklist，在 360px/桌面及至少一台實際手機驗收登入、聊天、來源、history、清除、上傳、登出、44px touch target、16px 輸入字體與虛擬鍵盤操作。
- [ ] 8.2 依 design 的「Compatibility」以不同 image/Cloud Run revision 驗收 SPA，通過後切換根路徑流量；保留可回復的 Gradio revision/image，記錄最小 rollback 程序。
- [ ] 8.3 驗收通過後移除 `src/ui.py`、暫時 Gradio adapter、`tests/test_ui_clear_history.py`、Gradio dependency 與無用 static/rate-limit 特例，重新產生 pinned `requirements.txt`。
- [x] 8.4 更新 `README.md` 的技術棧、畫面、開發/build/test/部署指令、環境變數、API 摘要與 side-project 已知限制（單 instance、共用 60 RPM、無 RBAC/PII 治理、所有登入者可上傳、清除範圍與 Stop 語意）。
- [x] 8.5 執行 backend test、frontend lint/typecheck/unit tests、Playwright、Docker smoke test，保存結果並將本 checklist 只依可驗證證據逐項勾選。
