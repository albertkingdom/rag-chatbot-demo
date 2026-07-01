## Context

Carbon Assistant 的 Cloud Run Service(`terraform/main.tf` 的 `google_cloud_run_v2_service.web`)目前透過 `google_cloud_run_v2_service_iam_member.public` 把 `roles/run.invoker` 授予 `allUsers`,等於對全網公開。`src/app.py` 用 `gr.mount_gradio_app` 把 Gradio UI 掛在 FastAPI 根路徑,完全沒有認證或限流;BOM mapper、手冊上傳、RAG chat 都能被匿名呼叫,直接消耗 OpenAI / Pinecone / OpenRouter 配額。

本 change 在不改動 RAG/BOM/upload 業務邏輯的前提下,補上三層防線:Cloud Run IAM 限縮、app 層 API key 認證、per-key Redis 限流,並補一個豁免認證的 `/health` 端點供 Cloud Run liveness 探測。目標對象:demo 給外部審查者瀏覽器使用,以及未來的程式化 API 存取。

既有相關資產:`gcp-deployment` change(尚未 archive)已建立 `gcp-cloud-run-service` spec 與 9 個 Secret Manager secret 的注入機制;本 change 沿用同一套 secret 注入流程新增 `APP_API_KEY`,並置換該 change引入的 `allUsers` invoker。由於 `gcp-cloud-run-service` 尚未成為 canonical spec,本 change 不為其產生 delta,改以新 capability `access-control` 承載對外契約,並在此 design 記錄互動。

## Goals / Non-Goals

**Goals:**

- 關閉 Cloud Run Service 對 `allUsers` 的預設公開存取。
- 在 FastAPI 層強制 API key 認證,涵蓋 Gradio UI 與所有後端路由。
- 以 Redis 做 per-key 限流,防止單一金鑰濫用。
- 提供豁免認證的 `/health` 端點,讓 Cloud Run liveness 探測在冷啟動時快速成功。
- 提供瀏覽器可用的 `/login` 流程,讓外部審查者能用金鑰登入 Gradio UI;登入成功後發給 session token(Redis-backed),原始金鑰不再寫入 cookie。
- 提供單一 session 吊銷(`/logout`),可在不輪替金鑰的前提下讓某個瀏覽器立即失效。
- 保留 `AUTH_ENABLED` 開關供本機開發與 hermetic 測試。

**Non-Goals:**

- 不做 OAuth/OIDC、JWT、使用者帳號或角色權限(RBAC)。
- 不做多租戶金鑰管理、金鑰核發 API 或自動輪替工具;單一共享金鑰來自 Secret Manager / 環境變數,輪替靠更新 secret。
- 不做 per-route 差異化限流;所有非豁免路由統一限流。
- 不做 readiness probe / 依賴項健康檢查(Redis/MongoDB 連通性);只做 liveness。
- 不引入 Google IAP、Cloud Load Balancer、自有網域。
- 不改動既有 RAG / BOM mapper / 手冊上傳的業務邏輯。
- 不為 `gcp-cloud-run-service` 產生 delta spec(其尚未 canonical)。

## Decisions

### IAM 限縮方式

移除 `google_cloud_run_v2_service_iam_member.public`(授予 `allUsers`),改為對一個可設定成員清單 `var.allowed_invoker_members`(list of string,預設空)逐一授予 `roles/run.invoker`。預設空清單 = 完全封閉,operator 必須主動填入組織成員 / demo service account。

考量過的替代方案:
- (a) 保留 `allUsers`、只用 app 層 API key 把關:被否決,因為 Cloud Run 實例仍會被任何網路請求喚起並計費,且在 app 層檢查前已消耗資源;不符「限縮可識別主體」的需求。
- (b) Google IAP:被否決,需 Cloud Load Balancer + 自有網域 + IAP 授權,成本與設定超出本次需求。
- (c) 改用 `google_cloud_run_v2_service` 的 `ingress = INGRESS_TRAFFIC_INTERNAL_ONLY`:只能擋外部網路、無法識別特定主體,且 demo 需要外部存取,不適用。

互動說明:Cloud Run invoker IAM 只決定「請求能否到達 app」。對純瀏覽器 demo 而言,若 `allowed_invoker_members` 只列組織成員,外部審查者會在到達 app 前就被 Cloud Run 擋下(403),app 層 API key 無從檢查。此一取捨見 Risks;operator 須在「完全封閉(內部)」與「加入審查者 Google 帳號 / 暫時恢復公開 + 靠 API key」之間選擇,本 change 預設封閉。

### 認證憑證形式:API key header 與 session token cookie

以 FastAPI middleware 實作,對所有非豁免路由檢查憑證。接受兩種憑證形式:

- **程式化路徑**:`X-API-Key` header 帶原始金鑰,middleware 用 `hmac.compare_digest` 比對 `APP_API_KEY`。
- **瀏覽器路徑**:`session_id` HttpOnly cookie 帶 session token,middleware 查 Redis `session:<id>` 是否存在;存在即放行,不再碰原始金鑰。

兩條路徑在驗證通過後都會導出「底層金鑰的 SHA256 hash」做為 rate limit 桶 key(header 路徑直接 hash 呈現的金鑰;session 路徑從 session 記錄裡讀出建立時存入的 key_hash),確保同一把金鑰的所有 session 與 header 請求共用同一個限流配額。

金鑰來源:本機/docker-compose 走 `APP_API_KEY` 環境變數;GCP 走 Secret Manager(加入既有 `local.secret_names` 清單),Terraform 注入。單一金鑰;多金鑰為 Non-Goal。

考量過的替代方案:
- (a) 用 Gradio 內建 `auth=("user","pass")`:被否決,它只保護 Gradio 路由、不涵蓋未來非 Gradio API 路由,且與 FastAPI middleware 雙重認證會互相衝突(login 頁本身會被 middleware 擋)。
- (b) query string `?api_key=`:被否決,金鑰會進入 URL、server log、Referer,洩漏風險高。
- (c) cookie 直接放原始金鑰:被否決,cookie 一旦洩漏拿到的是真金鑰、可永久重用且無法單獨吊銷;改用 session token 把「證明知道金鑰」與「後續請求的憑證」解耦(見下節)。

### Session token 而非原始金鑰 cookie

登入成功時不把金鑰寫進 cookie,改發一張隨機 session token:`secrets.token_urlsafe(32)`(≥128 bits),存入 Redis `session:<id>`,值含 `{key_hash, created_at}`(key_hash 供限流分桶),TTL `SESSION_TTL_SECONDS`(預設 86400)。cookie 只放 `session_id`,HttpOnly + `SameSite=Lax`。

這樣做的理由:
- **最小暴露**:原始金鑰在登入後不再離開伺服器;cookie 被竊的損失從「拿到真金鑰可永久重用」降為「只能冒用該 session、且可隨時註銷」。
- **可吊銷**:`POST /logout` 刪 `session:<id>` 即讓該瀏覽器立即失效,不必輪替整把金鑰、不影響其他登入者。Redis TTL 也讓 session 自動過期。
- **成本近乎免費**:本 change 已用 Redis 做限流,session 查詢只是多一次 `EXISTS`/`GET`,不需新依賴。

考量過的替代方案:
- (a) JWT 自簽:被否決,需處理簽章金鑰管理、過期/刷新、撤銷清單,對 demo 規模過度;Redis-backed opaque token 更簡單且立即可吊銷。
- (b) 無狀態(raw key in cookie):見上節 (c),被否決。

### 瀏覽器登入流程

新增 `GET /login`(回 HTML form,豁免認證)與 `POST /login`(用 `hmac.compare_digest` 驗證金鑰;成功則建立 session token、寫入 Redis、設 HttpOnly `session_id` cookie 並 302 到 `/`;失敗回 401 + form,不建立 session、不設 cookie)。新增 `POST /logout`(豁免認證)刪 `session:<id>` 並清 cookie。middleware 對瀏覽器請求(`Accept: text/html` 且無有效憑證)回 302 到 `/login`;對程式化請求回 401 JSON。Gradio UI 的所有內部 asset / SSE / websocket 請求都會自動帶 `session_id` cookie,不需要逐一豁免。

### Rate limit 演算法

用 Redis fixed-window counter:以 `INCR rate:<keyhash>:<window_start>` + `EXPIRE` 一個原子 pipeline 實作,視窗 60 秒,限額 `RATE_LIMIT_RPM`(預設 60)。超限回 429 + `Retry-After`。`<keyhash>` 是底層金鑰的 SHA256 前 16 hex — header 路徑直接 hash 呈現的金鑰;session 路徑從 `session:<id>` 記錄裡讀出建立時存入的 `key_hash`。這確保同一把金鑰的所有 session 與 header 請求共用同一配額,且明文金鑰不會出現在 Redis key。`/health`、`GET /login`、`POST /login`、`POST /logout` 豁免。Redis 不可用時 fail-open(放行 + log error),因為憑證閘道已是主要存取控制,可用性優於嚴格限流。

考量過的替代方案:
- (a) token bucket:更平滑但實作複雜、需 Lua script,對 demo 規模過度。
- (b) fail-closed(Redis 掛就擋所有請求):被否決,Redis 是共享服務、偶發抖動會讓整個 app 不可用,不符合 demo 可用性要求。

### 健康檢查端點

`GET /health` 回 200 `{"status":"ok"}`,不檢查金鑰、不限流、不初始化任何重型 singleton(reranker / embeddings / vector store / BM25)。這讓 Cloud Run liveness probe 在冷啟動時能立刻成功,不會因為 BGE 模型載入慢而被誤殺。readiness / 依賴項健康檢查為 Non-Goal。

### AUTH_ENABLED 開發模式

`AUTH_ENABLED`(預設 `true`)為 `false` 或 `APP_API_KEY` 未設時,middleware 跳過認證與限流,`/health` 仍可用,啟動時 log 一行警告 `AUTH DISABLED — not for production`。這讓 `docker-compose` 本機開發與 hermetic 單元測試不必設金鑰,同時確保 prod 必須顯式設 `AUTH_ENABLED=true` + `APP_API_KEY`。

## Implementation Contract

**行為(對外可觀察):**

- `GET /health` → 200 `{"status":"ok"}`,不需憑證、不限流、不載入重型 singleton。
- 非豁免路由無有效憑證 → 401 JSON `{"detail":"Missing or invalid credential"}`;瀏覽器(`Accept: text/html`)→ 302 到 `/login`。
- `GET /login` → 200 HTML form(豁免)。
- `POST /login` 有效金鑰 → 建立 session(Redis `session:<id>`,TTL `SESSION_TTL_SECONDS`,值含 `key_hash`)+ 設 HttpOnly `session_id` cookie + 302 到 `/`;無效 → 401 + form + 錯誤訊息,不建立 session、不設 cookie。**原始金鑰不寫入 cookie。**
- `POST /logout` → 刪 `session:<id>`、清 `session_id` cookie;該 session 立即失效。
- 超過 `RATE_LIMIT_RPM` → 429 + `Retry-After` header(同一把金鑰的所有 session/header 請求共用配額)。
- `AUTH_ENABLED=false` 或 `APP_API_KEY` 未設 → 所有路由放行,啟動 log 含 `AUTH DISABLED` 警告。
- 原始金鑰比對用 `hmac.compare_digest`;金鑰不出現在 log / URL / cookie。

**介面 / 資料形狀:**

- 新模組 `src/access_control.py`:
  - `class AuthConfig`:欄位 `enabled: bool`、`api_key: str | None`、`rate_limit_rpm: int`、`session_ttl_seconds: int`、`redis` 連線。
  - `class AuthRateLimitMiddleware(BaseHTTPMiddleware)`:檢查憑證(`X-API-Key` header 或 `session_id` cookie)→ 查 Redis session(header 路徑用 `hmac.compare_digest`;session 路徑用 `EXISTS`)→ 限流 → 放行或拒絕。
  - `def build_auth_config() -> AuthConfig`:從環境變數 `AUTH_ENABLED`、`APP_API_KEY`、`RATE_LIMIT_RPM`、`SESSION_TTL_SECONDS`、`REDIS_URL` 建構。
  - `def mount_auth(app: FastAPI) -> None`:掛載 middleware、註冊 `/health`、`GET /login`、`POST /login`、`POST /logout`。
  - Session 存取輔助:`create_session(redis, key_hash) -> str`(產生 `secrets.token_urlsafe(32)`、寫 `session:<id>`)、`validate_session(redis, sid) -> str | None`(回 `key_hash` 或 `None`)、`revoke_session(redis, sid) -> None`。
- `src/app.py`:在 `gr.mount_gradio_app` 之前呼叫 `mount_auth(app)`。
- `src/config.py`:新增 `RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM", "60"))`、`AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() == "true"`、`SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))`。
- `terraform/main.tf`:移除 `google_cloud_run_v2_service_iam_member.public`;新增 `variable "allowed_invoker_members"`(list, default `[]`)+ `for_each` invoker grant;`local.secret_names` 加入 `"APP_API_KEY"`;Service 容器 env 加入 `AUTH_ENABLED=true`、`RATE_LIMIT_RPM`、`SESSION_TTL_SECONDS`。
- `docker-compose.yml`:`web` 與 `worker` 傳遞 `APP_API_KEY`、`AUTH_ENABLED`、`RATE_LIMIT_RPM`、`SESSION_TTL_SECONDS`。
- 測試:`tests/test_access_control.py`、`tests/test_health_endpoint.py`(hermetic,monkeypatch Redis / `AUTH_ENABLED`)。

**失敗模式:**

- 憑證無效/缺失 → 401(程式)或 302(瀏覽器)。
- session 過期或被吊銷 → 視為無憑證 → 401/302。
- 超限 → 429 + `Retry-After`。
- Redis 不可用 → 認證與限流皆 fail-open 放行 + log error(憑證閘道降級,但 app 仍可用)。
- `AUTH_ENABLED=false` → 放行 + 啟動警告。

**驗收條件:**

- `tests/test_access_control.py` 通過:valid header 放行、missing/invalid header 401、valid session cookie 放行、expired/revoked session 401、`POST /login` 成功建 session 並設 `session_id` cookie(不含原始金鑰)、`POST /login` 失敗不建 session、`POST /logout` 刪 session、rate limit 內/超限/per-key(同金鑰多 session 共用配額)、Redis down fail-open、`AUTH_ENABLED=false` 全放行、`build_auth_config` 各分支。
- `tests/test_health_endpoint.py` 通過:`GET /health` 無憑證回 200、`/health` 不受限流影響、`/health` handler 不觸發 reranker/embeddings 載入(以 spy 驗證)。
- `terraform validate` 通過;`terraform plan` 顯示 `allUsers` invoker 被銷毀、`allowed_invoker_members` grants 建立、`APP_API_KEY` secret 加入。
- 手動:`curl -I <url>/health` 回 200;`curl <url>/` 無憑證回 401;`curl -H "X-API-Key: <key>" <url>/` 回 200;瀏覽器首次連 `/` 被導到 `/login`,登入後可正常使用 Gradio,`POST /logout` 後立即失效。

**範圍邊界:**

- In scope:上述 `src/access_control.py`、`src/app.py` 掛載點、`src/config.py` 常數、`terraform/main.tf` IAM + secret、`docker-compose.yml` env、兩支測試、README/handoff 文件更新。
- Out of scope:RAG/BOM/upload 業務邏輯、IAP/LB/網域、OAuth/JWT/RBAC、多金鑰管理、readiness probe、`gcp-cloud-run-service` delta spec。

## Risks / Trade-offs

- [Cloud Run IAM 預設封閉會擋掉外部瀏覽器 demo] → 預設 `allowed_invoker_members=[]` 為最安全;operator 需為外部審查者加入其 Google 帳號,或暫時把該成員設為公開並依賴 app 層憑證。此取捨在 README/handoff 文件明確說明,且 app 層憑證永遠是第二道防線。
- [Redis 抖動導致認證與限流 fail-open] → 可用性優先;fail-open 時憑證閘道降級但仍可運作。文件記載此為已知取捨,未來可加本地 in-memory session/cache fallback(Non-Goal)。
- [session cookie 的 XSS 風險] → cookie 設 `HttpOnly`(JS 不可讀)+ `SameSite=Lax`;不設 `Secure` 是因為 Cloud Run 終止 TLS 後到容器的內部流量是 HTTP,設 `Secure` 會讓 cookie 不被帶上。文件記載此取捨。session token 被竊的影響已透過可吊銷性 + TTL 限制(相較於 raw key in cookie 的永久重用)。
- [計時側通道洩漏金鑰] → `hmac.compare_digest`。
- [Gradio 內部 asset/SSE 請求被擋] → `session_id` cookie 讓瀏覽器自動帶憑證給所有同源請求,不需逐一豁免 asset 路徑。
- [單一金鑰無法分辨使用者] → Non-Goal;session 提供吊銷單一瀏覽器的能力,但金鑰輪替仍靠更新 Secret Manager + 重新部署,所有 holder 同步換 key。
- [`APP_API_KEY` 未設但 `AUTH_ENABLED=true` 會讓 app 啟動後所有請求 401] → `build_auth_config` 在此情況 log 明確錯誤並把 `enabled` 設為 false(降級為 AUTH_DISABLED 行為),避免靜悄悄地全擋。
- [session store 與限流共用 Redis,Redis 全失效時 session 也失效] → 已由 fail-open 處理(放行);此時限流失效但 app 仍可用,且 Cloud Run IAM 是更外層的防線。
