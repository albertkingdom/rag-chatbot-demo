## Context

`create_app` 呼叫 `mount_auth` 初始化驗證。現行設定缺金鑰時仍完成初始化。

## Decisions

在 `build_auth_config` 判斷缺金鑰時拋出 RuntimeError。既有的同步呼叫鏈會讓錯誤傳到應用程式入口。若延後到請求才拒絕，服務仍會完成啟動，且無法及早回報部署設定錯誤。

## Implementation Contract

- 讀取驗證旗標和金鑰後立即檢查，先於限流及 session 數值解析、掛載中介層和取得 Redis client，啟用驗證且金鑰未設定或空字串時拋出 RuntimeError，訊息包含 `AUTH_ENABLED` 和 `APP_API_KEY`。
- 明確設為 false 時保留開發模式與既有警告。真實 `mount_auth` 測試驗證可接受無憑證請求且健康檢查仍回 200。
- 保留環境旗標解析、非空金鑰原值、API key 比對、登入、session 和限流行為。

- 評估腳本從 `src.services` 與 `src.rag_pipeline` 直接取得 provider，避免載入 HTTP app。保留 app 對 provider 的既有匯出。

- 移除 `src/config.py` 中無呼叫者且在匯入時重複解析的驗證設定。由 `build_auth_config` 負責解析，讓 ASGI 入口在同時缺金鑰和數值設定無效時仍能指出缺金鑰。

## Risks / Trade-offs

使用預設啟用驗證但從未提供金鑰的環境，升級後需補上金鑰。本機開發可明確設為 false。同步 README、環境變數範例及架構文件的參數來源。
