## Problem

啟用驗證時，未設定或留空共用金鑰會讓應用程式以停用驗證及限流的狀態啟動。

## Root Cause

`build_auth_config` 偵測到缺少金鑰後，將啟用旗標改為 false 並繼續掛載驗證中介層。

## Proposed Solution

驗證啟用時要求提供金鑰。缺少金鑰時，應用程式在初始化驗證時拋出包含設定名稱的錯誤並停止啟動。

## Non-Goals

共用金鑰的比對、登入、session、限流規則與既有環境旗標解析維持原狀。

## Success Criteria

- 驗證為 true（含預設）且金鑰未設定或為空字串時，初始化失敗並指出 `APP_API_KEY`。
- 明確停用驗證且無金鑰的開發模式仍可啟動，保留警告及公開健康檢查。
- 設有金鑰時，既有登入、API key 驗證和 session 測試通過。

- 評估腳本可在未提供 HTTP 金鑰時載入既有 provider。

## Capabilities

### Modified Capabilities

- `access-control` — 啟用驗證但缺金鑰時終止啟動。

## Impact

- 修改 `src/access_control.py`、`tests/test_access_control.py`、`src/config.py`、`README.md`、`.env.example`、`architecture.md`。
- 同步 `openspec/specs/access-control/spec.md`。
- 將 `tests/evaluate_rag.py` 與 `tests/generate_rag_eval_data.py` 的 provider 匯入指向其定義模組。
