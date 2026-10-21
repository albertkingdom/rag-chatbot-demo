## Why

正式站仍以他方提供的手冊為知識庫（Pinecone `carbon-assistant-qa-index`，GCS 上的 `uploaded_files/` 與 `models/`），與公開 repo 的範例手冊不一致。評估腳本固定使用 `rag-demo-qa-index` 並會刪除不在範例手冊中的向量，因此正式站不能共用該 index，需要自己的 index 與資料目錄。另外，手冊同步後不清除語意快取（architecture.md 已知問題），切換知識庫後會持續回傳舊答案。

## What Changes

- 正式站改用新的 Pinecone index `carbon-assistant-sample-index`，以及 GCS 上新的資料目錄（`/mnt/data/sample/uploaded_files`、`/mnt/data/sample/models`）；web Service 與 sync Job 由 Terraform 以同一組 local 值設定。
- 舊 index 與 GCS 上的舊目錄保留不刪，供回復使用；是否刪除另行決定。
- 語意快取的 key 與索引加上 index 名稱作為命名空間，不同 index 的快取互不干擾；切換與回復時新舊 revision 各用各的快取。
- 手冊同步完成（向量與 BM25 都成功）後清除目前命名空間的快取；BM25 重建失敗時不清除；清除失敗只記錄警告。`clear_cache` 改為在 Redis 錯誤時拋出例外，不再回傳 0。
- 同步回傳 `status=error` 時，sync Job 以非零結束碼結束，讓 Cloud Run 與介面都記錄為失敗。
- 評估腳本拒絕的正式 index 清單改為新舊兩個名稱。
- 新增切換與回復的操作手冊（`docs/deployment/releases.md`），並更新 `architecture.md`。

## Non-Goals

- 不刪除舊的 Pinecone index 與 GCS 上的舊手冊、舊 BM25 檔案。
- 不改變檢索、路由、評估流程與範例手冊內容。
- 不讓 web 在觸發 Job 時覆寫 Job 的環境變數（需要調整 CI 以外管理的 IAM）。

## Capabilities

### New Capabilities

- `sync-job-outcome`: 同步完成後清除快取的條件，以及失敗時 Job 的結束碼。
- `cache-namespace`: 語意快取依 Pinecone index 名稱分隔。

### Modified Capabilities

- `sample-knowledge-base`: 正式環境的 index 名稱與資料目錄改變；評估腳本拒絕的正式 index 改為清單。
- `cache-key-stability`: 快取 key 範例改為含命名空間的格式。

## Impact

- Affected specs: `sync-job-outcome`、`cache-namespace`（新增）；`sample-knowledge-base`、`cache-key-stability`（修改）
- Affected code:
  - Modified: terraform/main.tf, src/build_vector_store.py, src/cache_service.py, tests/test_cache_service.py, scripts/eval_sample_manual.py, tests/test_document_identity.py, tests/test_eval_sample_manual.py, architecture.md, docs/deployment/releases.md
- Affected systems: Pinecone 新增一個 serverless index（需確認方案的 index 數量上限）；正式站 Cloud Run 的 web Service 與 sync Job 環境變數；GCS bucket 新增 `sample/` 目錄
