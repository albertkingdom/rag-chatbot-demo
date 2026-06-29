## Context

專案為 FastAPI + Gradio chatbot，目前以 `docker-compose` 在本機運行，包含六個服務：web、worker、redis、mongodb、redis-insight、mongo-express。部署目標為 GCP，使用 Cloud Run（無伺服器容器）取代自管 VM，降低維運成本並取得公開 HTTPS URL。

所有 GCP 資源（Cloud Run Service、Cloud Run Job、Artifact Registry、IAM、Secret Manager）透過 Terraform 管理，確保環境可版本控制與重現。外部依賴（OpenAI、Pinecone、OpenRouter、LangSmith）維持不變。本地 BGE Reranker 模型（570MB）需預先嵌入 Docker image 以避免冷啟動延遲。

## Goals / Non-Goals

**Goals:**

- Web App 在 Cloud Run Service 上公開運行，具備 HTTPS URL
- `sync_vector_store` 改為 Cloud Run Job，用完即止，不需常駐 worker process
- 所有 GCP 資源由 Terraform 管理，`terraform apply` 可重現完整環境
- 所有 API 金鑰透過 GCP Secret Manager 注入，不使用 `.env` 檔案
- push 到 `release/**` branch 自動 build image 並執行 `terraform apply` 完成部署；master push 不觸發
- BGE Reranker 模型預先下載進 Docker image，消除冷啟動卡頓

**Non-Goals:**

- 不使用 GCP Memorystore 或 ElastiCache
- 不替換 BGE Reranker 為 Vertex AI Ranking API
- 不設定 staging / preview 環境
- 不修改 `sync_vector_store` 業務邏輯
- 不以 Terraform 管理 Upstash Redis 與 MongoDB Atlas（第三方服務）

## Decisions

### 使用 Terraform 管理 GCP 基礎設施

所有 GCP 資源以 Terraform HCL 描述，存放於專案根目錄 `terraform/` 資料夾，納入 git 版本控制。

管理範圍：
- `google_artifact_registry_repository`：Docker image repository
- `google_cloud_run_v2_service`：Web App 服務（含 Secret Manager 掛載）
- `google_cloud_run_v2_job`：sync-job（含 Secret Manager 掛載）
- `google_secret_manager_secret`：8 個 secret 的資源定義（不含值，值另行手動設定）
- `google_iam_workload_identity_pool` / `provider`：GitHub Actions WIF 認證
- `google_service_account` 及相關 IAM bindings

Terraform state 存於 GCP Cloud Storage bucket（remote backend），避免本機 state 衝突。

替代方案：純 gcloud CLI — 無法追蹤變更歷史，環境難以重現；若手動在 Console 修改設定，CI 部署後可能遭覆蓋。

### 以 Upstash Redis 取代 Redis Container（Redis 不可移除）

Cloud Run 無法執行 docker-compose 多容器，需要外部 Redis。

選擇 **Upstash Redis**（免費層，10,000 指令/天）而非 GCP Memorystore（$16/月）。Upstash 屬第三方服務，不由 Terraform 管理，手動建立後將 `REDIS_URL` 存入 Secret Manager。

**Redis 在 GCP 環境仍為必要依賴**，不只是 RQ 用途：`src/app.py` 的語意快取 `PromptCacheService(conn)`（chat 查詢路徑）在 `JOB_RUNNER=gcp` 下仍會使用 Redis。因此 `conn = Redis.from_url(...)` 在 **local 與 gcp 兩種模式都要初始化**，只有 RQ `Queue`（`q`）是本機限定。`REDIS_URL` 必須出現在 Cloud Run Service 注入的 secret 清單中。

快取讀寫會分攤 Upstash 免費層的每日配額，需與 `poll_status` 的 polling 一併納入配額評估（非僅 polling）。

替代方案：GCP Memorystore — 走 VPC 內網延遲更低，但費用過高，chatbot 場景不需要。

### MongoDB Atlas M0 的 IP allowlist：開 `0.0.0.0/0` + 帳密/TLS 把關（方案 a）

Cloud Run 預設出口 IP 為動態、無法事先列舉，無法加入 Atlas 白名單。Atlas **M0（免費層）不支援 VPC peering / PrivateLink（M10+ 才有）**，故私網繞過白名單在免費層不可行。`src/conversation_db.py` 以 `MongoClient(MONGODB_URL)` 連線，認證靠連線字串內的帳密 + TLS（Atlas 強制 TLS、`mongodb+srv` 預設啟用）。

採 **方案 (a)：Atlas 白名單開 `0.0.0.0/0`**，安全由帳密 + TLS 承擔，並加三項加固使其站得住：

1. **強隨機密碼，只存 Secret Manager**：`MONGODB_URL` 為 9 個 secret 之一，不落地明文。
2. **最小權限 DB user**：僅授予 `chatbot` database 的 `readWrite`，不給 `atlasAdmin` 或 cluster 級權限。
3. **強制 TLS + SRV**：連線字串使用 `mongodb+srv://`，不帶任何關閉 TLS 的參數。

Atlas 屬第三方服務、不由 Terraform 管理（見 Non-Goals），故上述為 Atlas Console 的手動步驟（task 2.2），連線字串再手動填入 Secret Manager（task 4.1）。

升級路徑：若日後需更嚴格的網路隔離，升級至 M10+ 走 PrivateLink/VPC peering，或在 GCP 端加 Serverless VPC Access connector（或 Direct VPC egress）+ Cloud Router + Cloud NAT + 保留靜態 external IP，取得固定出口 IP 後將 Atlas 白名單收斂為單一 IP。

替代方案 (b)：現在就建 Cloud NAT 靜態出口 IP — 可將白名單收斂為單一 IP，但 VPC connector/NAT 有每月固定費用 + 流量費、Terraform 須多寫一整組網路資源，對 demo/低流量為過度投資，故不採用。

### 以 Cloud Run Job 取代 RQ Worker（透過 JOB_RUNNER 環境變數切換）

`sync_vector_store` 是一次性批次任務（timeout 1 小時），不需要常駐 worker 輪詢 Redis。

為同時支援本機 docker-compose 與 GCP 兩個環境，以環境變數 `JOB_RUNNER` 切換執行模式：

- `JOB_RUNNER=local`（docker-compose 預設）：使用 RQ + Redis，保留 worker container
- `JOB_RUNNER=gcp`（Cloud Run 設定）：呼叫 Cloud Run Jobs API，不需要 Redis 或 worker

`src/app.py` 的調整：
- 模組載入時 **`conn = Redis.from_url(...)` 在兩種模式都初始化**（語意快取需要）；僅 `q = Queue(connection=conn)` 包在 `if JOB_RUNNER == "local":` 內
- `upload_manual_func`（實際函式名，非 `upload_and_enqueue`）：依 `JOB_RUNNER` 分支，`gcp` 模式呼叫 `run_v2.JobsClient().run_job()`，回傳 execution name；`local` 模式呼叫 `q.enqueue()`，回傳 RQ job id
- `poll_status`：依 `JOB_RUNNER` 分支，`gcp` 模式用 `run_v2.ExecutionsClient().get_execution(name=job_id)`；`local` 模式用 `Job.fetch(job_id, connection=conn)`

GCP 狀態對應：`RUNNING` → 執行中、`SUCCEEDED` → 完成、`FAILED` → 失敗。

`requirements.txt` 保留 `rq` 與 `redis`（本機需要），新增 `google-cloud-run`（GCP 需要）。兩個環境共用同一個 Docker image，透過 `JOB_RUNNER` 決定行為。

替代方案：FastAPI BackgroundTasks — App 重啟任務消失，無持久化。

### Cloud Run Job 的進入點：同一 image + Job 層 command override

Service 與 Job 共用同一份 image，但 `Dockerfile` 的 `CMD` 是 `uvicorn src.app:app`（常駐 web server，永不結束）。Service 沿用此 `CMD`；Job 不能跑 web server，需要「執行 `sync_vector_store` 後退出」。

採 **Google 推薦／業界標準的「同一 image、在 Job 資源層 override container command」** 作法，不為 Job 另 build image，也不改 `Dockerfile` 的 `CMD`：

- **進入點重用既有 `__main__`**：`src/build_vector_store.py` 末端已有 `if __name__ == '__main__':` 呼叫 `sync_vector_store()`，因此 Job command 直接設為 `["python", "-m", "src.build_vector_store"]` 即可，**不另開 `run_sync.py`**。
- **Terraform 在 `google_cloud_run_v2_job` 設 `template.template.containers.command`** 為上述命令；`google_cloud_run_v2_service` **不設** `command`，沿用 `Dockerfile` 的 `CMD`。
- **退出碼修正（關鍵）**：Cloud Run Job 以行程 exit code 判定成敗（0=SUCCEEDED、非 0=FAILED）。現有 `__main__` 的 `except` 只 `print` 後仍以 exit 0 結束，會使 sync 失敗被誤判為 SUCCEEDED（且 UI `poll_status` 也誤報成功）。需改為例外時 `sys.exit(1)`，讓失敗如實反映為 FAILED。

此作法對應 12-Factor App 的 admin process（管理型一次性任務以同一 codebase／設定跑成 one-off process）。spec `gcp-cloud-run-job` 既有的「Job runs to completion and exits」「Job failure is recorded」兩個 scenario 由此落實。

替代方案：(a) 為 Job 另 build 一份 image 改 `CMD` — 兩份 image、build 兩次，違反共用 image 決定；(b) 新增 `src/run_sync.py` 專用入口 — 與既有 `__main__` 重複，多一個檔。皆不採用。

### 在 Dockerfile Build 階段預下載 BGE 模型

Cloud Run 冷啟動若需從 HuggingFace 下載 570MB 模型，會導致第一個請求逾時。

在 `Dockerfile` 加入 RUN 指令，build 時預先執行 `snapshot_download('BAAI/bge-reranker-v2-m3', cache_dir=<快取目錄>)`。`snapshot_download` 使用 HF hub 格式 `models--BAAI--bge-reranker-v2-m3/snapshots/<commit>/...`；現代 sentence-transformers（2.3+）的 `CrossEncoder` 下載亦委派 `huggingface_hub`，使用相同格式，故 layout 相符、預下載有效。為避免相符性與冷啟動仍連網的風險，加上以下三項加固：

**(1) 鎖定相依版本**：`requirements.txt` 的 `sentence-transformers` 與 `huggingface_hub` 須釘版本（避免 build 拉到行為不同的版本導致 layout 不符）。

**(2) 快取路徑單一來源**：現行 `src/app.py` 的 `get_reranker_model()` 以 `os.path.join(os.getcwd(), "models")` 推導路徑，與 Dockerfile 寫死的 `cache_dir` 各自為政、可能漂移。改為兩邊共用環境變數 `MODEL_CACHE_DIR`（預設 `/app/models`）：`Dockerfile` 的預下載與 `get_reranker_model()` 的 `cache_folder` 皆讀此變數，從根本消除路徑漂移。

**(3) 離線旗標保證冷啟動零網路**：即使快取已存在，`snapshot_download` / sentence-transformers 預設仍會對 HuggingFace 發 HTTP HEAD 比對 revision，非零網路且 HF 不可達時可能卡住第一個請求。設環境變數 `HF_HUB_OFFLINE=1` 與 `TRANSFORMERS_OFFLINE=1`（快取有就用、絕不連網），真正達成冷啟動零網路，並讓驗證變為確定性。

替代方案：改用 `HuggingFaceCrossEncoder` 自身的下載方式預載（而非 `snapshot_download`）以保證 layout 必然相符——可行，但 build 時需多載一次模型、較慢，且鎖版本 + 離線旗標已足以涵蓋風險，故不採用。

### Container port：uvicorn 讀 `$PORT`（方案 b）

`Dockerfile` 現為 `EXPOSE 80` + `uvicorn --port 80`（port 寫死）。但 Cloud Run 會注入 `PORT` 環境變數（預設 8080）並把流量與健康檢查都送往該 port。若程式仍只聽 80，Cloud Run 敲 8080 無回應 → 健康檢查失敗、Service 收不到流量。

採 **方案 (b)：讓 uvicorn 讀 `$PORT`，未設時退回 80**，使同一份 image 在本機與 Cloud Run 皆正確：

- `Dockerfile` `CMD` 改為 shell form 以展開環境變數：`CMD ["sh", "-c", "uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-80}"]`（exec form 不會展開 `${PORT}`，故需 `sh -c`）。
- Cloud Run：讀到 `PORT=8080` → 聽 8080，與平台對齊。
- 本機 docker-compose：未設 `PORT` → 退回 80，行為與現況一致，不破壞本機。
- Terraform 的 `google_cloud_run_v2_service` **不需特別設 `ports.container_port`**，沿用 Cloud Run 預設 8080。
- `EXPOSE` 改為 `EXPOSE 8080`（純文件用途，Cloud Run 以 `$PORT`/container port 為準，不影響實際流量）。

替代方案 (a)：保留 `--port 80`，改在 Terraform 設 `container_port = 80` 讓 Cloud Run 送往 80。可行，但 Dockerfile 與 Terraform 間形成隱性耦合（任一方漏改即壞），且不符 Cloud Run 用 `$PORT` 的慣例，故不採用。

### 以 GCS bucket 作為 Service 與 Job 的共享儲存

Cloud Run Service 與 Cloud Run Job 是兩個獨立的 ephemeral 檔案系統。現行流程依賴共享檔案系統（docker-compose 下 web 與 worker 共用 `.:/app` volume）：

- **上傳文件**：Service 將上傳檔寫入 `DATA_SOURCE_DIR`，sync 從該目錄讀取
- **BM25 index**：sync 將 `bm25_corpus.json` 寫入 `models/`，Service 查詢時（`get_bm25_index()`）讀取

在 GCP 拆成 Service + Job 後，兩者的本機磁碟互不可見：Service 收到的上傳檔 Job 看不到；Job 建好的 BM25 index 隨容器銷毀而消失。Pinecone 向量（外部服務）不受影響。

解法：新增一個 **GCS bucket，以 Cloud Run（2nd-gen）FUSE volume 掛載到 Service 與 Job 的 `/mnt/data`**。`src/config.py` 的 `DATA_SOURCE_DIR` 與 `src/bm25_index.py` 的持久化路徑改吃環境變數：本機維持相對路徑（`./uploaded_files/`、`models/`），GCP 指向 `/mnt/data` 下對應子目錄。`bm25_corpus.json` 屬「整檔寫、整檔讀」存取，正是 FUSE 最穩的模式。

替代方案：(a) 顯式 GCS SDK get/put — 較可攜但需改 `bm25_index.py` 的 load/save 與 upload 邏輯，改動較大；(b) 把語料存進 Redis — 受 Upstash 免費層大小/請求限制；(c) GCP 完全捨棄 BM25 走 Pinecone-only — 丟棄剛完成的 hybrid-search 成果。皆不採用。

### Web Service 設 max-instances=1

低流量、管理員手動觸發 sync 的場景不需要水平擴展。Gradio 使用全域 singleton 與 `session_hash` 佇列，多實例會造成 session 路由錯亂；且每個實例各持一份記憶體 BM25 index，多實例間無法一致。

設 `max-instances=1`（`min-instances=0` 維持 scale-to-zero）即可從根本消除跨實例不一致，成本不變。流量成長到需要多實例時，再升級為下節的指標檔機制 + 跨實例失效（如 Pub/Sub）。

### BM25 index 自動更新：指標檔 + 惰性重載

`get_reranker_model()` 與 BM25 都把結果快取在 module global（`_bm25_index`），首次查詢載入後不再重讀磁碟。即使 GCS 上的檔案已被 sync 更新，Service 記憶體中的索引仍是舊的，需重啟才更新——此 staleness 在現行 docker-compose 即存在，搬上正式環境會更明顯。

採「指標檔 + 惰性重載」：

- sync 完成時：寫 **write-once 版本檔** `bm25_corpus_<version>.json`（version 用 timestamp 或 content hash，永不覆寫），再**原子更新**指標檔 `bm25_current.txt`（temp → `os.replace`）使其內容指向最新版本檔
- Service 查詢時 `get_bm25_index()` 呼叫 `reload_if_stale()`：讀指標檔，若版本與已載入的不同則 `_load()` 載入指標指向的新檔並更新記憶體版本，相同則直接用記憶體
- `src/bm25_index.py` 的 `_persist()` 現為直接覆寫（`open("w")`），需改為寫版本檔 + 原子更新指標，順帶解決「Service 讀到 Job 寫一半的 JSON」partial-read race
- 版本檔會累積，需保留策略（例如只留最近 N 版，sync 完清理更舊版本），避免 bucket 無限長大；保留多版本同時提供 rollback 能力

此方案不依賴瀏覽器 session（不靠 `poll_status` 在前端開著），上傳新文件後 Web 下次查詢即自動使用新 index。

替代方案：(a) `poll_status` 偵測 `SUCCEEDED` 即重置 singleton — 依賴管理員分頁開著，脆弱；(b) Pub/Sub 推播失效 — 即時且多實例正確，但需額外基礎設施，且 scale-to-zero 時實例不在會漏訊息，`max-instances=1` 場景不需要。

### 使用 GCP Secret Manager 管理 API 金鑰

Cloud Run 支援直接從 Secret Manager 掛載 secret 為環境變數，不需要在 `.env` 或 CI secrets 中明文存放。

Secret 資源（非值）由 Terraform 建立。Secret 的實際值（API keys）在首次部署前手動以 `gcloud secrets versions add` 填入。Terraform 的 `google_cloud_run_v2_service` 資源透過 `env.value_source.secret_key_ref` 引用這些 secret。

共 **9 個 secret**：`OPENAI_API_KEY`、`PINECONE_API_KEY`、`GOOGLE_API_KEY`、`OPENROUTER_API_KEY`、`LANGCHAIN_API_KEY`、`LANGCHAIN_ENDPOINT`、`LANGCHAIN_PROJECT`、`MONGODB_URL`、`REDIS_URL`。（先前清單遺漏 `REDIS_URL`——快取需要——已補入。）

**可觀測性開關**：除上述 9 個 secret 外，Service 與 Job 另注入一個**非機密**環境變數 `LANGCHAIN_TRACING_V2=true`（plain env，非 Secret Manager）。LangChain 僅在此旗標為 true 時才會把 chain 各階段 trace 上報 LangSmith；只有三把 `LANGCHAIN_*` 金鑰而缺此開關，production 追蹤實際上是關閉的。藉此在 LangSmith 取得逐階段延遲／token 瀑布圖，定位 chat 延遲瓶頸（reranker vs 外部 LLM 跳轉）。

**執行期 service account IAM**：Cloud Run Service/Job 的 runtime service account 需 `roles/secretmanager.secretAccessor`（讀 secret，否則容器起不來）；Service 還需觸發 Job 的權限（`roles/run.developer` 或對 `sync-job` 的 invoker）。此與 GitHub Actions 的 WIF service account（`run.admin`、`artifactregistry.writer`）為不同主體，須分別授權。

### GitHub Actions 自動部署（執行 terraform apply）

push 到 `release/**` branch 時，GitHub Actions workflow 執行（master push 不觸發）：
1. 以 Workload Identity Federation 認證 GCP
2. `docker build` → tag 為 git SHA → push 到 Artifact Registry
3. 更新 Terraform 變數 `image_tag` 為新 SHA（透過 `-var` flag）
4. `terraform init` → **scoped** `terraform apply -auto-approve -target=google_cloud_run_v2_service.web -target=google_cloud_run_v2_job.sync`

Terraform apply 負責將 Cloud Run Service 和 Job 更新至新 image tag，不再需要獨立的 `gcloud run deploy` 指令。

使用 Workload Identity Federation 取代 service account JSON key，避免長效憑證外洩風險。

**CI 只 apply app 層（Cloud Run），基礎設施人工本機 apply（最小權限決定）**

第一次走 CI 部署時暴露：workflow 原本跑*完整* `terraform apply`，等於要求 deployer SA 有權管理*全部*資源（SA、IAM bindings、Secret、WIF、buckets、AR repo）。但 deployer SA 刻意只給「部署 app」等級權限（`run.admin` + `artifactregistry.writer`），於是 refresh 既有 SA/secret 時 403。

決定不放大 deployer 權限（方案 A：給近乎 owner，CI 被攻破即專案被接管），改採**方案 B：縮小 CI 範圍**：

- **CI 的 apply 用 `-target` 只動 Cloud Run Service/Job**。`-target` 會連帶 read-only refresh 其相依（runtime SA、9 個 secret 資源、共享 bucket），但不會建立/修改任何基礎設施。
- **基礎設施（SA、IAM、Secret、WIF、bucket、AR repo）由人以 owner 身分本機 `terraform apply` 管理** — 這些極少變動，且不該讓 CI 有權改寫（符合 runtime/deployer 雙 SA 分權精神）。
- **deployer SA 維持最小權限**：除 `run.admin` + `artifactregistry.writer`，僅再加 runtime SA 上的 `iam.serviceAccountUser`（actAs）、`secretmanager.viewer`（只讀 secret metadata、非值）、tfstate bucket 的 `storage.objectAdmin`、共享 bucket 的 `storage.legacyBucketReader`。**不給** owner/editor/任何 IAM 或 secret 寫入角色 → CI 被攻破也無法提權。
- **必要 API**：除 run/artifactregistry/secretmanager/storage 外，須啟用 `iam.googleapis.com`（refresh SA）與 `iamcredentials.googleapis.com`（WIF 模擬）。此二者及上述 IAM 授權皆為**手動 bootstrap**，因牽涉 deployer SA 自身權限，屬雞生蛋、不納入 CI 可改寫範圍。

代價：日後若基礎設施有變（加 secret、改 IAM），需人工本機 apply，CI 不會自動套用 — 這正是分權的取捨，可接受。

## Implementation Contract

**Terraform 目錄結構：**
- `terraform/main.tf`：所有資源定義（provider、Cloud Run、Artifact Registry、Secret Manager、IAM）
- `terraform/variables.tf`：變數宣告（`project_id`、`region`、`image_tag`）
- `terraform/outputs.tf`：輸出值（Cloud Run Service URL）
- `terraform/terraform.tfvars.example`：範例變數檔（不含敏感值，納入 git）
- `terraform/backend.tf`：GCS remote backend 設定
- `terraform.tfvars`（不納入 git，列於 `.gitignore`）：實際變數值

**Cloud Run Service（Web App）：**
- 部署後產生 `https://<service-name>-<hash>-de.a.run.app` HTTPS URL，由 `terraform output cloud_run_url` 輸出
- 允許未驗證存取（IAM binding `roles/run.invoker` → `allUsers`）
- 記憶體 2GB、1 vCPU、`min-instances=0`、`max-instances=1`
- uvicorn 讀 `$PORT`（`Dockerfile` `CMD` 用 `${PORT:-80}`）；Service 不設 `ports.container_port`，沿用 Cloud Run 預設 8080
- BGE 模型快取：`Dockerfile` 預下載與 `get_reranker_model()` 共用 `MODEL_CACHE_DIR`（預設 `/app/models`）；設 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 保證冷啟動零網路
- 掛載 GCS volume 到 `/mnt/data`，`DATA_SOURCE_DIR` 與 BM25 路徑指向其下子目錄
- 環境變數由 Secret Manager 注入（9 個 secret，含 `REDIS_URL`），啟動時可讀取 `os.environ["OPENAI_API_KEY"]` 等
- runtime service account 具 `roles/secretmanager.secretAccessor` 及觸發 `sync-job` 的權限

**Cloud Run Job（sync_vector_store）：**
- `google_cloud_run_v2_job` 的 container `command` 設為 `["python", "-m", "src.build_vector_store"]`（Job 層 override），Service 不設 `command` 沿用 Dockerfile `CMD`
- Job 進入點重用 `src/build_vector_store.py` 既有的 `__main__`，並修正其 `except` 分支於失敗時 `sys.exit(1)`（否則 sync 失敗被誤判為 SUCCEEDED）
- `src/app.py` 的 `upload_manual_func` 函式在 `JOB_RUNNER=gcp` 時不呼叫 `q.enqueue()`
- 改呼叫 `run_v2.JobsClient().run_job(name="projects/.../jobs/sync-job")`，回傳 execution name 作為 job_id
- UI 的 `poll_status(job_id)` 改從 `run_v2.ExecutionsClient().get_execution(name=job_id)` 取得狀態
- Job 執行 timeout 設為 3600 秒（1 小時）
- 掛載與 Service 相同的 GCS volume 到 `/mnt/data`，sync 寫入 BM25 版本檔與指標檔於其下

**驗收條件：**
- `terraform apply` 從零可成功建立所有 GCP 資源，無手動操作
- Cloud Run Service URL 可在瀏覽器開啟 Gradio UI
- 點擊「Upload and Enqueue Sync」後，GCP Console → Cloud Run Jobs → 執行記錄出現新的 execution
- execution 完成後 UI 顯示「同步成功！知識庫已更新。」
- push 到 master 後，GitHub Actions workflow 成功執行（綠燈），Cloud Run 自動更新至新 image

**Scope 邊界：**
- 範圍內：`terraform/`（所有 .tf 檔，含 GCS bucket、runtime SA IAM）、`Dockerfile`、`src/app.py`（觸發、狀態查詢、`conn`/`q` 分離、`get_bm25_index` reload）、`src/config.py`（路徑改環境變數）、`src/bm25_index.py`（原子寫 + 版本檔/指標檔 + `reload_if_stale` + 版本清理）、`docker-compose.yml`、`requirements.txt`、`.github/workflows/deploy.yml`
- 範圍外：`sync_vector_store` 業務邏輯、Pinecone 整合、BM25 評分/檢索演算法、所有其他 src/ 檔案

## Risks / Trade-offs

- [Risk] Terraform state 若未設定 remote backend 而使用本機 state，多人協作或 CI 環境會造成 state 衝突 → Mitigation：首次執行前先建立 GCS bucket 作為 remote backend
- [Risk] Upstash 免費層 10,000 指令/天，由 `poll_status` polling **與語意快取讀寫**共同分攤（快取在 GCP 模式仍使用 Redis） → Mitigation：polling 間隔確認為 3-5 秒一次；配額評估需含快取流量
- [Risk] BM25 `_persist()` 現為直接覆寫，GCS 共享下 Service 可能讀到 Job 寫一半的 JSON（partial-read race） → Mitigation：改為 write-once 版本檔 + 原子更新指標檔
- [Risk] Service 記憶體中的 `_bm25_index` 為 module global，sync 後不會自動重讀磁碟 → Mitigation：`get_bm25_index()` 加 `reload_if_stale()` 比對指標檔版本
- [Risk] BM25 版本檔在 GCS 累積導致 bucket 持續長大 → Mitigation：sync 完成後保留最近 N 版、清理更舊版本
- [Risk] BGE 模型預下載使 Docker image 增大 570MB，build 時間增加 → Mitigation：可接受，image 大小換取零冷啟動延遲是正確取捨
- [Risk] `terraform apply` 在 CI 環境執行若 state 不一致可能造成資源被重建 → Mitigation：使用 `terraform plan` 先預覽，確認無意外變更後再 apply；Cloud Run revision 管理確保可 rollback
- [Risk] Atlas 白名單開 `0.0.0.0/0`，DB 端點暴露公網、減少一層 IP 防護 → Mitigation：強隨機密碼僅存 Secret Manager、最小權限 DB user（僅 `chatbot` 的 `readWrite`）、強制 `mongodb+srv` TLS；需更嚴格隔離時升級 M10+ 私網或加 Cloud NAT 靜態 IP

## Migration Plan

1. 手動建立 GCS bucket 作為 Terraform remote backend
2. 在 Upstash 建立 Redis instance，在 MongoDB Atlas 建立 M0 cluster
3. 撰寫 `terraform/` 目錄下所有 .tf 檔（含 GCS bucket、runtime SA IAM）
4. 執行 `terraform init` 和 `terraform apply` 建立所有 GCP 資源（第一次，不含 image，先用 placeholder）
5. 手動填入 Secret Manager 各 secret 的值（9 個）
6. 修改 `Dockerfile` 加入 BGE 模型預下載
7. 修改 `src/config.py` 路徑改環境變數、`src/bm25_index.py` 改原子寫 + 指標檔 + `reload_if_stale`
8. 修改 `src/app.py`：`conn`/`q` 分離、`JOB_RUNNER` 切換、`get_bm25_index` 加 reload
9. 建立 `.github/workflows/deploy.yml`（build → push → terraform apply）
10. 在 GitHub 設定 Workload Identity Federation
11. push 到 `release/**`，驗證自動部署流程

**Rollback：** `terraform apply` 前先執行 `terraform plan` 確認變更；Cloud Run Service 支援 revision 管理，可透過 Terraform 指定前一個 image tag 回滾；BM25 可將指標檔切回前一版本檔回滾索引。

## Open Questions

（本輪已解決 Redis 去留：語意快取 `PromptCacheService` 需要 Redis，兩種模式皆保留 `conn`，不移除。）

已解決：Cloud Run Job 進入點 — 採同一 image + Job 層 `command` override 為 `["python", "-m", "src.build_vector_store"]`（重用既有 `__main__`，並修正失敗時 `sys.exit(1)`），詳見上方「Cloud Run Job 的進入點」決定。

已解決：Container port — 採方案 (b)，uvicorn 讀 `${PORT:-80}`（`Dockerfile` `CMD` 改 shell form），Service 不設 container port 沿用 Cloud Run 預設 8080，詳見上方「Container port」決定。

已解決：BGE 模型 cache layout — 現代 sentence-transformers 與 `snapshot_download` 使用相同 HF hub 格式故相符；另加固：鎖 `sentence-transformers`/`huggingface_hub` 版本、cache 路徑改吃 `MODEL_CACHE_DIR` 單一來源、設 `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` 保證冷啟動零網路，詳見上方「在 Dockerfile Build 階段預下載 BGE 模型」決定。

已解決：MongoDB Atlas M0 IP allowlist — 採方案 (a) 白名單開 `0.0.0.0/0`，安全由強密碼（存 Secret Manager）+ 最小權限 DB user + `mongodb+srv` TLS 承擔；M0 不支援私網，更嚴格隔離留待升級 M10+ 或加 Cloud NAT 靜態 IP，詳見上方「MongoDB Atlas M0 的 IP allowlist」決定。

（本輪四項待討論議題已全部討論定案並寫回。）
