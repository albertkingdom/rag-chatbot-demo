## 1. 設定與模組骨架

- [x] 1.1 在 `src/config.py` 新增 `AUTH_ENABLED`、`RATE_LIMIT_RPM`、`SESSION_TTL_SECONDS`、`APP_API_KEY` 來源常數:`AUTH_ENABLED = os.environ.get("AUTH_ENABLED","true").lower()=="true"`、`RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM","60"))`、`SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS","86400"))`、`APP_API_KEY = os.environ.get("APP_API_KEY")`。驗收:`python -c "from src.config import AUTH_ENABLED, RATE_LIMIT_RPM, SESSION_TTL_SECONDS, APP_API_KEY"` 可匯入且預設值正確(手動 content review + 匯入指令)。
- [x] 1.2 建立 `src/access_control.py`,定義 `AuthConfig` dataclass(欄位 `enabled: bool`、`api_key: str | None`、`rate_limit_rpm: int`、`session_ttl_seconds: int`、`redis`)與 `build_auth_config() -> AuthConfig`,從環境變數建構;當 `AUTH_ENABLED=true` 但 `APP_API_KEY` 未設時,`enabled` 降為 `False` 並 log 明確錯誤。驗收:`build_auth_config()` 在 `AUTH_ENABLED=true`+`APP_API_KEY=set` 回 `enabled=True`;`APP_API_KEY` 未設回 `enabled=False` 且 log 含錯誤(以 `tests/test_access_control.py::test_build_auth_config_*` 驗證)。

## 2. 認證中介層(Request authentication via API key or session token)

- [x] 2.1 在 `src/access_control.py` 實作 `AuthRateLimitMiddleware(BaseHTTPMiddleware)`:對非豁免路由檢查兩種憑證 — (a) `X-API-Key` header 用 `hmac.compare_digest` 比對 `APP_API_KEY`;(b) `session_id` cookie 查 Redis `session:<id>` 是否存在。無有效憑證 → 401 JSON `{"detail":"Missing or invalid credential"}`;瀏覽器(`Accept: text/html`)→ 302 到 `/login`。豁免清單:`/health`、`GET /login`、`POST /login`、`POST /logout`。驗收:`tests/test_access_control.py` 中 valid header 放行、valid session cookie 放行、missing/invalid header 401、expired/unknown session 401、瀏覽器 302 到 `/login` 等情境通過(實作 Request authentication via API key or session token;對應設計決策「認證憑證形式:API key header 與 session token cookie」)。
- [x] 2.2 在 `src/access_control.py` 實作 `mount_auth(app: FastAPI) -> None`:掛載 `AuthRateLimitMiddleware` 並註冊 `/health`、`GET /login`、`POST /login`、`POST /logout` 路由。在 `src/app.py` 的 `gr.mount_gradio_app(app, demo, path="/")` **之前**呼叫 `mount_auth(app)`,確保中介層對 Gradio 路由生效。驗收:`TestClient(app)` 對 `/` 無憑證回 401、`/health` 回 200(以 `tests/test_access_control.py::test_middleware_mounted_before_gradio` 驗證)。

## 3. 健康檢查端點(Health check endpoint exempt from authentication and rate limiting)

- [x] 3.1 在 `mount_auth` 內註冊 `GET /health` 回 200 `{"status":"ok"}`,handler 不觸發 `get_reranker_model`/`get_embeddings`/`get_vectorstore`/`get_bm25_index` 任何 singleton 載入。驗收:`tests/test_health_endpoint.py::test_health_no_credential_returns_200`、`test_health_not_rate_limited`(連打 > RATE_LIMIT_RPM 仍 200)、`test_health_does_not_load_heavy_singletons`(以 monkeypatch spy 驗證 reranker/embeddings 未被呼叫)(實作 Health check endpoint exempt from authentication and rate limiting)。

## 4. Session token 生命週期(Session token lifecycle and revocation)

- [x] 4.1 在 `src/access_control.py` 實作 session 存取輔助:`create_session(redis, key_hash) -> str`(以 `secrets.token_urlsafe(32)` 產生 ≥128 bits id,寫 Redis `session:<id>` → JSON `{"key_hash":..., "created_at":...}`,TTL `SESSION_TTL_SECONDS`,回傳 id)、`validate_session(redis, sid) -> str | None`(查 `session:<id>`,回 `key_hash` 或 `None`)、`revoke_session(redis, sid) -> None`(`DELETE session:<id>`)。驗收:`tests/test_access_control.py::test_create_session_is_random_and_128bits`(兩次產生不同、長度 ≥32 hex)、`test_session_stored_with_ttl`、`test_validate_session_expired_returns_none`、`test_revoke_session_deletes_record`(實作 Session token lifecycle and revocation;對應設計決策「Session token 而非原始金鑰 cookie」)。

## 5. Per-key Redis 限流(Per-API-key rate limiting via Redis)

- [x] 5.1 在 `AuthRateLimitMiddleware` 加入限流邏輯:Redis fixed-window counter,`INCR rate:<keyhash>:<window_start>` + `EXPIRE 60`,視窗 60 秒、限額 `RATE_LIMIT_RPM`;`<keyhash>` = 底層金鑰 SHA256 前 16 hex(header 路徑直接 hash 呈現金鑰;session 路徑從 `validate_session` 回傳的 `key_hash` 取得)。超限 → 429 + `Retry-After` header。豁免 `/health`、`GET /login`、`POST /login`、`POST /logout`。驗收:`tests/test_access_control.py::test_rate_limit_within_limit_succeeds`、`test_rate_limit_over_limit_returns_429_with_retry_after`、`test_rate_limit_per_underlying_key_not_per_session`(同一把金鑰的兩個 session 共用配額;不同金鑰互不影響)(實作 Per-API-key rate limiting via Redis;對應設計決策「Rate limit 演算法」)。
- [x] 5.2 實作 Redis 不可用 fail-open:當 `redis` 連線例外時放行請求並 log error,不拋 500。驗收:`tests/test_access_control.py::test_redis_unavailable_fails_open`(mock redis 拋 `ConnectionError`,請求仍放行 + log 含 error)。

## 6. 瀏覽器登入與登出流程(Browser login endpoint issuing session tokens)

- [x] 6.1 在 `mount_auth` 內註冊 `GET /login` 回 HTML form(key input + submit,method POST),與 `POST /login` 用 `hmac.compare_digest` 驗證金鑰:成功則呼叫 `create_session` 建立 session、設 HttpOnly `session_id` cookie(`SameSite=Lax`,不設 `Secure`,值為 session id **非原始金鑰**)並 302 到 `/`;失敗回 401 + form + 錯誤訊息,不建立 session、不設 cookie。form 以 POST 提交,金鑰不入 URL。驗收:`tests/test_access_control.py::test_get_login_form_reachable`、`test_post_login_valid_issues_session_cookie_and_redirects`(cookie 值是 session id、Redis 有 `session:<id>` 記錄)、`test_post_login_invalid_no_cookie_no_session`(實作 Browser login endpoint issuing session tokens;對應設計決策「瀏覽器登入流程」)。
- [x] 6.2 在 `mount_auth` 內註冊 `POST /logout`(豁免認證):呼叫 `revoke_session` 刪 `session:<id>`、清 `session_id` cookie、回 302 到 `/login`。驗收:`tests/test_access_control.py::test_logout_revokes_session`(登出後該 session cookie 立即失效,後續請求 401/302)、`test_logout_one_session_does_not_revoke_others`(同金鑰另一 session 仍有效)(實作 Session token lifecycle and revocation 的吊銷情境)。

## 7. AUTH_DISABLED 開發模式(Auth-disabled development mode)

- [x] 7.1 當 `AuthConfig.enabled=False` 時,`AuthRateLimitMiddleware` 跳過認證與限流(`/health` 仍可用);`mount_auth` 在此模式 log 一行 `AUTH DISABLED — not for production` 警告。驗收:`tests/test_access_control.py::test_auth_disabled_allows_all`、`test_auth_disabled_logs_warning`(以 `caplog` 驗證警告)(實作 Auth-disabled development mode;對應設計決策「AUTH_ENABLED 開發模式」)。

## 8. Terraform IAM 限縮與 secret 注入(Cloud Run Service IAM restriction and secret injection)

- [x] 8.1 在 `terraform/variables.tf` 新增 `variable "allowed_invoker_members" { type = list(string) default = [] }`;在 `terraform/main.tf` 移除 `google_cloud_run_v2_service_iam_member.public`(`allUsers`),改為 `resource "google_cloud_run_v2_service_iam_member" "invokers" { for_each = toset(var.allowed_invoker_members) ... member = each.value role = "roles/run.invoker" }`。驗收:`terraform -chdir=terraform validate` 通過;`terraform -chdir=terraform plan -var=project_id=... -var=region=... -var=image_tag=... -var=github_repository=...` 顯示 `allUsers` binding 銷毀、無 member 時不建立任何 invoker grant(實作 Cloud Run Service IAM restriction and secret injection 的 IAM 部分;對應設計決策「IAM 限縮方式」)。
- [x] 8.2 在 `terraform/main.tf` 的 `local.secret_names` 清單加入 `"APP_API_KEY"`;在 `google_cloud_run_v2_service.web` 容器 env 加入 `AUTH_ENABLED=true`、`RATE_LIMIT_RPM`(預設 60)、`SESSION_TTL_SECONDS`(預設 86400);確認 dynamic secret 注入涵蓋 `APP_API_KEY`。驗收:`terraform validate` 通過;`terraform plan` 顯示 `APP_API_KEY` secret 資源建立 + Service 容器 env 含 `AUTH_ENABLED`、`RATE_LIMIT_RPM`、`SESSION_TTL_SECONDS`(content review + plan diff)(實作 Cloud Run Service IAM restriction and secret injection 的 secret 注入部分)。

## 9. docker-compose 環境變數

- [x] 9.1 在 `docker-compose.yml` 的 `web` 與 `worker` 服務 `environment` 加入 `APP_API_KEY=${APP_API_KEY:-}`、`AUTH_ENABLED=${AUTH_ENABLED:-true}`、`RATE_LIMIT_RPM=${RATE_LIMIT_RPM:-60}`、`SESSION_TTL_SECONDS=${SESSION_TTL_SECONDS:-86400}`,讓本機 compose 可用 `.env` 提供。驗收:`docker-compose config` 不報錯且輸出含四個變數;手動設 `APP_API_KEY=test` 後 `docker-compose up` 起來,`curl localhost:8000/health` 回 200、`curl localhost:8000/` 無憑證回 401(手動驗證)。

## 10. 測試

- [x] 10.1 撰寫 `tests/test_access_control.py` 涵蓋:valid header 放行、valid session cookie 放行、missing/invalid header 401、expired/revoked session 401、瀏覽器 302、`create_session` 隨機性與長度、session 存 Redis 含 TTL、`validate_session` 過期回 None、`revoke_session` 刪記錄、`POST /login` 成功建 session 並設 `session_id` cookie(cookie 值非原始金鑰)、`POST /login` 失敗不建 session、`POST /logout` 刪 session 且不影響其他 session、rate limit 內/超限/per-underlying-key(同金鑰多 session 共用配額)、Redis down fail-open、`AUTH_ENABLED=false` 全放行 + 警告 log、`build_auth_config` 各分支。使用 `fastapi.testclient.TestClient` + monkeypatch Redis(以 `fakeredis` 或 `MagicMock`),全程 hermetic 不需外部服務。驗收:`pytest tests/test_access_control.py -v` 全綠。
- [x] 10.2 撰寫 `tests/test_health_endpoint.py` 涵蓋:`GET /health` 無憑證 200、`/health` 不受限流、`/health` 不觸發重型 singleton(spy `get_reranker_model`/`get_embeddings`/`get_vectorstore`/`get_bm25_index` 未被呼叫)。驗收:`pytest tests/test_health_endpoint.py -v` 全綠。

## 11. 文件更新

- [x] 11.1 在 `README.md` 的「Getting Started / Environment Setup」加入 `APP_API_KEY`、`AUTH_ENABLED`、`RATE_LIMIT_RPM`、`SESSION_TTL_SECONDS`、`allowed_invoker_members` 說明;在「Access the Tools」補充首次進入需 `/login` 輸入金鑰,登入後發給 session(24h),`/logout` 可登出。驗收:content review 確認新變數與登入/登出流程已記載。
- [x] 11.2 在 `docs/deployment-handoff.md` 補一節「存取控制與金鑰」:說明 `allUsers` 已移除、`allowed_invoker_members` 填法、`APP_API_KEY` 需先手動建到 Secret Manager、session token 機制(Redis-backed,非 raw key in cookie)、外部 demo 的兩種方式(加 Google 帳號 / 暫時公開 + 靠憑證)、金鑰輪替流程。驗收:content review 確認 operator 交接資訊完整。
