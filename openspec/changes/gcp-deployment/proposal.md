## Why

目前專案以 `docker-compose` 在本機運行，缺乏可公開存取的穩定端點及自動化部署流程。部署至 GCP 可提供公開 HTTPS URL、自動擴縮、以及 CI/CD 整合，使專案進入可維運狀態。使用 Terraform 管理基礎設施可確保環境可重現、變更可追蹤。

## What Changes

- 將 FastAPI + Gradio Web App 部署至 **Cloud Run Service**，取得公開 HTTPS URL
- 加入環境變數 `JOB_RUNNER`（`local` / `gcp`）切換任務執行模式：本機使用 RQ，GCP 使用 Cloud Run Job
- GCP 環境將 `sync_vector_store` 改為 **Cloud Run Job**，執行完自動結束，不需常駐；本機仍維持 RQ worker
- 新增 **GCS bucket 作為 Service 與 Job 的共享儲存**（以 Cloud Run FUSE volume 掛載到 `/mnt/data`），共享上傳文件目錄與 BM25 index；`src/config.py` 的 `DATA_SOURCE_DIR` 與 BM25 持久化路徑改吃環境變數，本機維持相對路徑、GCP 指向掛載點
- BM25 index 改為 **「指標檔 + 惰性重載」**：sync 寫 write-once 版本檔 `bm25_corpus_<version>.json` 並原子更新指標檔 `bm25_current.txt`；Web Service 查詢時比對指標，版本變了才重載記憶體中的索引，使上傳新文件後 Web 自動使用新 index
- Web Service 設 **`max-instances=1`**：低流量場景不需水平擴展，並消除多實例間記憶體索引不一致；`min-instances=0` 維持 scale-to-zero
- 以 **Upstash Redis**（免費層）供 GCP 環境使用；本機繼續使用 docker-compose Redis container。**Redis 不可移除**：語意快取 `PromptCacheService` 在兩種模式都需要 `conn`，僅 RQ `Queue`（`q`）為本機限定
- 以 **MongoDB Atlas M0**（免費層）取代 docker-compose 的 MongoDB container
- 在 **Dockerfile** 預先下載 `BAAI/bge-reranker-v2-m3` 模型（約 570MB），解決 Cloud Run 冷啟動問題
- 使用 **GCP Secret Manager** 集中管理所有 API 金鑰，取代 `.env` 檔案
- 使用 **Terraform** 管理所有 GCP 資源（Cloud Run Service、Cloud Run Job、Artifact Registry、Secret Manager、IAM），確保環境可版本控制與重現
- 加入 **GitHub Actions** workflow，push 到 `release/**` branch 時自動建置 Docker image 並執行 `terraform apply` 完成部署；master push 不觸發部署
- `docker-compose.yml` 保持不變，`redis-insight` 與 `mongo-express` 繼續保留供本機開發使用

## Non-Goals

- 不使用 GCP Memorystore（Upstash 免費層已符合需求，避免額外費用）
- 不使用 GCP Vertex AI Ranking API 取代本地 BGE Reranker（繁體中文效果較佳且免費）
- 不設定 staging 環境（單一 production 環境即可）
- 不修改 `sync_vector_store` 的業務邏輯，僅調整觸發機制
- 不以 Terraform 管理 Upstash Redis 與 MongoDB Atlas（第三方服務，手動設定後將連線字串存入 Secret Manager）

## Capabilities

### New Capabilities

- `gcp-cloud-run-service`: FastAPI + Gradio Web App 在 Cloud Run 上的部署設定，包含環境變數、Secret Manager 整合、GCS volume 掛載、`max-instances=1`、BM25 指標檔惰性重載及公開存取設定
- `gcp-cloud-run-job`: `sync_vector_store` 以 Cloud Run Job 執行的設定，包含觸發方式從 RQ enqueue 改為 GCP Jobs API 呼叫、GCS volume 掛載、BM25 版本檔寫入，以及 `conn`（Redis，兩模式皆需）與 `q`（RQ，本機限定）的初始化分離
- `gcp-terraform`: Terraform 設定檔管理所有 GCP 資源，包含 Cloud Run Service、Cloud Run Job、Artifact Registry、Secret Manager、GCS bucket、執行期 service account IAM 及 IAM
- `gcp-cicd-pipeline`: GitHub Actions workflow，自動建置 Docker image 並執行 terraform apply 部署至 Cloud Run

### Modified Capabilities

（無，現有 spec 的行為需求不變）

## Impact

- Affected specs: `gcp-cloud-run-service`（新增）、`gcp-cloud-run-job`（新增）、`gcp-terraform`（新增）、`gcp-cicd-pipeline`（新增）
- Affected code:
  - Modified: `Dockerfile`、`src/app.py`（`JOB_RUNNER` 切換、`conn`/`q` 分離、`upload_manual_func`、`poll_status`、`get_bm25_index` 加 reload）、`src/config.py`（路徑改環境變數）、`src/bm25_index.py`（`_persist` 改原子寫 + 版本檔/指標檔、新增 `reload_if_stale`、版本檔清理）、`docker-compose.yml`（加入 `JOB_RUNNER=local`）、`requirements.txt`（新增 `google-cloud-run`，保留 `rq`、`redis`）
  - New: `terraform/main.tf`、`terraform/variables.tf`、`terraform/outputs.tf`、`terraform/backend.tf`、`terraform/terraform.tfvars.example`、`.github/workflows/deploy.yml`
  - Unchanged: `docker-compose.yml` 的服務定義（redis-insight、mongo-express 保留）、`sync_vector_store` 業務邏輯
