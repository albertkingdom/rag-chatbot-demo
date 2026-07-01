## Why

Cloud Run Service 目前對 `allUsers` 公開(`terraform/main.tf` 的 `google_cloud_run_v2_service_iam_member.public`),`src/app.py` 也沒有任何認證或限流。任何能連網的人都能直接呼叫 RAG 鏈、BOM mapper 與手冊上傳,會產生未受控的 OpenAI / Pinecone / OpenRouter 費用,並讓使用者上傳的 BOM 與手冊暴露在無身分識別的存取下。這是業界標準安全防線的第一條,必須在對外開放前補上。

## What Changes

- **BREAKING**:Cloud Run Service 的 IAM 從 `allUsers` (`roles/run.invoker`) 改為只允許可識別主體(組織成員 / 指定 service account / 限縮網域),不再公開匿名存取。
- 在 FastAPI 層加入認證中介層:程式化請求帶 `X-API-Key` header;瀏覽器透過 `/login` 登入後發給 Redis-backed session token(HttpOnly `session_id` cookie),原始金鑰不寫入 cookie。無有效憑證回 `401`(程式)或 302 到 `/login`(瀏覽器)。
- 加入基於 Redis 的 per-key rate limiting,超過限額回 `429` 並帶 `Retry-After`;同一把金鑰的所有 session 與 header 請求共用配額。
- 新增 `/logout` 可吊銷單一 session,不需輪替金鑰。
- 新增 `/health` 唯讀端點,**豁免**認證與限流,供 Cloud Run 健康檢查與冷啟動探測使用。
- API key 來源:本機/docker-compose 走環境變數 `APP_API_KEY`;GCP 走 Secret Manager(與既有 9 個 secret 同機制),Terraform 注入。
- 加入可關閉開關 `AUTH_ENABLED`(預設 `true`),供本機開發與測試使用;關閉時所有路由免驗證且不限流,並在啟動 log 標示警告。
- README 與 `docs/deployment-handoff.md` 補充 API key 與 IAM 變更的交接說明。

## Non-Goals

(設計細節與否決方案留待 design.md)

## Capabilities

### New Capabilities

- `access-control`: 以 API key 為基礎的請求認證,加上 per-key Redis 限流,以及一個豁免認證的健康檢查端點;涵蓋 FastAPI 中介層行為與 Terraform Cloud Run IAM 限縮的對外契約。

### Modified Capabilities

(無。既有 canonical specs 均為 RAG 檢索能力,不涉及對外存取控制。Cloud Run Service 的 IAM 變更與一個進行中、尚未成為 canonical 的部署 change 有互動,會在 design.md 標明;本 change 不為其產生 delta spec。)

## Impact

- Affected specs:
  - New: `access-control`
- Affected code:
  - New: `src/access_control.py`, `tests/test_access_control.py`, `tests/test_health_endpoint.py`
  - Modified: `src/app.py` (掛載中介層與 `/health`、`/login`、`/logout` 路由), `src/config.py` (新增 auth/rate-limit/session 設定常數), `terraform/main.tf` (移除 `allUsers` invoker、改為限縮成員;`APP_API_KEY` 加入 `local.secret_names` 並注入 Service), `docker-compose.yml` (傳遞 `APP_API_KEY`、`AUTH_ENABLED`、`RATE_LIMIT_RPM`、`SESSION_TTL_SECONDS`), `Dockerfile` (無需改 entrypoint,僅依賴環境變數), `requirements.txt` (無新依賴,沿用既有 `redis`、`fastapi`), `README.md`, `docs/deployment-handoff.md`
