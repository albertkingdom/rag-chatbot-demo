# 部署交接文件 — Carbon Assistant

> 給接手的 AI / 工程師。說明如何透過 GitHub 發版、檢查發版狀態,以及 gcloud 操作細節。
> 內容對照 `.github/workflows/deploy.yml` 與 `terraform/` 實際設定(最後更新 2026-06-30)。

---

## 0. 關鍵識別資料(一覽)

| 項目 | 值 |
| --- | --- |
| GitHub repo | `albertkingdom/cedars-digital-assignment` |
| GCP project | `carbon-rag-assistant-prod-2026` |
| Region | `asia-east1` |
| Artifact Registry repo | `carbon-assistant` |
| Docker image 名 | `carbon-assistant` |
| Cloud Run **Service**(web) | `carbon-assistant-web` |
| Cloud Run **Job**(知識庫 sync) | `sync-job` |
| Service URL | `https://carbon-assistant-web-mbzqd4zlha-de.a.run.app` |
| CI workflow | `.github/workflows/deploy.yml`(名稱 "Deploy to Cloud Run") |
| **部署觸發分支** | `release/**`(目前用 `release/gcp-deployment`) |
| tfstate bucket(GCS) | `carbon-rag-assistant-prod-2026-tfstate` |
| 共享資料 bucket(GCS FUSE,上傳檔+BM25 index) | terraform `google_storage_bucket.shared` |

**本機工具**(此環境):`terraform` 在 `/opt/homebrew/bin/terraform`(v1.15.7);gcloud SDK 在 `~/google-cloud-sdk/bin/gcloud`(下文命令省略全路徑,若 PATH 沒設請自行補上)。

---

## 1. 部署架構(先理解這個再操作)

```
本機改 code
  └─ feature/<name>  ──merge──▶  master  ──merge──▶  release/gcp-deployment ──push──▶ 觸發 CI
                                  (push master 不部署)                                  │
                                                                                        ▼
                              GitHub Actions (deploy.yml):
                              1. WIF 認證(無長期金鑰,OIDC)
                              2. docker build(CI 在 ubuntu/amd64,native,不需 --platform)
                                 → tag = git SHA → push 到 Artifact Registry
                              3. terraform init(state 在 GCS backend)
                              4. terraform apply -target=Service -target=Job
                                 帶 image_tag=<git SHA>
                                        │
                                        ▼
                              Cloud Run 產生新 revision(滾動切流量)
```

**重點原則**

- **只有 push 到 `release/**` 會部署**;push `master` 不會(見 deploy.yml `on.push.branches`)。
- **CI 只 apply「app 層」**(Cloud Run Service + Job),用 `-target`。基礎設施(service account、IAM、Secret Manager、WIF、buckets、Artifact Registry repo)**由人工在本機用 owner 權限 `terraform apply`**,CI 的 deployer SA 是最小權限,不能改 IAM/WIF。所以**不要期待 CI 會建立新的 secret/權限/bucket**——那些要先本機 apply。
- image 一律用 **git SHA** 當 tag(CI 帶 `image_tag=$GITHUB_SHA`),可追溯到 commit。

---

## 2. 如何發版(標準流程)

假設你已在 `feature/<name>` 完成並 commit 改動。

```bash
# 1) 併進 master(整合分支;push master 不部署,且本 repo 政策禁止直接 push master,見 §5)
git checkout master
git merge --no-ff feature/<name> -m "Merge <name> into master"

# 2) 併進 release 分支並 push —— 這一步才會觸發部署
git checkout release/gcp-deployment
git merge --no-ff master -m "Merge <name> into release/gcp-deployment"
git push origin release/gcp-deployment      # ← CI 從這裡啟動
```

push 完成的瞬間 CI 就開始跑(約 12–13 分鐘)。接著到 §3 檢查狀態。

**若改動含 Secret Manager / IAM / 新 bucket 等基礎設施**:CI 不會建。先在本機:
```bash
cd terraform
terraform apply        # 完整 apply,需 owner 等級 ADC(gcloud auth application-default login)
```
再走上面的 release 部署。

---

## 3. 如何檢查發版狀態(GitHub 端)

用 `gh` CLI(已登入 `albertkingdom`)。

```bash
# 列出 release 分支最近的 CI run(看 status / conclusion / run-id)
gh run list --branch release/gcp-deployment --limit 5

# 即時監看某個 run 跑到結束(exit code 反映成功/失敗)
gh run watch <run-id> --exit-status

# 看某個 run 的逐步驟結果
gh run view <run-id>

# 失敗時看失敗步驟的 log
gh run view <run-id> --log-failed
```

**判讀**:`deploy.yml` 只有一個 job `deploy`,步驟依序是 Checkout → WIF 認證 → setup gcloud → configure docker → **Build and push image** → setup terraform → **Terraform init & apply**。最常見失敗點在最後兩步(image build 或 terraform apply 權限/狀態問題,見 §6 踩雷)。

---

## 4. 如何檢查發版狀態(GCP / gcloud 端)

CI 綠燈後,確認 Cloud Run 真的換上新 revision。

```bash
PROJ=carbon-rag-assistant-prod-2026
REGION=asia-east1
SVC=carbon-assistant-web

# 4.1 目前流量指向的 revision + image tag(tag 應等於你部署的 git SHA)
gcloud run services describe $SVC --project $PROJ --region $REGION \
  --format="value(status.latestReadyRevisionName, spec.template.spec.containers[0].image)"

# 4.2 確認 health(冷啟動:minScale=0,第一個請求會慢)
curl -s -o /dev/null -w "HTTP %{http_code}  %{time_total}s\n" \
  https://carbon-assistant-web-mbzqd4zlha-de.a.run.app/

# 4.3 看某 revision 的 CPU / 記憶體 / 環境變數(驗證設定有生效)
gcloud run services describe $SVC --project $PROJ --region $REGION --format=json \
  | python3 -c "import sys,json;c=json.load(sys.stdin)['spec']['template']['spec']['containers'][0];print('cpu',c['resources']['limits']);[print(e['name'],e.get('value','<secret>')) for e in c.get('env',[])]"

# 4.4 看 runtime log(冷啟動、錯誤、模型載入等)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="carbon-assistant-web"' \
  --project $PROJ --limit 30 --format="value(timestamp, textPayload)"
```

### 知識庫 sync(重建向量 + BM25 index)

web app 在 `JOB_RUNNER=gcp` 時,使用者從 UI 上傳知識庫檔會觸發 Cloud Run **Job** `sync-job`(`src/app.py` 用 `projects/<proj>/locations/<region>/jobs/sync-job` 執行)。也可手動觸發:

```bash
# 手動跑一次 sync-job(例如改了 BM25 分詞後要重建 index)
gcloud run jobs execute sync-job --project $PROJ --region $REGION

# 看 job 執行歷史與結果
gcloud run jobs executions list --job sync-job --project $PROJ --region $REGION --limit 5

# 看某次 execution 的 log(成功應 exit(0),log 顯示 vectors 數 + BM25 rebuilt)
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="sync-job"' \
  --project $PROJ --limit 30 --format="value(timestamp, textPayload)"
```

---

## 5. GitHub Secrets(CI 依賴)

`deploy.yml` 需要這三個 repo secret(已設好,改動時才需動):

| Secret | 用途 |
| --- | --- |
| `GCP_PROJECT_ID` | = `carbon-rag-assistant-prod-2026` |
| `WIF_PROVIDER` | Workload Identity Provider 資源名(terraform output `workload_identity_provider`) |
| `WIF_SERVICE_ACCOUNT` | deployer SA(terraform output `github_actions_service_account`) |

取得 terraform outputs:`cd terraform && terraform output`。

**Secret Manager 的 9 個應用密鑰**(`OPENAI_API_KEY`、`PINECONE_API_KEY`、`GOOGLE_API_KEY`、`OPENROUTER_API_KEY`、`LANGCHAIN_API_KEY`、`LANGCHAIN_ENDPOINT`、`LANGCHAIN_PROJECT`、`MONGODB_URL`、`REDIS_URL`)是給 **runtime** 用,經 Secret Manager 注入,**不是** GitHub secret。Cloud Run 引用 `version="latest"`,更新密鑰值後下次冷啟動/新 execution 自動生效。

---

## 6. 踩雷備忘(務必讀,前人血淚)

1. **直接 push `master` 會被擋**:本 session 的 auto-mode 政策禁止直接 push 預設分支(繞過 PR review)。發版走 `release/**`;要同步 master 到遠端請開 **PR**。(目前 `origin/master` 可能落後本機 master。)

2. **絕不要本機對 app 層 `terraform apply`**:`terraform.tfvars` 的 `image_tag="latest"`,但線上 image 是 git SHA。本機 `terraform plan`/`apply` 會把 image **倒回 `latest`**(等於回退版本)。app 層一律交給 CI(它帶 `image_tag=$GITHUB_SHA`)。本機只跑 `terraform plan` 做語法/diff 檢查 OK,**別 apply**。基礎設施層(非 image)才本機 apply。

3. **Secret 的 CRLF `\r`**:`.env` 若是 CRLF 行尾,灌進 Secret Manager 的值會帶 `\r`,httpx-based client(OpenAI/Pinecone)把 key 放進 HTTP header 時 `\r` 非法 → `Invalid header value` → sync/chat 直接掛。灌 secret 前先 `perl -pi -e 's/\r$//' .env` 轉 LF,並用 `printf %s`(不要用 `echo`,會補 `\n`)。

4. **reranker 首查很慢是假象**:Cloud Run `minScale=0`,新 revision 第一次 chat 會把 BGE reranker 模型載入+warmup 算進 `_rerank` span(曾測到 9.2s)。**看效能要看 followup(暖機後)trace**,穩態約 3.4s。觀測用 LangSmith(`smith.langchain.com`,`LANGCHAIN_TRACING_V2=true` 已設)。

5. **改了 BM25 分詞器必須重建 index**:`tokenized_corpus` 持久化在 GCS。只改 `_tokenize` 不重跑 `sync-job`,query 與 corpus 的 token 不相容 → BM25 仍失效。改完務必 §4 手動 execute `sync-job`。

6. **CPU 與執行緒要對齊**:Cloud Run 加 `cpu` 時,必須同步設 `OMP_NUM_THREADS` = vCPU 數(在 `terraform/main.tf` 的 Service container env),否則 torch 預設 1 thread,加核無效。(已固化成 `rerank-stage` spec 需求。)

7. **Atlas / 外部服務 Network Access**:MongoDB Atlas 必須允許 Cloud Run 動態出口 IP(`0.0.0.0/0`),否則連不上 DB。Redis 用 Upstash(`rediss://`)。

---

## 7. 存取控制與金鑰(access-control change)

本 change 移除了 Cloud Run Service 的公開存取並加入 app 層認證。交接重點:

### 7.1 Cloud Run IAM:不再對 `allUsers` 公開

- `terraform/main.tf` 已移除 `google_cloud_run_v2_service_iam_member.public`,改為 `google_cloud_run_v2_service_iam_member.invokers`,以 `for_each = toset(var.allowed_invoker_members)` 逐一授權。
- **預設 `allowed_invoker_members = []` = 完全封閉**。部署後 Service 對外回 403,任何網路請求都到不了 app。
- 開放存取的兩種方式:
  - (a) **加特定成員**:在 `terraform.tfvars` 設 `allowed_invoker_members = ["user:demo@example.com", "group:team@example.com"]`,對方用 Google 帳號通過 Cloud Run IAM,再進 app 層 API key 登入。最安全。
  - (b) **暫時公開 + 靠 API key**:`allowed_invoker_members = ["allUsers"]`,讓請求到得了 app,再由 app 層 `APP_API_KEY` 把關。適合外部審查者沒有 Google 帳號的 demo;風險是實例會被任何網路請求喚起(但仍需金鑰才能操作)。

### 7.2 `APP_API_KEY` Secret Manager

- `APP_API_KEY` 已加入 `local.secret_names`,Terraform 會建立 Secret Manager 資源殼並注入 Service 容器。
- **值需先手動建**(Terraform 只建資源、不寫值):
  ```bash
  echo -n "a_long_random_32+char_string" | gcloud secrets create APP_API_KEY --data-file=-
  # 之後輪替:
  echo -n "new_random_string" | gcloud secrets versions add APP_API_KEY --data-file=-
  ```
- 建議值:≥32 字元隨機 hex/字母數字,不可與其他金鑰共用。
- Service 容器 env 已含 `AUTH_ENABLED=true`、`RATE_LIMIT_RPM=60`、`SESSION_TTL_SECONDS=86400`(可在 `terraform.tfvars` 覆寫 `rate_limit_rpm` / `session_ttl_seconds`)。

### 7.3 Session token 機制(Redis-backed)

- 瀏覽器 `/login` 輸入金鑰 → app 用 `hmac.compare_digest` 驗證 → 產生 `secrets.token_urlsafe(32)`(≥128 bits)session id,存 Redis `session:<id>`(TTL `SESSION_TTL_SECONDS`,值含金鑰的 SHA256 hash 供限流分桶)→ 設 HttpOnly `session_id` cookie。
- **原始金鑰從不寫入 cookie**;cookie 被竊只能冒用該 session,且 `POST /logout` 可立即吊銷單一 session,不必輪替整把金鑰。
- 程式化呼叫直接帶 `X-API-Key` header,不走 session。
- Redis 不可用時認證與限流 **fail-open**(放行 + log error),因為 Cloud Run IAM + API key 已是主要防線,可用性優先。

### 7.4 金鑰輪替流程

1. `gcloud secrets versions add APP_API_KEY --data-file=-`(寫新值)。
2. 重新部署 Service(release branch push → CI)讓新 revision 讀到 `latest` version。
3. 通知所有 holder 新金鑰;舊 session 在 TTL 內仍有效,如需立即失效請清 Redis `session:*` keys。
- 單一共享金鑰,無 per-user 帳號;輪替影響所有 holder 同步換 key(Non-goal:多租戶金鑰管理)。

### 7.5 驗證

- `curl -I <service-url>/health` → 200(豁免認證)。
- `curl <service-url>/` 無 header → 401。
- `curl -H "X-API-Key: <key>" <service-url>/` → 200。
- 瀏覽器首次連 `/` → 302 到 `/login`,登入後可用 Gradio,`/logout` 後立即失效。

---

## 8. 本地認證(若要本機跑 gcloud / terraform)

```bash
gcloud auth login                          # 互動式;在此 session 用 `! gcloud auth login`
gcloud auth application-default login       # 給 terraform 用的 ADC
gcloud config set project carbon-rag-assistant-prod-2026
```

CI 不需要這些(它用 WIF / OIDC,無長期金鑰)。

---

## 9. Prompt Cache 一次性 Flush(`add-answer-sources` 部署前置作業)

`add-answer-sources` change 為 prompt cache 的快取值 schema 新增必要欄位 `sources`(見 `openspec/changes/add-answer-sources/design.md`)。舊 schema 的快取值沒有這個欄位,且程式碼**不做向後相容 fallback**——`get_cached_response` 讀到缺 `sources` 的舊快取值時行為未定義(視為快取讀取失敗)。

**部署此 change 時,必須在新版程式碼開始服務流量之前,於同一維護窗口內清空 Redis 的 `prompt_cache:*` namespace**:

```bash
# 透過 Upstash Redis CLI 或任何連得上 REDIS_URL 的 redis-cli
redis-cli -u "$REDIS_URL" --scan --pattern 'prompt_cache:*' > /tmp/prompt_cache_keys.txt
xargs -a /tmp/prompt_cache_keys.txt redis-cli -u "$REDIS_URL" del
```

**驗證**:`redis-cli -u "$REDIS_URL" --scan --pattern 'prompt_cache:*'` 應回傳空結果,確認舊快取已全數清空。

**預期影響**:flush 後短期內 cache miss 率會回升到接近 100%,reranker 負載短暫回升(見 §6.4 的暖機/穩態說明,穩態約 3.4s/次);這是預期代價,換取新舊 schema 不必相容。不需額外程式碼防護,只需確保這一步排進部署 runbook,且順序早於新 revision 開始收流量。

---

## 10. 一句話總結

> 改完 code → 併到 `release/gcp-deployment` 並 push → `gh run watch` 看 CI(~13 min)→ 綠燈後 `gcloud run services describe` 確認新 revision 的 image tag = 你的 commit SHA → 若動到 BM25/知識庫,手動 `gcloud run jobs execute sync-job` 重建 → LangSmith 看 followup trace 驗效能。
