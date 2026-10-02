# Gradio / SPA parity checklist

本案只提供共用 key、單一 session 當前對話與所有登入者可上傳。無多帳號、SSO/RBAC、多對話或 distributed lock。

| 功能 | Legacy 與 SPA 對照 | 驗收證據 |
|---|---|---|
| 登入／登出 | 共用 APP_API_KEY、opaque HttpOnly session、登出 revocation；保留 POST /login redirect | access_control / API contract、browser journey |
| 範例問題 | React 保留原有六題；桌面／手機可水平捲動範例列 | App 原始碼、browser viewport checks |
| RAG/direct/cache/guardrail | Gradio adapter 與 SPA 消費同一 typed chat service；完整回答保存規則一致 | test_chat_service.py、test_gradio_adapter.py |
| 來源／timing | Legacy adapter append 來源與回應時間；SPA 分開呈現 | adapter parity、App tests、browser journey |
| History／clear | Cookie server session；restore、確認、204 後清畫面；503 保留 | App/API/security tests、socket journey |
| 手冊上傳 | PDF/XLSX/CSV；SPA 基本安全驗證、拖放、progress、202 | manual/API/component tests |
| Local RQ | queued/running/succeeded/failed；enum normalization | test_manual_jobs.py |
| GCP Job | execution name encode/decode、running/success/failure/not-found | test_manual_jobs.py；不提交真實 cloud job |
| Stop | 停止 client 接收；未完成 answer 不持久化；已完整生成可保存 | service cancellation、transport close、parser abort、App stop |
| 回復 | 舊 production revision/image 不變；新 image 可 FRONTEND_MODE=gradio 作暫時 rollback | Docker/Compose smoke、deployment runbook |

正式切換與實機驗收結果另記於 validation.md；GCP adapter unit test 不等於已執行正式 job。
