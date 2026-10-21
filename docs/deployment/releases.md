# 發布紀錄

一般版本的發布流程與每版紀錄。React 切換時的完整手冊見 [replace-gradio-frontend.md](replace-gradio-frontend.md)。

## 發布流程

`release/**` push 會觸發 `.github/workflows/deploy.yml`：建置 image（tag 為 commit SHA），再以 Terraform 更新 web Service 與 sync Job 的 image。Terraform 不管理 Service 的 traffic，所以當正式流量固定在指定 revision 時，CI 建立的新 revision 為 0% 流量，可先驗收再切換。sync Job 沒有流量概念，push 後即使用新 image。

1. **保存狀態**：記錄目前 revision 與 traffic，確認 traffic 固定在指定 revision（不是 latest）。
   ```sh
   gcloud run services describe carbon-assistant-web --project <project-id> --region asia-east1 \
     --format='json(status.latestReadyRevisionName,status.traffic,status.url,spec.template.spec.containers[0].image)'
   ```
2. **檢查 Terraform plan**（唯讀，`-lock=false`）：確認只變更 image，不動 traffic。第一次包含 `PINECONE_INDEX_NAME` 的版本，plan 還會在 web Service 與 sync Job 各新增一個 `PINECONE_INDEX_NAME = carbon-assistant-qa-index` 的 env；值與先前寫死在程式中的 index 相同，屬預期變更。
   ```sh
   cd terraform && terraform init -backend-config="bucket=<state-bucket>" && terraform plan -lock=false \
     -target=google_cloud_run_v2_service.web -target=google_cloud_run_v2_job.sync \
     -var="project_id=<project-id>" -var="region=asia-east1" \
     -var="image_tag=$(git rev-parse HEAD)" -var="github_repository=albertkingdom/rag-chatbot-demo"
   ```
3. **push release 分支**：從 master 建立 `release/vX.Y.Z` 並 push，等 CI 成功，確認新 revision ready、正式流量未變。
4. **加 preview tag 並驗收候選 URL**：
   ```sh
   gcloud run services update-traffic carbon-assistant-web --project <project-id> --region asia-east1 \
     --set-tags=<tag>=<new-revision>
   ```
   - 不需登入：`/health` 200、`/`、`/login`、`/admin` 回 SPA、hashed JS 可載入、未登入 API 回 JSON 401（未登入時不存在的 `/api/*` 也回 401，因為驗證在路由之前）、新 revision 無 ERROR log。
   - 需登入（由使用者操作）：聊天、來源、本版修改的功能。
5. **切換流量**到明確的 revision，再驗收正式 URL 與 log：
   ```sh
   gcloud run services update-traffic carbon-assistant-web --project <project-id> --region asia-east1 \
     --to-revisions=<new-revision>=100
   ```
6. **移除 preview tag**（保留 revision 供回復）：`--remove-tags=<tag>`。

回復：`--to-revisions=<previous-revision>=100`，再確認 `/health` 與登入頁。

## 切換正式站知識庫

第一次發布 `carbon-assistant-sample-index` 設定時使用（換成範例手冊）。CI 的 `terraform apply` 會讓 sync Job 立即改用新 index 與 `/mnt/data/sample/` 目錄，web 新 revision 則是 0% 流量，所以下列步驟在一般發布流程的第 3 步之後接著做。以下 `<data-bucket>` 為 `/mnt/data` 掛載的 bucket，名稱是 `<project-id>-carbon-assistant-data`（見 `terraform/main.tf` 的 `google_storage_bucket.shared`）。

1. **事前確認**：Pinecone 方案可再建立一個 index。從這一步到第 6 步結束都不要在「文件管理」上傳手冊，並確認沒有執行中的同步：
   ```sh
   gcloud run jobs executions list --job sync-job --project <project-id> --region asia-east1 --limit 3
   ```
2. **發布**：照一般流程第 1 到 3 步。plan 會顯示 web 與 Job 的 `PINECONE_INDEX_NAME`、`DATA_SOURCE_DIR`、`BM25_INDEX_DIR` 改為新值。
3. **放入範例手冊並同步**：
   ```sh
   gcloud storage cp examples/carbonm_sample_manual.xlsx gs://<data-bucket>/sample/uploaded_files/
   gcloud run jobs execute sync-job --project <project-id> --region asia-east1 --wait
   ```
   每次執行都要成立的驗收條件：
   - Job execution 成功（同步回傳錯誤時 Job 會以結束碼 1 失敗）。
   - log 有 `Sync complete. Total vectors: 60.`、`BM25 index rebuilt with 60 documents.` 與 `Semantic cache cleared`。
   - `carbon-assistant-sample-index` 的向量數為 60（Pinecone 主控台或 `describe_index_stats`，新 index 可能延遲數秒才更新）。

   只在特定情況出現、不作為驗收條件的 log：index 尚不存在時會先印 `Creating index: carbon-assistant-sample-index`；第一次同步為 `Upserted: 60, Deleted: 0`，之後重跑同一份手冊為 `Upserted: 0, Deleted: 0`。
4. **驗收新 revision**：加 preview tag（一般流程第 4 步），同步完成後至少等 90 秒（pointer 每 30 秒檢查，加上 GCS FUSE 約 60 秒的 stat cache），登入 preview URL 問兩三題範例手冊的問題，例如「忘記密碼怎麼辦？」。新 revision 的 log 必須出現：
   ```
   [BM25Index] Loaded persisted index (60 docs) from /mnt/data/sample/models/
   ```
   回答的來源必須來自範例手冊。沒有這行 log 就不切流，先重問一次或檢查 Job log。
5. **切換流量**到新 revision（一般流程第 5 步），驗收正式 URL。快取依 index 分命名空間，不需要另外清除。
6. **確認舊 index 未變**：`carbon-assistant-qa-index` 仍為 126 筆向量（Pinecone 主控台或 `describe_index_stats`）。之後才恢復上傳。

回復：

1. 停止上傳並確認沒有執行中的同步，先把流量切回舊 revision 止血（舊程式、舊 index、舊目錄、無命名空間的快取）。
2. 以一個 release 把 `terraform/main.tf` 的 `pinecone_index_name` 改回 `carbon-assistant-qa-index`、`knowledge_base_dir` 改回 `local.shared_data_dir`；CI 完成後 Job 與新的 web revision 都使用舊 index。
3. 以 preview 驗收這個新 revision，再把流量切過去。不要長期停在舊 revision：舊 revision 的快取沒有命名空間，Job 同步後清不到。
4. 完成前不上傳手冊。

## v1.0.5（2026-10-18）

- 分支／commit：`release/v1.0.5`，`bc06ea74424b246b728725bed3718f2727002677`；[deploy run](https://github.com/albertkingdom/rag-chatbot-demo/actions/runs/39138686537) 成功。
- Image digest：`sha256:c36eb6e7dac7b39f8af0e4271c8cdf9baeef33fe17a5f8ba6aa13f77ffda362d`。
- Revision：`carbon-assistant-web-00019-pls`，100% 流量。回復目標：`carbon-assistant-web-00018-5j9`（v1.0.4）。
- 內容：
  - #12 重排分數不足時保留較佳的檢索結果；仍不足時回答標示「知識庫查無直接相關資料」，不送檢索內容、不顯示來源、不寫入語意快取。
  - #13 語意快取查詢涵蓋所有快取項目，並以 `CACHE_MAX_ENTRIES` 限制索引大小。
  - #11 開 PR 前須完成 code review（僅影響開發流程）。
- 驗收：
  - 2026-10-20 正式 URL 檢查：`/health`、`/`、`/login`、`/admin` 200，hashed JS 可載入，未登入 API 與不存在的 `/api/*` 回 JSON 401。
  - 新 revision 唯一的 ERROR log 是 2026-10-20 的 "no available instance"：`maxScale=1`、`minScale=0` 下冷啟動期間的並行請求被拒（當下 `/health` 500、`/` 429），實例就緒後重試皆 200。
  - 候選 URL 的登入驗收結果未記錄。
  - 2026-10-20 移除 preview tag `v105-preview`，正式流量維持 100% 在 `carbon-assistant-web-00019-pls`。

## v1.0.4（2026-10-11）

- 分支／commit：`release/v1.0.4`，`99ea08736d6793c49a5423335d0003f1f7ac3cb5`；[deploy run](https://github.com/albertkingdom/rag-chatbot-demo/actions/runs/38130403369) 成功。
- Image digest：`sha256:7fe086f4be900829f3c3b3825695199b7c58191e48a01e13fec374d54c77d17b`。
- Revision：`carbon-assistant-web-00018-5j9`，100% 流量。回復目標：`carbon-assistant-web-00017-g92`（v1.0.3）。
- 內容：
  - #6 同步後自動重新載入 BM25 index；電話 PII 規則不再誤判日期與一般數字；修正 RAG 評估腳本。
  - #7 ragas 與 langchain-community 0.4 相容（僅影響評估腳本）。
  - #8 規則路由加入 CarbonM 系統操作關鍵字（知識庫問題規則覆蓋率 67 → 122 / 126）。
  - #9 BM25 index 最多每 30 秒檢查一次更新。
  - #5 架構文件更新。
- 驗收：
  - Terraform plan 僅變更 web 與 sync Job 的 image。
  - 候選 URL 不需登入的檢查通過，新 revision 無 ERROR log。
  - 使用者於候選 URL 登入確認：含日期的回答正常顯示（不再被 PII 規則擋下）、「我忘記密碼該怎麼辦？」回答重設流程並附來源。
  - 切換後正式 URL `/health`、SPA、assets、未登入 API 401 正常，無 ERROR log。
- 未驗證：正式環境的手冊上傳與同步（會修改共用知識庫），以及 BM25 重新載入在 GCS 掛載上的實際延遲。
