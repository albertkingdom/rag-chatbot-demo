## Context

正式站的 web Service 與 sync Job 由 `terraform/main.tf` 設定 `PINECONE_INDEX_NAME = carbon-assistant-qa-index`，資料目錄為 GCS FUSE 掛載的 `/mnt/data/uploaded_files`（手冊）與 `/mnt/data/models`（BM25）。CI 只對 `release/**` 執行 `terraform apply`；web 的流量固定在指定 revision，新 revision 預設 0% 流量；sync Job 沒有流量概念，apply 後立即使用新設定。web 觸發同步時只以 Job 名稱執行，不覆寫 Job 的環境變數。

同步（`sync_vector_store`）以資料目錄為準 upsert 新 ID、刪除 index 中多出的 ID，再重建 BM25；BM25 失敗只記錄警告（`bm25-index` 規格），同步仍回傳成功。同步回傳 `status=error` 時，CLI 不會以非零結束，Cloud Run 會記錄成功。

語意快取在正式站的 Redis（`REDIS_URL`，web 與 Job 都有），key 為 `prompt_cache:<hash>`、索引為 `prompt_cache:index:zset`，不含 index 名稱，新舊 revision 共用。`clear_cache` 沒有呼叫者，且在 Redis 錯誤時回傳 0，與「沒有快取」無法區分。web 每 30 秒檢查 BM25 pointer；GCS FUSE 另有約 60 秒的 stat cache；BM25 缺失時檢索降級為 `vector_only`，仍可能回答並附來源。

## Goals / Non-Goals

**Goals:**

- 正式站改用範例手冊，使用與評估隔離的 index 與資料目錄。
- 切換不中斷服務、可回復，且新舊知識庫的快取答案不互相污染。
- 切流前能確認新 revision 已載入新的 BM25，而不是降級為 `vector_only`。

**Non-Goals:**

- 不刪除舊 index、舊手冊與舊 BM25 檔案。
- 不讓 web 以覆寫環境變數的方式觸發 Job（需要調整由人工管理、CI 不套用的 IAM）。
- 不改檢索、路由與評估流程。

## Decisions

### 新 index 與新資料目錄

`terraform/main.tf` 的 locals 改為 `pinecone_index_name = "carbon-assistant-sample-index"`，並新增 `knowledge_base_dir = "${local.shared_data_dir}/sample"`；web 與 Job 的 `DATA_SOURCE_DIR`、`BM25_INDEX_DIR` 改為 `${local.knowledge_base_dir}/uploaded_files` 與 `${local.knowledge_base_dir}/models`。舊目錄與舊 index 原封不動。

替代方案「沿用舊目錄、只換 index」被否決：舊目錄的原手冊會被同步進新 index。「共用 `rag-demo-qa-index`」被否決：評估腳本會刪除不在範例手冊中的向量。

### 語意快取依 index 分命名空間

`PromptCacheService.__init__` 新增 `namespace` 參數，預設為 `config.PINECONE_INDEX_NAME`。非空時資料 key 為 `prompt_cache:<namespace>:<hash>`、索引為 `prompt_cache:<namespace>:index:zset`；傳入空字串時沿用原本不含命名空間的格式（供既有測試使用）。`clear_cache` 只清除目前命名空間的索引與資料 key，以及 `LEGACY_INDEX_KEY`（更早版本的 Set 索引）。

效果：舊 revision（舊程式、無命名空間）與新 revision（`carbon-assistant-sample-index` 命名空間）的快取完全分開；preview 驗收、切流、回復時都不會讀到另一個知識庫的答案，也不需要在切換時清快取。舊格式的 key 在 24 小時 TTL 後自然過期。

### 同步完成的定義與快取清除

`sync_vector_store()` 追蹤 BM25 是否重建成功。向量同步與 BM25 都成功時，才呼叫 `PromptCacheService(get_redis_conn()).clear_cache()` 並印出清除筆數；BM25 失敗時沿用既有行為（警告、回傳成功），但不清除快取，並在訊息中註明。`clear_cache` 改為 Redis 錯誤時拋出例外；同步捕捉後印出警告，結果仍為成功。

`build_vector_store.py` 的 CLI 在結果為 `status=error` 時以結束碼 1 結束，Cloud Run Job 與介面輪詢都會顯示失敗。

### BM25 載入 log 立即輸出

`BM25Index._load` 印出 `[BM25Index] Loaded persisted index (...)` 時加上 `flush=True`，確保切流前的 log 關卡不受 stdout 緩衝影響。

### 評估腳本的拒絕清單

`PRODUCTION_INDEX` 改為 `PRODUCTION_INDEXES = ("carbon-assistant-qa-index", "carbon-assistant-sample-index")`，評估 index 屬於清單時拒絕執行。

### 切換步驟（寫入 `docs/deployment/releases.md`）

1. 確認 Pinecone 方案可再建立一個 index。
2. 從這一步到第 7 步結束都不要上傳手冊；確認沒有執行中的 Job（`gcloud run jobs executions list --job sync-job`）。
3. 建立 release 分支並 push，CI 完成後：web 新 revision 為 0% 流量；sync Job 已改用新 index 與新目錄。
4. `gcloud storage cp examples/carbonm_sample_manual.xlsx gs://<bucket>/sample/uploaded_files/`，再 `gcloud run jobs execute sync-job --wait`。Job 必須成功，且 log 有 `BM25 index rebuilt with 60 documents` 與 `Upserted: 60`。
5. 加 preview tag，等待至少 90 秒（pointer 檢查 30 秒加 FUSE stat cache 60 秒），對 preview URL 問範例手冊的問題。新 revision 的 log 必須出現 `[BM25Index] Loaded persisted index (60 docs) from /mnt/data/sample/models/`，回答的來源必須來自範例手冊；沒有這行 log 就不切流。
6. 流量切到新 revision，驗收正式 URL。
7. 確認舊 index `carbon-assistant-qa-index` 的向量數仍為 126。

回復：

1. 停止上傳並確認沒有執行中的 Job，流量先切回舊 revision 止血（舊程式、舊 index、舊目錄、無命名空間的快取）。
2. 以一個 release 把 Terraform locals 改回舊值；CI 完成後 Job 與新的 web revision 都使用舊 index 與 `carbon-assistant-qa-index` 命名空間的快取。
3. 以 preview 驗收這個新 revision 後把流量切過去，不要長期停在舊 revision：舊 revision 的快取沒有命名空間，Job 清不到。
4. 完成前不上傳手冊。

## Risks / Trade-offs

- [切換或回復期間上傳手冊，web 與 Job 會指向不同目錄] → 操作手冊在這兩段期間禁止上傳，並先確認沒有執行中的 Job；這是單一管理者的 side project，可以用流程控制。
- [新 revision 啟動時新 index 尚不存在] → web 的 Pinecone 連線在第一次查詢時才建立；驗收與切流都在同步之後。
- [BM25 未載入時檢索降級但仍能回答] → 第 5 步以 log 確認載入 60 筆後才切流。
- [Pinecone index 數量上限] → 第 1 步先確認，不足時暫停。
- [快取 key 格式改變] → 舊 key 由 TTL 自然過期，不影響正確性。
- [同一個 index 更新手冊後，web 載入新 BM25 前（最長約 90 秒）或在途請求寫入的快取仍可能來自舊資料，最長留存 24 小時] → 這是既有行為，本 change 不處理；記入 architecture.md 已知問題，日後可用資料版本作為快取命名空間的一部分解決。切換正式站不受影響，因為新 index 的命名空間從空開始。
