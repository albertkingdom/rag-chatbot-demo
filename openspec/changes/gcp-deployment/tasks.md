## 1. GCP 專案初始化與前置作業

- [ ] 1.1 在 GCP Console 啟用必要 API（Cloud Run、Artifact Registry、Secret Manager、Cloud Storage），確認 `gcloud services list --enabled` 輸出包含 `run.googleapis.com`、`artifactregistry.googleapis.com`、`secretmanager.googleapis.com`、`storage.googleapis.com`
- [ ] 1.2 手動建立 GCS bucket 作為 Terraform remote backend（名稱如 `<project-id>-tfstate`，區域 `asia-east1`），確認 `gcloud storage buckets describe gs://<bucket-name>` 顯示 bucket 存在 — 滿足「Terraform remote state stored in GCS」需求
- [ ] 1.3 安裝 Terraform CLI（v1.5+），確認 `terraform version` 回傳版本號

## 2. 外部服務設定（以 Upstash Redis 取代 Redis Container）

- [ ] 2.1 在 Upstash 建立 Redis instance，取得 `REDIS_URL`（格式 `rediss://...`），確認可從本機 `redis-cli -u $REDIS_URL ping` 回傳 `PONG`。註：Redis 在 GCP 模式仍為必要依賴（語意快取 `PromptCacheService` 使用），不可移除
- [ ] 2.2 在 MongoDB Atlas 建立 M0 免費 cluster：建立**最小權限 database user**（僅授予 `chatbot` database 的 `readWrite`，不給 `atlasAdmin`/cluster 級權限）與 `chatbot` database；**IP allowlist 設 `0.0.0.0/0`**（因 Cloud Run 動態出口 IP 無法列舉、M0 不支援私網，安全由帳密 + TLS 承擔）；取得 `mongodb+srv://...` 連線字串作為 `MONGODB_URL`。確認本機可用 `mongosh "$MONGODB_URL"` 連線成功，且連線字串為 `mongodb+srv` 形式（TLS 啟用）— 滿足 design「MongoDB Atlas M0 的 IP allowlist」決定

## 3. Terraform 設定（使用 Terraform 管理 GCP 基礎設施）

- [ ] 3.1 建立 `terraform/backend.tf`，設定 GCS remote backend（bucket 指向 1.2 建立的 bucket），確認 `terraform init` 成功連線 GCS backend 並顯示 `Successfully configured the backend "gcs"`
- [x] 3.2 建立 `terraform/variables.tf`，宣告 `project_id`、`region`（預設 `asia-east1`）、`image_tag` 三個變數；建立 `terraform/terraform.tfvars.example` 含三個變數的佔位值，確認檔案可被 git 追蹤且不含敏感資料 — 滿足「Terraform variables control image tag and project configuration」需求
- [ ] 3.3 建立 `terraform/main.tf`，定義 `google_artifact_registry_repository`（名稱 `carbon-assistant`）、`google_storage_bucket`（共享資料 bucket）和 9 個 `google_secret_manager_secret` 資源（不含 secret 值）；執行 `terraform plan` 確認計畫顯示將新增這些資源 — 滿足「Terraform manages all GCP resources」需求
- [ ] 3.4 在 `terraform/main.tf` 加入 `google_cloud_run_v2_service`（`carbon-assistant-web`，memory 2Gi、cpu 1、min-instances 0、**max-instances 1**、allow-unauthenticated，含明文環境變數 `JOB_RUNNER=gcp`、GCS volume 掛載至 `/mnt/data`，**不設 container `command`，沿用 Dockerfile `CMD`**）和 `google_cloud_run_v2_job`（`sync-job`，task-timeout 3600、相同 GCS volume 掛載至 `/mnt/data`、**container `command = ["python", "-m", "src.build_vector_store"]` 覆寫 entrypoint**），兩者均透過 `env.value_source.secret_key_ref` 引用 9 個 Secret Manager secrets — 滿足「Cloud Run resources configured with Secret Manager references」「Scale-to-zero with single-instance cap」「Shared GCS bucket mounted on both Cloud Run resources」「Cloud Run Job runs the sync entrypoint via container command override」需求；驗證：`terraform plan` 顯示兩個 Cloud Run 資源、`JOB_RUNNER=gcp`、`max_instance_count = 1`、兩者皆有 `/mnt/data` GCS volume，且 Job 的 `command` 為 `["python", "-m", "src.build_vector_store"]`、Service 無 `command`
- [ ] 3.5 在 `terraform/main.tf` 加入 `google_iam_workload_identity_pool`、`google_iam_workload_identity_pool_provider`（設定 GitHub repo 為允許的 subject）及 GitHub Actions 部署用 `google_service_account`（附 `roles/run.admin`、`roles/artifactregistry.writer` 權限）；確認 `terraform plan` 包含這些 IAM 資源 — 滿足「GCP authentication uses Workload Identity Federation」需求
- [ ] 3.6 在 `terraform/main.tf` 加入 Cloud Run **執行期** service account 及其 IAM bindings：`roles/secretmanager.secretAccessor`、共享 bucket 的物件讀寫、觸發 `sync-job` 的權限（`roles/run.developer` 或對 job 的 invoker）；將此 SA 指定為 Service 與 Job 的 `service_account` — 滿足「Cloud Run runtime service account has least-privilege IAM」「Service can read secrets and trigger the Job」需求；驗證：`terraform plan` 顯示 runtime SA 與三項 binding，且與 GitHub Actions 部署 SA 為不同主體
- [ ] 3.7 建立 `terraform/outputs.tf`，輸出 `cloud_run_url`；執行 `terraform apply -auto-approve` 建立所有資源，確認 `terraform output cloud_run_url` 回傳非空 URL — 滿足「Fresh environment provisioning」需求

## 4. Secret Manager 填值

- [ ] 4.1 手動執行 `gcloud secrets versions add <secret-name> --data-file=-` 為 9 個 secret 各填入實際值（`OPENAI_API_KEY`、`PINECONE_API_KEY`、`GOOGLE_API_KEY`、`OPENROUTER_API_KEY`、`LANGCHAIN_API_KEY`、`LANGCHAIN_ENDPOINT`、`LANGCHAIN_PROJECT`、`MONGODB_URL`、`REDIS_URL`），確認 `gcloud secrets versions list <secret-name>` 顯示 version 1 為 ENABLED

## 5. Dockerfile 修改（在 Dockerfile Build 階段預下載 BGE 模型）

- [ ] 5.1 修改 `Dockerfile`：設 `ENV MODEL_CACHE_DIR=/app/models`，在 `pip install` 步驟後加入 `RUN python -c "import os; from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-reranker-v2-m3', cache_dir=os.environ['MODEL_CACHE_DIR'])"`，並設 `ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` — 滿足「BGE Reranker model pre-downloaded in Docker image」需求（pre-download 與 load 共用 `MODEL_CACHE_DIR`、離線旗標）；驗證：`docker build -t test-app .` 成功且 `docker run test-app ls /app/models/models--BAAI--bge-reranker-v2-m3` 顯示模型檔案
- [ ] 5.2 修改 `Dockerfile`：`CMD` 改為 shell form 讓 uvicorn 讀 `$PORT`（`CMD ["sh", "-c", "uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-80}"]`），`EXPOSE` 改為 `EXPOSE 8080` — 滿足「Service listens on the Cloud Run injected $PORT」需求；驗證：`docker run -e PORT=8080 -p 8080:8080 test-app` 後 `curl localhost:8080` 有回應；不設 `PORT` 啟動則聽 80（行為與現況一致）
- [x] 5.3 在 `requirements.txt` 釘定 `sentence-transformers` 與 `huggingface_hub` 版本（範圍須使 `CrossEncoder` 快取採 HF hub layout，如 `sentence-transformers>=2.3,<6`，與本機實裝 5.x 相容），確認 `pip install -r requirements.txt` 後 `pip show sentence-transformers huggingface_hub` 版本落在指定範圍 — 滿足「BGE Reranker model pre-downloaded in Docker image」需求（鎖版本以保證 layout 相符）

## 6. src/app.py 加入 JOB_RUNNER 切換（JOB_RUNNER environment variable controls task execution mode；以 Cloud Run Job 取代 RQ Worker 透過 job_runner 環境變數切換；sync_vector_store runs as Cloud Run Job in GCP mode）

- [x] 6.1 在 `requirements.txt` 加入 `google-cloud-run`，保留 `rq` 和 `redis`，確認 `pip install -r requirements.txt` 成功且 `pip show rq google-cloud-run` 兩者均顯示已安裝 — 滿足「requirements.txt supports both local and GCP modes」需求
- [x] 6.2 在 `docker-compose.yml` 的 `web` service 加入環境變數 `JOB_RUNNER=local`，確認 `docker compose config` 的 web service 環境變數包含 `JOB_RUNNER: local` — 滿足「docker-compose sets local mode」需求
- [x] 6.3 修改 `src/app.py`：在模組頂層讀取 `JOB_RUNNER = os.environ.get("JOB_RUNNER", "local")`，以此環境變數切換任務執行模式（JOB_RUNNER environment variable controls task execution mode）；`conn = Redis.from_url(...)` **在兩種模式都初始化**（語意快取 `PromptCacheService` 需要），僅將 `q = Queue(connection=conn)` 包在 `if JOB_RUNNER == "local":` 區塊內 — 滿足「JOB_RUNNER environment variable controls task execution mode」「Local mode initializes RQ on startup」「GCP mode initializes Redis but skips RQ queue」「Redis available in GCP mode for semantic cache」需求；驗證：以 `JOB_RUNNER=gcp` 啟動後 chat 查詢的快取讀寫正常、且未建立 `rq.Queue`
- [x] 6.4 修改 `src/app.py` 的 `upload_manual_func` 函式（實際函式名）：加入 `if JOB_RUNNER == "gcp":` 分支呼叫 `run_v2.JobsClient().run_job(name="projects/{PROJECT}/locations/asia-east1/jobs/sync-job")`，回傳 execution name；`else:` 分支保留 `q.enqueue(sync_vector_store, job_timeout='1h')` — 滿足「sync_vector_store runs as Cloud Run Job in GCP mode」「Upload triggers Cloud Run Job execution in GCP mode」和「Upload triggers RQ job in local mode」需求；驗證：本機 `docker compose up` 後上傳文件觸發 RQ job，確認 worker log 出現執行紀錄
- [x] 6.5 修改 `src/app.py` 的 `poll_status` 函式：加入 `if JOB_RUNNER == "gcp":` 分支用 `run_v2.ExecutionsClient().get_execution(name=job_id)` 取得狀態；`else:` 分支保留 `Job.fetch(job_id, connection=conn)` — 滿足「UI polls status using mode-appropriate client」需求；驗證：本機完成 sync 後 UI 顯示「同步成功！知識庫已更新。」

- [x] 6.6 修改 `src/build_vector_store.py` 的 `if __name__ == '__main__':` 區塊：`except` 分支於印出錯誤後呼叫 `sys.exit(1)`（成功路徑維持 exit 0），使 Cloud Run Job 能依行程退出碼正確判定成敗 — 滿足「Sync failure exits non-zero」需求；驗證：本機以可預期失敗的輸入執行 `python -m src.build_vector_store; echo $?` 回傳非 0；成功時回傳 0
- [x] 6.7 修改 `src/app.py` 的 `get_reranker_model()`：`model_dir` 改為 `os.environ.get("MODEL_CACHE_DIR", os.path.join(os.getcwd(), "models"))`，使 runtime 載入路徑與 Dockerfile 預下載的 `MODEL_CACHE_DIR` 為同一來源、不再依賴 `os.getcwd()` — 滿足「Pre-download and load paths share one source」需求；驗證：設 `MODEL_CACHE_DIR=/app/models` 後 `get_reranker_model()` 從該目錄載入；未設時值與現況相同

## 7. GCS 共享儲存路徑與 BM25 指標檔惰性重載（Shared GCS storage mounted as a volume；BM25 index persisted via write-once version file and atomic pointer；BM25 index reloads when the pointer changes）

- [x] 7.1 修改 `src/config.py`：`DATA_SOURCE_DIR` 與 BM25 持久化目錄改吃環境變數（如 `DATA_SOURCE_DIR = os.environ.get("DATA_SOURCE_DIR", "./uploaded_files/")`、BM25 目錄預設 `models/`），本機維持相對路徑、GCP 由 Cloud Run 設為 `/mnt/data` 下子目錄 — 滿足「Shared GCS storage mounted as a volume」需求；驗證：未設環境變數時值與現況相同；設 `DATA_SOURCE_DIR=/mnt/data/uploaded_files` 後 `python -c "from src import config; print(config.DATA_SOURCE_DIR)"` 顯示新值
- [x] 7.2 修改 `src/bm25_index.py` 的 `_persist()`：改為寫 write-once 版本檔 `bm25_corpus_<version>.json`（version 用 timestamp 或 content hash，不覆寫既有版本），再以 temp 檔 + `os.replace` 原子更新指標檔 `bm25_current.txt` 指向最新版本檔 — 滿足「BM25 index persisted via write-once version file and atomic pointer」需求；驗證：連續兩次 `build_from_documents` 後目錄存在兩個版本檔，`bm25_current.txt` 內容為較新者檔名
- [x] 7.3 修改 `src/bm25_index.py` 的 `_load()` 改為讀指標檔解析出版本檔再載入，並記錄「已載入版本」；新增 `reload_if_stale()` 方法：比對指標檔版本與已載入版本，不同才重跑 `_load()` — 滿足「BM25 index reloads when the pointer changes」需求；驗證：載入後手動更新指標指向新版本檔，呼叫 `reload_if_stale()` 後 `corpus_size` 反映新檔；指標未變時 `reload_if_stale()` 不重讀
- [x] 7.4 修改 `src/app.py` 的 `get_bm25_index()`：回傳索引前呼叫 `reload_if_stale()` — 滿足「Service auto-loads the latest BM25 index after sync」「New upload becomes searchable without restart」需求；驗證：本機上傳新文件 sync 完成後，不重啟 web 直接查詢即可命中新文件內容
- [x] 7.5 在 sync 流程加入版本檔保留/清理：更新指標後，刪除最近 N 版以外的舊版本檔，且永不刪除指標檔指向的版本 — 滿足「Old BM25 version files are pruned」需求；驗證：產生 N+2 個版本後執行清理，僅剩最近 N 個，且 `bm25_current.txt` 指向的檔仍存在

## 8. 初次手動部署驗證（Web App deployed to Cloud Run Service；sync_vector_store runs as Cloud Run Job；使用 GCP Secret Manager 管理 API 金鑰）

- [ ] 8.1 手動執行 `docker build` 並 push image 到 Artifact Registry（tag 為任意測試 SHA），再執行 `terraform apply -var="image_tag=<test-sha>"` 完成首次含 image 的完整部署 — 滿足「Web App deployed to Cloud Run Service」、「Scale-to-zero with single-instance cap」和「API secrets injected via GCP Secret Manager」需求；驗證：`terraform output cloud_run_url` 的 URL 可在瀏覽器開啟 Gradio UI
- [ ] 8.2 確認冷啟動零網路載入模型：在 `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` 下，Cloud Run Logs 顯示「BGE-Reranker model initialized successfully on cpu」且無任何 HuggingFace 下載進度或連線訊息（離線仍能載入即證明 layout 相符且預下載生效） — 滿足「Cold start performs no network access」需求；可另在本機以 `docker run -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 --network none test-app` 觸發一次 reranker 載入,確認無連線錯誤即成功
- [ ] 8.3 手動觸發一次 sync-job 端對端流程：透過 Gradio UI 上傳測試文件並點擊「Upload and Enqueue Sync」，確認 GCP Console → Cloud Run Jobs → sync-job 出現新 execution，execution 完成後狀態為 SUCCEEDED，UI 顯示成功訊息；接著不重啟 Service 直接查詢上傳文件內容，確認 BM25 指標檔重載生效 — 滿足「sync_vector_store runs as Cloud Run Job」「Job runs to completion and exits」「New upload becomes searchable without restart」需求

## 9. GitHub Actions CI/CD 設定（GitHub Actions 自動部署（執行 terraform apply））

- [ ] 9.1 在 GitHub repository 設定 3 個 Secrets（`GCP_PROJECT_ID`、`WIF_PROVIDER`、`WIF_SERVICE_ACCOUNT`，值從 `terraform output` 取得），確認 Settings → Secrets → Actions 顯示全部 3 個 secret
- [ ] 9.2 建立 `.github/workflows/deploy.yml`：workflow 觸發條件設為 `on: push: branches: ['release/**']`（master push 不觸發），執行 WIF 認證 → docker build（tag 為 git SHA）→ push 到 Artifact Registry → `terraform init` → `terraform apply -auto-approve -var="image_tag=$GITHUB_SHA"` — 滿足「GitHub Actions deploys to Cloud Run on push to release branch」和「Docker image tagged with git SHA」需求；驗證：建立 `release/1.0.0` branch 並 push，GitHub Actions workflow 全部步驟綠燈；另外直接 push 到 master 確認 workflow 不被觸發
- [ ] 9.3 執行 `terraform apply` 進行二次 apply 確認 idempotency：`terraform plan` 應顯示 `No changes. Infrastructure is up-to-date.` — 滿足「Idempotent re-apply」需求
