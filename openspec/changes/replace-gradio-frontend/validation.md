# React 前端替換驗收 — 2026-10-02

## 結論

本機自動化、iOS 模擬器及真實 RAG 單次／兩次並行驗收通過，tasks.md 已完成 **34/37**。實體手機、正式部署與 Gradio 移除仍待完成；PR 尚未完成 review，未切換正式流量。

## 最終結果

| 檢查 | 結果 |
| --- | --- |
| 後端回歸 | 273 passed；2 integration tests 未執行，另以真實 RAG smoke 驗證單次／並行路徑 |
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

## 剩餘事項與範圍限制

- **8.1**：完整實體手機驗收；模擬器不能代替實體手機。
- **8.2**：PR review、候選 Cloud Run revision 驗收、正式切換與 rollback 驗證。
- **8.3**：切換驗收後移除 Gradio、暫時 adapter、相關測試與依賴。
- 模擬器上傳已驗證檔案確實到達隔離後端且內容一致；同步 job 為 fake，尚無真實上傳後寫入 Pinecone 的驗收證據。

PNG 僅保留本機，不納入版控。重跑指令見 README「前端驗收」及 `scripts/real_rag_smoke.py`；真實 provider 測試需明確授權。
