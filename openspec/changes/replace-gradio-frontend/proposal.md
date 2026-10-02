## Why

目前介面由 `src/ui.py` 以 Gradio 建立，聊天、登入、清除紀錄、手冊上傳與背景同步狀態都直接綁在 Python UI handler 上。這使視覺與交互設計受限、前後端邊界不明確，也不利於獨立測試、無障礙、響應式佈局與後續產品化。

本變更將以 React + TypeScript + Vite 的單頁式應用取代 Gradio，並將現有 Python handler 整理成可版本化、可測試的 FastAPI API。產品環境維持單一同源服務：FastAPI 提供 `/api/v1/*` 與靜態前端成品，不新增一個需要獨立維運的 Node.js 服務。

## What Changes

- 新增 `frontend/` React + TypeScript + Vite 應用，包含登入頁、聊天頁、參考資料呈現、範例問題、對話清除、登出、管理員手冊上傳與同步狀態。
- 新增版本化 FastAPI API，涵蓋 session 驗證、聊天事件串流、對話紀錄讀取/清除、手冊上傳與背景工作狀態查詢。
- 將 `chat_stream` 內的業務流程與 Gradio request/UI 形狀解耦，產生 `status`/`delta`/`sources`/`metadata`/`done`/`error` 等結構化事件，由 API 以 NDJSON 傳輸。
- 將前端成品整合到 Docker 多階段 build，產品環境由 FastAPI 同源提供；本機開發由 Vite dev server proxy `/api` 至 FastAPI。
- 在新介面通過功能同等、整合測試與可回復驗收後，移除 `src/ui.py`、Gradio mount 與 Gradio dependency。
- 本機 Compose web/worker 共用手冊與 BM25 index volumes，source mount 不遮蔽編譯前端，確保上傳後背景同步可讀取同一資料。
- 更新 `README.md`、環境變數範例、本機開發與部署說明。

## Non-Goals

- 不在本次建立多帳號、注冊、OAuth/SSO、RBAC 或「上傳者」獨立角色；沿用現有共用 `APP_API_KEY` 與 Redis session。
- 不改寫 RAG 檢索、rerank、guardrail、cache 或 LLM provider 的商業邏輯。
- 不在本次實作多對話清單、分享對話、訊息編輯/重生成、檔案管理或完整 CMS。
- 不引入 Next.js SSR、獨立 Node.js production server 或前端專用雲端主機。
- 不變更 MongoDB 文件 schema、Redis chat-history schema 與 Pinecone 索引 schema。
- 本專案定位為 side project；本次不導入企業級安全治理，包括 SSO/RBAC、完整 PII/DLP、自動資料遮罩、分散式鎖、IP reputation/WAF、惡意檔案掃描、完整稽核與合規保存政策。保留登入驗證、既有 rate limit、後端輸入長度驗證、Markdown XSS 防護、同源 cookie 防護與基本上傳路徑/類型/大小驗證。
- 不承諾高併發或水平擴展；維持單一 Cloud Run instance 的 side-project 容量，正式擴大使用前再另案壓測與設計背壓。

## Capabilities

### New Capabilities

- `web-frontend`: 定義正式 Web 前端、FastAPI 邊界、結構化聊天串流、登入 session、對話恢復/清除、手冊上傳與部署切換的行為契約。

### Modified Capabilities

- `app-structure`: FastAPI 不再 mount Gradio，改為 mount `/api/v1` router 與前端靜態成品。
- `access-control`: 原有 HTML form login 改由 JSON API + SPA 登入頁接手，仍使用 HttpOnly session cookie。
- `stream-response`: 聊天串流改為有明確 event schema 的 NDJSON，前端不必猜測狀態文字或累積字串。
- `chat-history-persistence`: 清除 history 失敗時 API 必須可辨識失敗，不得回傳成功後仍沿用舊脈絡。

## Impact

- Affected specs: `web-frontend` (new), `app-structure`, `access-control`, `stream-response`, `chat-history-persistence` (modified)
- New code: `frontend/`, `src/api/`, 聊天應用服務與對應測試
- Modified code: `src/app.py`, `src/rag_pipeline.py`, `src/access_control.py`, `Dockerfile`, `docker-compose.yml`, `requirements.in`, `requirements.txt`, `README.md`
- Removed after cutover: `src/ui.py`, `tests/test_ui_clear_history.py`, Gradio dependency and mount
- Operational impact: Docker build 新增 Node build stage，但 runtime image 不需 Node.js；對外仍使用同一 host/port。
