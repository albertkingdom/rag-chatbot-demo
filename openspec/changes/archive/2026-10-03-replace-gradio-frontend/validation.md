# React 前端替換驗收 — 2026-10-03

## 結論

本機自動化、iOS 模擬器、真實 RAG 單次／兩次並行及 Cloud Run 部署驗收通過，tasks.md 已完成 **37/37**。PR #3 已完成本地 review 並合併；正式流量已切至 React。使用者已確認實體手機驗收完成；Gradio 清理與新 runtime 驗證亦完成，清理修改尚未再部署。

## 最終結果

| 檢查 | 結果 |
| --- | --- |
| 後端回歸 | Gradio 清理後 244 passed；2 integration tests 未執行。切換前為 273 passed，已將仍有價值的 legacy 驗證轉至 typed service/API tests |
| 前端 | lint、41 tests、TypeScript 與 production build 通過 |
| 瀏覽器 | 360×740、360×420、874×294、1440×900 完整流程通過；使用 mock API |
| 同源 HTTP | 登入/cookie、鍵盤 viewport、聊天/來源、history refresh/clear、Stop/abort、upload/job、登出通過；使用 fake providers |
| iOS Simulator | iPhone 17 Pro / iOS 26.0 Safari：登入、聊天/來源、歷史恢復、清除、停止、英文與注音鍵盤、組字 Enter、原生選檔/上傳、橫向版面及登出通過 |
| 安全與版面 | XSS、cookie/origin、2,000 字邊界、上傳路徑/類型/大小驗證、44px touch targets、16px 輸入字體通過 |
| Docker / Compose | image build/smoke、SPA/assets/deep links、API 錯誤、上傳/job、Python-only runtime、開發 mount 與 web/worker 共用資料驗證通過 |
| Bundle | JS 390.41 kB（gzip 120.27 kB）；CSS 9.22 kB（gzip 2.64 kB） |
| 本機首次載入 | DOMContentLoaded 148ms；僅本機觀察，不代表正式網路效能 |

候選 image：`rag-react-acceptance:20261002`，SHA-256 `e1f641f2ad4fb0d297780e895174000966ab1c03a255ab5f089d2e5b42bbc009`。

### 真實 RAG

單次請求完成回答、來源及非快取 RAG 路徑驗證。修正後兩次同時發出的請求均通過：

| 問題 | 耗時 | 回答字元 | 來源 |
| --- | --- | --- | --- |
| 固定式燃料源排放 | 14.78s | 222 | 3 |
| 報告與清冊 | 10.21s | 176 | 3 |

兩路均收到 DoneEvent，`response_source=rag`、`cache_hit=false`。測試經使用者授權，Pinecone 僅讀取，停用 tracing、快取與對話寫入。這是兩請求功能驗收，不代表容量或回答品質評分。

## 驗收中修正的問題

- 手機鍵盤開啟時首次點送出只收起鍵盤：保留 composer 焦點，避免 click 前版面重排；原 iOS 模擬器重測一次點擊即可送出。
- 橫向短視窗裁切聊天及輸入區：縮減留白與輔助內容，保留操作按鈕；新增 874×294 瀏覽器回歸並在 iOS 重測。
- 真實並行 Pinecone 查詢失敗：避免共享 async session 的查詢生命周期重疊；新增並行與取消後恢復測試，真實並行重測通過。僅向量查詢受 lock 保護，回答生成仍可並行。

## Cloud Run 部署 — 2026-10-03

- Release：`release/v1.0.2`，commit `bcfb75c588bd08c62389608689147cc851b87e04`；[CI 成功](https://github.com/albertkingdom/rag-chatbot-demo/actions/runs/37092841053)。web 與 sync job 使用同一 commit image。
- 正式 revision：`carbon-assistant-web-00016-mpd`，流量 100%；image digest `sha256:1236fc8f53fc636b4fe980d52f8ee9456dd8c130bcb17e6fcf20cd4a9d704cfa`。
- 候選及正式 URL：health、SPA/assets/deep links、登入/cookie、API 401/404、異源 mutation 403、history restore/clear、登出通過。
- 經使用者授權的聊天驗收：候選 7.514s、正式 8.605s，均為 `cache` 路徑、3 筆來源及 DoneEvent；未證明雲端非快取 RAG 路徑。未上傳文件或執行同步 job。
- 初次候選聊天因 Atlas DNS NXDOMAIN 失敗；使用者 resume 叢集後重測通過。正式 revision 驗收時 ERROR 日誌為 0。
- Preview tag 已移除；舊 Gradio revision `carbon-assistant-web-00014-pw9` 與 image 保留。回復指令見部署手冊；未做實際流量回復演練。

## Gradio 清理 — 2026-10-03

- 分支 `feature/remove-gradio`；React 為唯一介面，保留 `POST /login` 相容轉址與既有 API/session/rate limit。
- 新 image `rag-react-no-gradio:20261003`，image ID `sha256:75d5504375bb97562f6fb1280e97e2d88f39165dae3f7cca0da2ea97b0e4c5db`。
- 新 image 網路隔離驗證：pip check、244 backend tests、SPA/assets/deep links、typed fake chat、upload/job、輸入邊界及離線 reranker 通過；runtime 不含 Gradio 或 Node。前端 lint、41 tests、typecheck/build 通過。
- 首次 image build 因 Docker 虛擬磁碟不足失敗；可用空間恢復至 7.9 GB 後重試通過，未擴容或重啟 Docker。
- Spectra 3.0 在原 worktree 讀取主工作目錄的變更清單；以獨立暫存副本完成檢查，未改動主工作目錄的規格。

## 完成狀態與範圍限制

- **8.1 已完成**：2026-10-03 使用者確認正式部署後的實體手機驗收完成；裝置型號與各項測試細節未提供。
- **8.2 已完成**：候選驗收、release CI、正式切換與正式 URL 驗收；保留舊 revision/image 及 rollback 指令，未實際執行流量回復。
- **8.3 已完成**：移除 Gradio UI、adapter、舊串流與登入頁；重新產生鎖檔，移除 17 個套件，其餘版本不變。清理修改尚未再部署。
- 模擬器上傳已驗證檔案確實到達隔離後端且內容一致；同步 job 為 fake，尚無真實上傳後寫入 Pinecone 的驗收證據。

PNG 僅保留本機，不納入版控。重跑指令見 README「前端驗收」及 `scripts/real_rag_smoke.py`；真實 provider 測試需明確授權。
